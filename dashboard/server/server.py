"""Mindframe dashboard backend — static shell + artifact viewer.

The persistent "dashboard agent" was removed (2026-05-21). The next iteration
of this dashboard is a merge with the taskboard plugin: taskboard supplies the
static topology frame; mindframe contributes ephemeral panes spawned by
dispatcher events, written to artifacts/<sid>/ and served by this backend.

What this server still does:

  - Serve the SPA in public/ (no build step).
  - Serve artifact HTML written by per-event task agents under artifacts/<sid>/.
  - Snapshot the current artifact to a sharable /s/<id> URL with 60-day retention.
  - Hourly sweep of expired shares.

What it no longer does:

  - Drive a persistent taskpilot dashboard agent.
  - Compose HTML in response to an instruction-box (no /api/run).
  - Talk to the taskpilot or session-bridge daemons.

FastAPI + uvicorn. No background workers, no daemons required.
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

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

# --------------------------- config ---------------------------

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
ARTIFACTS_ROOT = ROOT / "artifacts"
SHARES_ROOT = ROOT / "shares"
WEB_ROOT = ROOT / "public"

PORT = int(os.environ.get("PORT", "5174"))

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
    return {"ok": True, "port": PORT}


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
