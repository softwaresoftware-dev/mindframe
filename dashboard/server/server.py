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

import sys

import httpx
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

# --------------------------- config ---------------------------

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
ARTIFACTS_ROOT = ROOT / "artifacts"
SHARES_ROOT = ROOT / "shares"
WEB_ROOT = ROOT / "public"
FRAMES_ROOT = Path(os.environ.get("MINDFRAME_FRAMES_ROOT", str(Path.home() / ".mindframe" / "frames")))
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TAIL_POLL_S = float(os.environ.get("MINDFRAME_TAIL_POLL_S", "0.25"))

# lib.frame lives one level above the dashboard, in the plugin root.
sys.path.insert(0, str(ROOT.parent))
from lib import frame as frame_lib  # noqa: E402

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


# --------------------------- block-stream API ---------------------------


def _frame_dir(mid: str) -> Path | None:
    if not FRAME_ID_RE.match(mid):
        return None
    d = FRAMES_ROOT / mid
    if not d.is_dir():
        return None
    return d


def _read_meta(fdir: Path) -> dict[str, Any]:
    meta_path = fdir / "meta.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _read_blocks(fdir: Path, since_id: str | None = None) -> tuple[list[dict[str, Any]], int]:
    """Read all blocks from blocks.jsonl, optionally filtering to those after
    `since_id` (UUIDv7 string comparison works because of chronological sort).

    Returns (blocks, file_size_bytes_read). The byte count lets tail loops
    cheaply detect whether the file has grown since the last read.
    """
    bpath = fdir / "blocks.jsonl"
    if not bpath.is_file():
        return [], 0
    try:
        raw = bpath.read_bytes()
    except OSError:
        return [], 0
    blocks: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            b = json.loads(line)
        except ValueError:
            continue
        if since_id and isinstance(b.get("id"), str) and b["id"] <= since_id:
            continue
        blocks.append(b)
    # Server-side application of supersedes / redact happens at the renderer
    # for now. Spec calls for server-side resolution; we'll iterate.
    return blocks, len(raw)


@app.get("/api/frames")
async def api_frames() -> dict[str, Any]:
    """List mindframes from ~/.mindframe/frames/, newest-activity first."""
    out: list[dict[str, Any]] = []
    if not FRAMES_ROOT.is_dir():
        return {"frames": []}
    try:
        entries = list(FRAMES_ROOT.iterdir())
    except OSError:
        return {"frames": []}
    for fdir in entries:
        if not fdir.is_dir() or not FRAME_ID_RE.match(fdir.name):
            continue
        meta = _read_meta(fdir)
        bpath = fdir / "blocks.jsonl"
        block_count = 0
        last_block_at = meta.get("last_block_at") or meta.get("created_at") or 0
        if bpath.is_file():
            try:
                with open(bpath, "rb") as fh:
                    block_count = sum(1 for line in fh if line.strip())
                last_block_at = max(last_block_at, int(bpath.stat().st_mtime * 1000))
            except OSError:
                pass
        out.append({
            "id": fdir.name,
            "title": meta.get("title") or fdir.name,
            "status": meta.get("status") or "active",
            "block_count": block_count,
            "last_block_at": last_block_at,
            "tags": meta.get("tags") or [],
        })
    out.sort(key=lambda f: f["last_block_at"], reverse=True)
    return {"frames": out}


@app.get("/api/frame/{mid}")
async def api_frame_meta(mid: str) -> Response:
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "frame not found"}, status_code=404)
    return JSONResponse(_read_meta(fdir))


@app.get("/api/frame/{mid}/blocks")
async def api_frame_blocks(mid: str, since: str | None = None) -> Response:
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "frame not found"}, status_code=404)
    blocks, _ = _read_blocks(fdir, since_id=since)
    last_id = blocks[-1]["id"] if blocks else None
    return JSONResponse({"frame_id": mid, "blocks": blocks, "last_block_id": last_id})


class CreateFrameBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    seed_block: dict | None = None
    spawned_by: dict | None = None
    tags: list[str] | None = None


@app.post("/api/frames")
async def api_frames_create(body: CreateFrameBody) -> Response:
    """Manual mindframe creation — used by the home '+ new mindframe' button
    and any external caller that wants to spawn a frame without going through
    the dispatcher. Calls lib.frame.create_frame directly (in-process).

    Note: this only creates the frame *shell*. To actually launch an agent
    against it, the caller (or a subsequent step) needs to spawn taskpilot
    with name=<id>, cwd=<frame_dir>. The shell alone is useful for testing
    the renderer and for manual block-stream authoring."""
    try:
        result = frame_lib.create_frame(
            title=body.title,
            seed_block=body.seed_block,
            spawned_by=body.spawned_by or {"kind": "manual"},
            tags=body.tags,
        )
    except (ValueError, FileExistsError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except OSError as e:
        return JSONResponse({"error": f"filesystem error: {e}"}, status_code=500)
    return JSONResponse({
        "id": result["id"],
        "url": result["url"],
        "frame_dir": result["frame_dir"],
    })


@app.get("/api/frame/{mid}/stream")
async def api_frame_stream(
    mid: str,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> Response:
    """SSE stream of blocks. Replays from Last-Event-ID (if given), then tails.

    Each event is `id: <uuid7>\\ndata: <json>\\n\\n`. The browser's EventSource
    auto-reconnects with Last-Event-ID set, so resumption is free.
    """
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "frame not found"}, status_code=404)

    async def gen():
        # Tell the client our preferred reconnect delay (ms).
        yield "retry: 2000\n\n"

        seen_id = last_event_id
        # Initial replay — everything after Last-Event-ID (or all of it).
        blocks, _ = _read_blocks(fdir, since_id=seen_id)
        for b in blocks:
            yield _sse_event(b)
            seen_id = b["id"]

        # Tail loop — poll file mtime, re-read if it grew.
        bpath = fdir / "blocks.jsonl"
        last_size = bpath.stat().st_size if bpath.is_file() else 0
        last_mtime = bpath.stat().st_mtime if bpath.is_file() else 0
        keepalive_counter = 0
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(TAIL_POLL_S)
            try:
                st = bpath.stat()
            except OSError:
                continue
            if st.st_size != last_size or st.st_mtime != last_mtime:
                new_blocks, size = _read_blocks(fdir, since_id=seen_id)
                for b in new_blocks:
                    yield _sse_event(b)
                    seen_id = b["id"]
                last_size = size
                last_mtime = st.st_mtime
                keepalive_counter = 0
            else:
                keepalive_counter += 1
                # Send a comment line every ~15s to keep proxies from closing.
                if keepalive_counter * TAIL_POLL_S >= 15:
                    yield ": keepalive\n\n"
                    keepalive_counter = 0

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
            "Connection": "keep-alive",
        },
    )


def _sse_event(block: dict[str, Any]) -> str:
    """Format one block as one SSE event. id field doubles as Last-Event-ID."""
    bid = block.get("id", "")
    payload = json.dumps(block, ensure_ascii=False, separators=(",", ":"))
    return f"id: {bid}\ndata: {payload}\n\n"


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
    headers = {"Cache-Control": "no-store, must-revalidate"}
    if full_path:
        candidate = (web / full_path).resolve()
        if (web in candidate.parents) and candidate.is_file():
            return FileResponse(candidate, headers=headers)
    index = web / "index.html"
    if index.is_file():
        return FileResponse(index, headers=headers)
    return JSONResponse({"error": "not found"}, status_code=404)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
