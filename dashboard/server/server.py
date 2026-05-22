"""Mindframe dashboard backend — panes feed.

The persistent "dashboard agent" was removed (2026-05-21). Replaced with a
push-driven model: dispatcher events spawn per-task agents that write HTML
into artifacts/<sid>/latest.html; the SPA polls /api/panes and materializes
each one as an ephemeral pane on a single canvas. Action buttons inside the
agent-authored HTML POST back to /api/dashboard-event, which proxies to the
dispatcher with a bearer the server reads from disk (the browser never sees
the bearer value).

The next iteration is a merge with the taskboard plugin: taskboard supplies
the static topology frame; the panes lane built here gets folded into that
chassis.

Endpoints:

  - GET  /api/health
  - GET  /api/panes              — list current artifacts with mtimes
  - POST /api/dashboard-event    — proxy to dispatcher (server holds the bearer)
  - GET  /artifacts/<sid>/<path> — serve agent-written HTML
  - POST /api/save               — snapshot current artifact to /s/<id>
  - GET  /s/<share_id>           — serve a saved share
  - GET  /api/share/<share_id>   — share metadata
  - GET  /<path>                 — SPA fallback

FastAPI + uvicorn + httpx. The dispatcher daemon is optional — the dashboard
itself runs fine without it; only /api/dashboard-event fails if dispatcher
is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

# --------------------------- config ---------------------------

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
ARTIFACTS_ROOT = ROOT / "artifacts"
SHARES_ROOT = ROOT / "shares"
WEB_ROOT = ROOT / "public"

PORT = int(os.environ.get("PORT", "5174"))

DISPATCHER_URL = os.environ.get("MINDFRAME_DISPATCHER_URL", "http://127.0.0.1:8911")
DISPATCHER_BEARER_FILE = Path(
    os.environ.get("MINDFRAME_DISPATCHER_BEARER_FILE", str(Path.home() / ".mindframe/secrets/dispatcher-bearer.token"))
)

SHARE_RETENTION_DAYS = int(os.environ.get("MINDFRAME_SHARE_RETENTION_DAYS", "60"))
SHARE_RETENTION_MS = SHARE_RETENTION_DAYS * 24 * 60 * 60 * 1000
SHARE_ID_LEN = 10
SHARE_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "MINDFRAME_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if o.strip()
]

SID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SHARE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")


def log(msg: str) -> None:
    print(f"[mindframe-dashboard] {msg}", flush=True)


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------- shares ---------------------------


def generate_share_id() -> str:
    for _ in range(8):
        out = "".join(SHARE_ID_ALPHABET[b % len(SHARE_ID_ALPHABET)] for b in secrets.token_bytes(SHARE_ID_LEN))
        if not (SHARES_ROOT / out).exists():
            return out
    return f"{int(time.time() * 1000):x}{secrets.token_hex(3)}"


def sweep_expired_shares() -> dict[str, int]:
    kept = 0
    pruned = 0
    now = now_ms()
    try:
        entries = list(SHARES_ROOT.iterdir())
    except OSError:
        return {"kept": kept, "pruned": pruned}
    for dir_path in entries:
        if not dir_path.is_dir():
            continue
        created_at = int(dir_path.stat().st_mtime * 1000)
        meta_path = dir_path / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
                if isinstance(meta.get("createdAt"), (int, float)):
                    created_at = int(meta["createdAt"])
            except (OSError, ValueError):
                pass
        if now - created_at > SHARE_RETENTION_MS:
            try:
                shutil.rmtree(dir_path, ignore_errors=True)
                pruned += 1
            except OSError:
                pass
        else:
            kept += 1
    return {"kept": kept, "pruned": pruned}


# --------------------------- artifacts ---------------------------


def sid_dir(sid: str) -> Path:
    if not SID_RE.match(sid):
        sid = str(uuid.uuid4())
    d = ARTIFACTS_ROOT / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_path(sid: str) -> Path:
    return sid_dir(sid) / "latest.html"


# --------------------------- app ---------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
    SHARES_ROOT.mkdir(parents=True, exist_ok=True)
    log(f"server on http://127.0.0.1:{PORT}")
    log(f"artifacts: {ARTIFACTS_ROOT}")
    log(f"shares: {SHARES_ROOT} ({SHARE_RETENTION_DAYS}-day retention)")
    swept = sweep_expired_shares()
    log(f"startup share sweep: kept {swept['kept']} pruned {swept['pruned']}")

    sweep_task = asyncio.create_task(_hourly_sweep())
    try:
        yield
    finally:
        sweep_task.cancel()


async def _hourly_sweep() -> None:
    while True:
        try:
            await asyncio.sleep(60 * 60)
        except asyncio.CancelledError:
            return
        pruned = sweep_expired_shares()["pruned"]
        if pruned > 0:
            log(f"hourly sweep pruned {pruned} expired share(s)")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)


@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    return {
        "ok": True,
        "port": PORT,
        "dispatcher_url": DISPATCHER_URL,
        "dispatcher_bearer_present": DISPATCHER_BEARER_FILE.is_file(),
    }


# --------------------------- panes feed ---------------------------


def _list_panes() -> list[dict[str, Any]]:
    """Enumerate artifacts/<sid>/latest.html with mtime + size, newest first.

    A pane is identified by its sid (the artifacts subdirectory name). Only
    sids matching SID_RE are returned — anything else is treated as foreign.
    """
    out: list[dict[str, Any]] = []
    try:
        entries = list(ARTIFACTS_ROOT.iterdir())
    except OSError:
        return out
    for sid_dir_path in entries:
        if not sid_dir_path.is_dir():
            continue
        sid = sid_dir_path.name
        if not SID_RE.match(sid):
            continue
        latest = sid_dir_path / "latest.html"
        if not latest.is_file():
            continue
        try:
            st = latest.stat()
        except OSError:
            continue
        out.append({
            "sid": sid,
            "url": f"/artifacts/{sid}/latest.html",
            "mtime_ms": int(st.st_mtime * 1000),
            "bytes": st.st_size,
        })
    out.sort(key=lambda p: p["mtime_ms"], reverse=True)
    return out


@app.get("/api/panes")
async def api_panes() -> dict[str, Any]:
    return {"panes": _list_panes()}


# --------------------------- dispatcher proxy ---------------------------


class DashboardEvent(BaseModel):
    """Action-button payload from agent-authored HTML.

    The agent embeds `<button onclick="postEvent({...})">` in its pane; the
    SPA wraps that helper and POSTs here. We forward to the dispatcher's
    /api/event with our held bearer; the browser never sees the token.

    `event_type` and `data` pass through verbatim. `source` is forced to
    `dashboard-button` so dispatcher routing can distinguish UI-originated
    events from external webhooks.
    """
    event_type: str = Field(..., min_length=1, max_length=128)
    data: dict | list | str | int | float | bool | None = None


def _read_dispatcher_bearer() -> str | None:
    try:
        return DISPATCHER_BEARER_FILE.read_text("utf-8").strip() or None
    except OSError:
        return None


@app.post("/api/dashboard-event")
async def api_dashboard_event(body: DashboardEvent) -> Response:
    bearer = _read_dispatcher_bearer()
    if not bearer:
        return JSONResponse(
            {"error": f"dispatcher bearer not found at {DISPATCHER_BEARER_FILE}"},
            status_code=503,
        )
    payload = {
        "source": "dashboard-button",
        "event_type": body.event_type,
        "data": body.data,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{DISPATCHER_URL}/api/event",
                json=payload,
                headers={"Authorization": f"Bearer {bearer}"},
            )
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"dispatcher unreachable: {e}"}, status_code=502)
    if not r.is_success:
        return JSONResponse(
            {"error": f"dispatcher rejected event (status {r.status_code})", "body": r.text[:500]},
            status_code=r.status_code,
        )
    try:
        return JSONResponse(r.json())
    except ValueError:
        return JSONResponse({"ok": True, "raw": r.text[:500]})


@app.get("/s/{share_id}")
def serve_share(share_id: str) -> Response:
    if not SHARE_ID_RE.match(share_id):
        return PlainTextResponse("invalid share id", status_code=400)
    dir_path = SHARES_ROOT / share_id
    file = dir_path / "index.html"
    if not file.exists():
        return PlainTextResponse("share not found", status_code=404)
    meta_path = dir_path / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            created = meta.get("createdAt")
            if isinstance(created, (int, float)) and now_ms() - created > SHARE_RETENTION_MS:
                return PlainTextResponse("this share has expired", status_code=410)
        except (OSError, ValueError):
            pass
    return FileResponse(
        file,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/share/{share_id}")
def api_share(share_id: str) -> Response:
    if not SHARE_ID_RE.match(share_id):
        return JSONResponse({"error": "invalid share id"}, status_code=400)
    meta_path = SHARES_ROOT / share_id / "meta.json"
    if not meta_path.exists():
        return JSONResponse({"error": "share not found"}, status_code=404)
    try:
        return JSONResponse(json.loads(meta_path.read_text("utf-8")))
    except (OSError, ValueError):
        return JSONResponse({"error": "meta unreadable"}, status_code=500)


class SaveBody(BaseModel):
    sid: str = ""
    label: str = ""


@app.post("/api/save")
def api_save(body: SaveBody) -> Response:
    """Snapshot the current artifact to a sharable URL."""
    sid = body.sid.strip()
    label = body.label[:200] if isinstance(body.label, str) else ""
    if not sid:
        return JSONResponse({"error": "sid required"}, status_code=400)
    src = artifact_path(sid)
    if not src.exists():
        return JSONResponse({"error": "no artifact to save"}, status_code=404)
    html = src.read_text("utf-8")
    share_id = generate_share_id()
    dir_path = SHARES_ROOT / share_id
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "index.html").write_text(html, "utf-8")
    created_at = now_ms()
    meta = {
        "id": share_id,
        "sid": sid,
        "label": label,
        "createdAt": created_at,
        "expiresAt": created_at + SHARE_RETENTION_MS,
        "retentionDays": SHARE_RETENTION_DAYS,
        "bytes": len(html),
    }
    (dir_path / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
    return JSONResponse({"url": f"/s/{share_id}", **meta})


@app.get("/artifacts/{sid}/{path:path}")
def serve_artifact(sid: str, path: str) -> Response:
    if not SID_RE.match(sid):
        return PlainTextResponse("not found", status_code=404)
    base = ARTIFACTS_ROOT / sid
    target = (base / path).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        return PlainTextResponse("not found", status_code=404)
    if not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(target, headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/{full_path:path}")
def serve_spa(full_path: str) -> Response:
    if full_path.startswith("api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    web = WEB_ROOT.resolve()
    if full_path:
        candidate = (web / full_path).resolve()
        if (web in candidate.parents) and candidate.is_file():
            return FileResponse(candidate)
    index = web / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse({"error": "not found"}, status_code=404)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
