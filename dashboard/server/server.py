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

# Vault path resolution — same priority order the rest of the bundle uses:
#   1. CLAUDE_PLUGIN_OPTION_VAULT_PATH env var (set by Claude Code when plugin
#      runs inside a session; absent when dashboard runs as a daemon)
#   2. MINDFRAME_VAULT_PATH env var (operator override)
#   3. The default vault from ~/.mindframe/vaults.yaml (which itself falls
#      back to pluginConfigs.mindframe.options.vault_path in settings.json)
def _resolve_default_vault_path() -> Path | None:
    env_val = (os.environ.get("CLAUDE_PLUGIN_OPTION_VAULT_PATH")
               or os.environ.get("MINDFRAME_VAULT_PATH"))
    if env_val:
        return Path(env_val).expanduser()
    # Try vaults.yaml (handles single-vault legacy via settings.json too).
    try:
        import importlib.util as _ilu
        plugin_root = Path(__file__).resolve().parent.parent.parent
        spec = _ilu.spec_from_file_location(
            "_vy_default", plugin_root / "lib" / "vaults_yaml.py")
        if spec is None or spec.loader is None:
            return None
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        name = mod.default_vault_name()
        if not name:
            return None
        v = mod.get_vault(name)
        if not v or not v.get("path"):
            return None
        return Path(v["path"]).expanduser()
    except Exception:
        return None


VAULT_PATH = _resolve_default_vault_path()

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


def _configure_logging() -> None:
    """Surface library log messages (httpx, asyncio, frame_lib, etc.) to
    journald / launchd's StandardOutPath. The dashboard's own log() helper
    print()s with flush=True and doesn't need this, but anything that uses
    the standard logging library (fastapi internals, httpx, frame_lib) goes
    silent without it. See dispatcher's main.py for the full rationale.
    """
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
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


def _scan_vault_targets(limit_per_kind: int = 5) -> dict[str, list[str]]:
    """Pull a handful of names out of the vault to seed suggested mindframes.

    Reads file basenames (without `.md`) from the canonical KB folders. Any
    folder that doesn't exist is skipped. Falls back to an empty dict if the
    vault path isn't configured or unreachable.
    """
    targets: dict[str, list[str]] = {"services": [], "repos": [], "products": []}
    if not VAULT_PATH or not VAULT_PATH.is_dir():
        return targets
    for kind, subdir in (
        ("services", "services"),
        ("repos", "repos"),
        ("products", "products"),
    ):
        d = VAULT_PATH / subdir
        if not d.is_dir():
            continue
        entries = sorted(
            f.stem for f in d.glob("*.md") if f.is_file() and not f.name.startswith("_")
        )
        targets[kind] = entries[:limit_per_kind]
    return targets


def _build_suggestions(targets: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Return suggested mindframes the user can spawn from the home screen.

    Each suggestion is a starting prompt plus a title. We weave in vault
    entries when we have them so the suggestions feel grounded — "review PRs
    on payments-api" beats "review your team's PRs". If the vault is empty,
    we fall back to generic placeholders.
    """
    services = targets.get("services") or []
    repos = targets.get("repos") or []
    products = targets.get("products") or []

    primary_repo = repos[0] if repos else (services[0] if services else None)
    primary_product = products[0] if products else None
    primary_service = services[0] if services else None

    def s(title: str, prompt: str, tag: str) -> dict[str, Any]:
        return {"title": title, "prompt": prompt, "tag": tag}

    out: list[dict[str, Any]] = []
    out.append(s(
        title=f"Review PRs on {primary_repo}" if primary_repo else "Review your team's PRs",
        prompt=(
            f"Create an agent that reviews open pull requests on {primary_repo} "
            "every weekday morning, flags risky changes, and posts a summary."
            if primary_repo else
            "Create an agent that reviews open pull requests across our repos "
            "every weekday morning, flags risky changes, and posts a summary."
        ),
        tag="engineering",
    ))
    out.append(s(
        title=f"E2E test {primary_product} weekly" if primary_product else "Weekly E2E browser test",
        prompt=(
            f"Create an agent that does a full end-to-end browser test of {primary_product} "
            "once a week. Walk the golden path, capture screenshots, report any regressions."
            if primary_product else
            "Create an agent that does a full end-to-end browser test of our product "
            "once a week. Walk the golden path, capture screenshots, report any regressions."
        ),
        tag="product",
    ))
    out.append(s(
        title=(
            f"Investigate {primary_service} infrastructure"
            if primary_service else "Investigate our infrastructure"
        ),
        prompt=(
            f"Investigate the health and cost of {primary_service}'s infrastructure — "
            "logs, metrics, alerts, recent deploys. Surface anything that looks off."
            if primary_service else
            "Investigate the health and cost of our production infrastructure — "
            "logs, metrics, alerts, recent deploys. Surface anything that looks off."
        ),
        tag="infra",
    ))
    out.append(s(
        title="Weekly customer-feedback digest",
        prompt=(
            "Create an agent that scans support tickets, sales call notes, and "
            "Slack #feedback every Monday morning and writes a digest of what "
            "customers are asking for."
        ),
        tag="business",
    ))
    return out


@app.get("/api/suggestions")
async def api_suggestions() -> Response:
    """Suggested mindframes for the home screen.

    Pulls service / repo / product names from the configured vault (if any)
    and threads them into a small library of starter prompts. Generic fallbacks
    when the vault isn't reachable.
    """
    targets = _scan_vault_targets()
    suggestions = _build_suggestions(targets)
    return JSONResponse({
        "vault_present": bool(VAULT_PATH and VAULT_PATH.is_dir()),
        "vault_path": str(VAULT_PATH) if VAULT_PATH else None,
        "targets": targets,
        "suggestions": suggestions,
    })


class PromptBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    source: str | None = None


@app.post("/api/prompt")
async def api_prompt(body: PromptBody) -> Response:
    """Human-typed prompt from the home chatbox.

    Creates a frame shell with the user's prompt as the seed block, then fires
    a `mindframe.create` dashboard event so the dispatcher can spawn an agent
    to fulfil the prompt. Returns the new frame's id so the SPA can navigate
    to it.

    The dispatcher event is best-effort — if dispatcher is unreachable, the
    frame still exists and the user can attach an agent manually.
    """
    title = body.text.strip().split("\n", 1)[0][:120]
    try:
        result = frame_lib.create_frame(
            title=title,
            seed_block={"type": "text", "markdown": body.text},
            spawned_by={"kind": "dashboard-prompt", "source": body.source or "home"},
            tags=["user-prompt"],
        )
    except (ValueError, FileExistsError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except OSError as e:
        return JSONResponse({"error": f"filesystem error: {e}"}, status_code=500)

    bearer = _read_dispatcher_bearer()
    dispatcher_status = "skipped"
    dispatcher_error: str | None = None
    if bearer:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(
                    f"{DISPATCHER_URL}/api/event",
                    headers={"Authorization": f"Bearer {bearer}"},
                    json={
                        "event_type": "mindframe.create",
                        "source": "dashboard-prompt",
                        "data": {
                            "frame_id": result["id"],
                            "prompt": body.text,
                            "title": title,
                        },
                    },
                )
                if r.status_code < 300:
                    dispatcher_status = "queued"
                else:
                    dispatcher_status = "rejected"
                    dispatcher_error = f"status {r.status_code}: {r.text[:200]}"
        except httpx.HTTPError as e:
            dispatcher_status = "unreachable"
            dispatcher_error = str(e)

    return JSONResponse({
        "id": result["id"],
        "url": result["url"],
        "dispatcher_status": dispatcher_status,
        "dispatcher_error": dispatcher_error,
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
    """Serve files from a frame's directory.

    Resolution order (first hit wins):
      1. Legacy panes path: dashboard/artifacts/<sid>/<path>
         (kept for the old artifact pipeline + saved demo HTML)
      2. Mindframe frame path: <FRAMES_ROOT>/<sid>/<path>
         (this is what the block-stream spec calls for — custom-html
         blocks point at sibling files inside the frame directory)

    Each lookup is sandbox-checked so `..` traversal can't escape.
    """
    if not (SID_RE.match(sid) or FRAME_ID_RE.match(sid)):
        return PlainTextResponse("not found", status_code=404)

    for root in (ARTIFACTS_ROOT, FRAMES_ROOT):
        if not root.is_dir():
            continue
        base = root / sid
        try:
            base_resolved = base.resolve()
            target = (base / path).resolve()
        except (OSError, ValueError):
            continue
        # Containment check — `..` traversal is rejected here.
        if base_resolved not in target.parents and target != base_resolved:
            continue
        if target.is_file():
            return FileResponse(target, headers={"Cache-Control": "no-store, must-revalidate"})

    return PlainTextResponse("not found", status_code=404)


# --------------------------- vaults panel (v0.8.0) ---------------------------
#
# Surfaces the multi-vault catalog (~/.mindframe/vaults.yaml) to the UI:
# list vaults, browse recent entries, share via vault-sharing agent, see
# inbound GitHub invites, accept. All operations route through the existing
# vault-sharing taskpilot agent (which uses gh CLI under the hood) — the
# dashboard server is a thin proxy.

import importlib.util
import subprocess
from datetime import datetime, timezone


def _load_mindframe_lib(module_name: str):
    """Import a module from the bundle's lib/ at runtime.

    The dashboard server doesn't sit inside the mindframe Python package
    namespace, but the bundle ships ${CLAUDE_PLUGIN_ROOT}/lib/*.py. We
    locate the lib relative to this file (dashboard/server/server.py →
    plugin root → lib/<name>.py).
    """
    plugin_root = Path(__file__).resolve().parent.parent.parent
    lib_path = plugin_root / "lib" / f"{module_name}.py"
    if not lib_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        f"_mf_lib_{module_name}", lib_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _count_entries_per_type(vault_path: Path) -> dict[str, int]:
    """Count *.md files per top-level entity-type directory."""
    out: dict[str, int] = {}
    if not vault_path.is_dir():
        return out
    for child in vault_path.iterdir():
        if child.is_dir() and not child.name.startswith(("."  , "_")):
            md_count = len(list(child.glob("*.md")))
            if md_count > 0:
                out[child.name] = md_count
    return out


def _vault_last_commit(vault_path: Path) -> dict | None:
    """Latest git commit metadata, or None if not a repo."""
    if not (vault_path / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(vault_path), "log", "-1",
             "--format=%H%n%cI%n%s%n%an"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        parts = r.stdout.strip().split("\n", 3)
        if len(parts) < 4:
            return None
        return {"sha": parts[0][:8], "committed_at": parts[1],
                "subject": parts[2], "author": parts[3]}
    except (OSError, subprocess.TimeoutExpired):
        return None


def _vault_remote(vault_path: Path) -> str | None:
    if not (vault_path / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(vault_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


@app.get("/api/vaults")
def vaults_list() -> Response:
    """List all vaults from vaults.yaml, with entry counts + recent activity.

    Falls back to the legacy single-vault config (pluginConfigs.mindframe.
    options.vault_path) if vaults.yaml is missing — same behavior as the
    lib/vaults_yaml.py readers.
    """
    vyl = _load_mindframe_lib("vaults_yaml")
    if vyl is None:
        return JSONResponse({"error": "vaults_yaml lib not found"}, status_code=500)
    try:
        raw_vaults = vyl.list_vaults()
        default = vyl.default_vault_name()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"vaults.yaml read failed: {e}"}, status_code=500)
    out = []
    for v in raw_vaults:
        path = Path(v.get("path", "")).expanduser()
        out.append({
            "name": v.get("name"),
            "path": str(path),
            "exists": path.is_dir(),
            "is_default": v.get("name") == default,
            "added_via": v.get("added_via", "manual"),
            "entry_counts": _count_entries_per_type(path) if path.is_dir() else {},
            "total_entries": sum(_count_entries_per_type(path).values()) if path.is_dir() else 0,
            "remote": _vault_remote(path) if path.is_dir() else None,
            "last_commit": _vault_last_commit(path) if path.is_dir() else None,
        })
    return JSONResponse({"vaults": out, "default_vault": default})


@app.get("/api/vaults/{name}/entries")
def vault_entries(name: str, limit: int = 50) -> Response:
    """Recent entries in one vault, grouped by entity-type, ordered by mtime.

    Returns a flat list (name, type, path, modified_at, title from frontmatter
    if available) for the home view's "recent activity" feed.
    """
    vyl = _load_mindframe_lib("vaults_yaml")
    if vyl is None:
        return JSONResponse({"error": "vaults_yaml lib not found"}, status_code=500)
    v = vyl.get_vault(name)
    if not v:
        return JSONResponse({"error": f"vault '{name}' not found"}, status_code=404)
    path = Path(v.get("path", "")).expanduser()
    if not path.is_dir():
        return JSONResponse({"error": f"vault path does not exist: {path}"}, status_code=404)
    entries = []
    for child in path.iterdir():
        if not (child.is_dir() and not child.name.startswith(("." , "_"))):
            continue
        for md in child.glob("*.md"):
            try:
                st = md.stat()
            except OSError:
                continue
            # Best-effort title from frontmatter
            title = md.stem
            try:
                head = md.read_text()[:1024]
                import re
                m = re.search(r"^title:\s*(.+)$", head, re.MULTILINE)
                if m:
                    title = m.group(1).strip().strip("\"'")
            except (OSError, UnicodeDecodeError):
                pass
            entries.append({
                "name": md.stem, "type": child.name,
                "path": str(md.relative_to(path)),
                "title": title,
                "modified_at": datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc).isoformat(),
                "size_bytes": st.st_size,
            })
    entries.sort(key=lambda e: e["modified_at"], reverse=True)
    return JSONResponse({"vault": name, "entries": entries[:limit],
                         "total": len(entries)})


@app.get("/api/vaults/{name}/graph")
def vault_graph(name: str, limit: int = 500) -> Response:
    """Return a node-link graph of the vault.

    Nodes: one per .md entry. Each carries id (relative path), label
    (frontmatter title or stem), type (parent dir), mtime, slug.
    Edges: one per [[wikilink]] in entry body. Resolves wikilinks to
    existing entries by exact stem match (case-insensitive). Unresolved
    wikilinks become dangling-edge hints attached to the source node.

    Cap at `limit` nodes (default 500) so a 10k-entry vault doesn't
    explode the payload. Sampled by mtime (newest first) on overflow.
    """
    vyl = _load_mindframe_lib("vaults_yaml")
    if vyl is None:
        return JSONResponse({"error": "vaults_yaml lib not found"}, status_code=500)
    v = vyl.get_vault(name)
    if not v:
        return JSONResponse({"error": f"vault '{name}' not found"}, status_code=404)
    path = Path(v.get("path", "")).expanduser()
    if not path.is_dir():
        return JSONResponse({"error": f"vault path does not exist: {path}"},
                            status_code=404)
    return JSONResponse(build_vault_graph(path, name, limit))


def build_vault_graph(path: Path, name: str, limit: int = 500) -> dict:
    """Pure graph builder: walk a vault dir, return its node-link payload.

    Edges come from TWO sources, both resolved against existing nodes:
      1. body [[wikilinks]] in each note
      2. frontmatter foreign_keys (per the vault's schema.yaml) — this is
         where the writer stores most relationships (owner -> person, …),
         so without it the graph renders as disconnected orphan nodes.

    Kept import-pure (no FastAPI / vault lookup) so it can be unit-tested
    against a tmp vault.
    """
    # Load the vault's schema so we can build edges from frontmatter
    # foreign_keys. dir_to_type maps each entity's on-disk directory -> its
    # schema type; fk_by_type maps a type -> {field: target_type}.
    dir_to_type: dict[str, str] = {}
    fk_by_type: dict[str, dict] = {}
    try:
        import yaml as _yaml
        schema_path = path / "schema.yaml"
        if schema_path.is_file():
            _schema = _yaml.safe_load(schema_path.read_text(errors="ignore")) or {}
            for tname, tdef in (_schema.get("entities") or {}).items():
                if not isinstance(tdef, dict):
                    continue
                dir_to_type[tdef.get("directory") or tname] = tname
                fks = tdef.get("foreign_keys") or {}
                if isinstance(fks, dict):
                    fk_by_type[tname] = fks
    except Exception:
        pass  # no schema / unparseable -> fall back to body-wikilink edges only

    # Walk all .md files under top-level type dirs (skip dotfiles + .git)
    raw_nodes = []
    for type_dir in path.iterdir():
        if not (type_dir.is_dir() and not type_dir.name.startswith(("." , "_"))):
            continue
        for md in type_dir.glob("*.md"):
            try:
                st = md.stat()
                body = md.read_text(errors="ignore")
            except OSError:
                continue
            raw_nodes.append({
                "id": f"{type_dir.name}/{md.stem}",
                "stem": md.stem,
                "type": type_dir.name,
                "label": md.stem,
                "title": md.stem,
                "mtime": st.st_mtime,
                "size": st.st_size,
                "body": body,
            })

    # Parse frontmatter (best-effort) for nicer labels + FK edges
    import re
    try:
        import yaml as _yaml
    except Exception:
        _yaml = None
    fm_block_re = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    fm_title_re = re.compile(r"^title:\s*(.+)$", re.MULTILINE)
    for n in raw_nodes:
        n["fm"] = {}
        mb = fm_block_re.match(n["body"])
        if mb and _yaml is not None:
            try:
                parsed = _yaml.safe_load(mb.group(1))
                if isinstance(parsed, dict):
                    n["fm"] = parsed
            except Exception:
                n["fm"] = {}
        head = n["body"][:1024]
        m = fm_title_re.search(head)
        if m:
            n["title"] = m.group(1).strip().strip("\"'")
        elif n["fm"].get("display_name"):
            n["title"] = str(n["fm"]["display_name"])

    # Cap by mtime if over limit
    raw_nodes.sort(key=lambda n: n["mtime"], reverse=True)
    truncated = len(raw_nodes) > limit
    if truncated:
        raw_nodes = raw_nodes[:limit]

    # Build stem -> id map for wikilink resolution (case-insensitive)
    stem_index: dict[str, str] = {}
    for n in raw_nodes:
        # Both bare stem and full-path forms commonly appear in wikilinks
        stem_index[n["stem"].lower()] = n["id"]
        stem_index[n["id"].lower()] = n["id"]

    # Wikilink scan: [[target]] or [[target|display]] or [[path/target|display]]
    wikilink_re = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
    edges = []
    dangling_per_node: dict[str, list[str]] = {}
    for n in raw_nodes:
        targets_seen = set()
        for m in wikilink_re.finditer(n["body"]):
            target_raw = m.group(1).strip()
            if not target_raw:
                continue
            # Try the exact form, then strip leading subdirs, then bare stem
            candidates = [target_raw, target_raw.split("/")[-1]]
            resolved = None
            for c in candidates:
                k = c.lower()
                if k in stem_index:
                    resolved = stem_index[k]
                    break
            if resolved and resolved != n["id"] and resolved not in targets_seen:
                edges.append({"source": n["id"], "target": resolved})
                targets_seen.add(resolved)
            elif not resolved:
                dangling_per_node.setdefault(n["id"], []).append(target_raw)

    # FK edges from frontmatter foreign_keys. The writer stores most
    # relationships here (owner -> person, service -> service, …) rather than
    # as body wikilinks, so these are the bulk of a vault's real structure.
    edge_keys = {(e["source"], e["target"]) for e in edges}
    for n in raw_nodes:
        etype = dir_to_type.get(n["type"])
        if not etype:
            continue
        fm = n.get("fm") or {}
        for field in (fk_by_type.get(etype) or {}):
            val = fm.get(field)
            if val is None:
                continue
            for v in (val if isinstance(val, list) else [val]):
                vs = str(v).strip() if v is not None else ""
                if not vs or vs == "~":
                    continue
                resolved = None
                for c in (vs, vs.split("/")[-1]):
                    if c.lower() in stem_index:
                        resolved = stem_index[c.lower()]
                        break
                if not resolved or resolved == n["id"]:
                    continue
                key = (n["id"], resolved)
                if key in edge_keys:
                    continue
                edge_keys.add(key)
                edges.append({"source": n["id"], "target": resolved, "kind": "fk", "field": field})

    # Build payload — strip body before sending (kept in raw_nodes only for parse)
    nodes = []
    for n in raw_nodes:
        dangling = dangling_per_node.get(n["id"], [])
        nodes.append({
            "id": n["id"], "label": n["title"], "type": n["type"],
            "mtime": int(n["mtime"]),
            "dangling_links": dangling[:5],  # cap per-node for payload size
            "dangling_count": len(dangling),
        })

    # Count distinct types for legend
    type_counts: dict[str, int] = {}
    for n in nodes:
        type_counts[n["type"]] = type_counts.get(n["type"], 0) + 1

    return {
        "vault": name,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "truncated": truncated,
        "types": sorted(type_counts.items(), key=lambda x: -x[1]),
        "nodes": nodes,
        "edges": edges,
    }


class VaultShareBody(BaseModel):
    recipient: str = Field(..., min_length=3, max_length=128)
    permission: str = Field("push", pattern=r"^(pull|push|admin)$")
    owner: str | None = None


@app.post("/api/vaults/{name}/share")
async def vault_share(name: str, body: VaultShareBody) -> Response:
    """Fire a share job at the vault-sharing agent. Fire-and-forget; the
    UI polls /api/vaults/{name} or watches the agent's outgoing.json for
    completion status.
    """
    vyl = _load_mindframe_lib("vaults_yaml")
    if vyl is None or not vyl.vault_exists(name):
        return JSONResponse({"error": f"vault '{name}' not found"}, status_code=404)

    # Resolve gh user as default owner
    owner = body.owner
    if not owner:
        try:
            r = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                                capture_output=True, text=True, timeout=5)
            owner = r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            owner = None
    if not owner:
        return JSONResponse({
            "error": "no GitHub owner — run `gh auth login` or pass `owner`",
        }, status_code=400)

    # Drop the job file in the agent's queue + message via session-bridge
    queue = Path.home() / ".mindframe" / "vault-sharing" / "queue"
    responses = Path.home() / ".mindframe" / "vault-sharing" / "responses"
    queue.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    response_path = responses / f"share-{job_id}.json"
    vault = vyl.get_vault(name)
    job = {
        "job_id": job_id, "kind": "share",
        "vault_name": name, "vault_path": vault.get("path"),
        "recipient": body.recipient, "permission": body.permission,
        "github_owner": owner, "response_path": str(response_path),
    }
    job_path = queue / f"{job_id}.json"
    job_path.write_text(json.dumps(job, indent=2))

    bridge = "http://127.0.0.1:8910"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{bridge}/sessions/vault-sharing/message",
                json={"text": f"vault-sharing job: {job_path}"},
            )
            if r.status_code >= 400:
                return JSONResponse({
                    "error": f"agent dispatch failed: {r.status_code} {r.text[:200]}",
                    "hint": "is vault-sharing agent spawned via taskpilot?",
                }, status_code=502)
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"session-bridge unreachable: {e}"},
                             status_code=502)

    return JSONResponse({
        "ok": True, "job_id": job_id,
        "vault": name, "recipient": body.recipient,
        "repo": f"{owner}/vault-{name}",
        "response_path": str(response_path),
        "status": "queued",
    })


@app.get("/api/github/owners")
def github_owners() -> Response:
    """List places the operator can create a repo under: their personal
    account + every org they're a member of. Drives the 'where should
    this vault live?' dropdown in the share dialog.
    """
    try:
        me = subprocess.run(
            ["gh", "api", "user", "--jq", "{login,name}"],
            capture_output=True, text=True, timeout=5,
        )
        orgs = subprocess.run(
            ["gh", "api", "user/orgs", "--jq", ".[] | {login,description}"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return JSONResponse({"error": "gh CLI not installed"}, status_code=500)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "gh CLI timed out"}, status_code=504)

    if me.returncode != 0:
        return JSONResponse({
            "error": "gh CLI not authenticated — run `gh auth login`",
            "stderr": me.stderr[:300],
        }, status_code=401)

    try:
        user = json.loads(me.stdout)
    except json.JSONDecodeError:
        user = {"login": None}

    org_list = []
    if orgs.returncode == 0:
        # gh returns one JSON object per line
        for line in orgs.stdout.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    org_list.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    owners = []
    if user.get("login"):
        owners.append({
            "login": user["login"],
            "kind": "personal",
            "label": f"{user['login']} (your personal account)",
        })
    for o in org_list:
        owners.append({
            "login": o["login"],
            "kind": "org",
            "label": f"{o['login']} (org)" + (f" — {o['description']}" if o.get("description") else ""),
        })
    return JSONResponse({
        "owners": owners,
        "default": user.get("login"),
    })


# --------------------------- data sources panel ---------------------------
#
# Mirror of the vaults panel. Surfaces every event-source the operator could
# connect — what's wired up, what's pending creds, when each last pulled
# successfully. Reads from:
#
#   - ~/.mindframe/credentials/<source>.json         — connected accounts
#   - ~/.dispatcher/channels.yaml                    — static routes
#   - dispatcher /sources/status (when running)      — live sync state
#
# The known-source catalog is intentionally hardcoded for now: it's the
# user-facing menu of "things you can connect," not a live discovery of
# what dispatcher adapters happen to be loaded. Adding a new source means
# adding a row here AND building an adapter — the registry is for humans.

KNOWN_SOURCES = [
    {
        "id": "github",
        "name": "GitHub",
        "icon": "github",
        "description": "Repos, issues, PRs, releases, webhooks.",
        "credential_kinds": ["gh-cli", "pat"],
    },
    {
        "id": "google-drive",
        "name": "Google Drive",
        "icon": "drive",
        "description": "Docs, Sheets, Slides, meeting recordings + transcripts (via Meet).",
        "credential_kinds": ["oauth"],
    },
    {
        "id": "google-calendar",
        "name": "Google Calendar",
        "icon": "calendar",
        "description": "Meetings, attendees, recurrence.",
        "credential_kinds": ["oauth"],
    },
    {
        "id": "slack",
        "name": "Slack",
        "icon": "slack",
        "description": "Channel messages, threads, reactions, files.",
        "credential_kinds": ["bot-token", "oauth"],
    },
    {
        "id": "confluence",
        "name": "Confluence",
        "icon": "confluence",
        "description": "Pages, spaces, comments — Atlassian wiki.",
        "credential_kinds": ["api-token"],
    },
    {
        "id": "sentry",
        "name": "Sentry",
        "icon": "sentry",
        "description": "Errors, performance, releases — incident triage.",
        "credential_kinds": ["auth-token"],
    },
    {
        "id": "pagerduty",
        "name": "PagerDuty",
        "icon": "pagerduty",
        "description": "Incidents, on-call rotations, schedules.",
        "credential_kinds": ["api-key"],
    },
    {
        "id": "gmail",
        "name": "Gmail",
        "icon": "gmail",
        "description": "Inbox, labels, filters — email triage signal.",
        "credential_kinds": ["oauth"],
    },
]


def _credentials_dir() -> Path:
    return Path.home() / ".mindframe" / "credentials"


def _source_status(source_id: str) -> dict:
    """Per-source connection + sync status. Cheap, side-effect-free."""
    creds_dir = _credentials_dir()
    cred_path = creds_dir / f"{source_id}.json"
    connected = cred_path.exists()
    status: dict = {
        "connected": connected,
        "credential_path": str(cred_path) if connected else None,
    }
    if connected:
        try:
            stat = cred_path.stat()
            status["credential_mtime"] = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat()
            # Peek for an account label without leaking the token.
            try:
                blob = json.loads(cred_path.read_text())
                for key in ("account", "user", "email", "login", "workspace"):
                    if key in blob and isinstance(blob[key], str):
                        status["account"] = blob[key]
                        break
                if "scope" in blob and isinstance(blob["scope"], str):
                    # Drive OAuth stores space-separated scopes
                    status["scopes"] = blob["scope"].split()
            except (json.JSONDecodeError, OSError):
                pass
        except OSError:
            pass
    # TODO: when dispatcher exposes /sources/status, fold last_sync + lag
    # in here. For now we only know "do creds exist."
    return status


@app.get("/api/sources")
def list_sources() -> Response:
    """Catalog of data sources mindframe can ingest from + per-source connection state."""
    creds_dir = _credentials_dir()
    creds_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    for src in KNOWN_SOURCES:
        sources.append({**src, **_source_status(src["id"])})
    connected_count = sum(1 for s in sources if s["connected"])
    return JSONResponse({
        "sources": sources,
        "connected": connected_count,
        "total": len(sources),
    })


@app.post("/api/sources/{source_id}/connect")
def connect_source(source_id: str) -> Response:
    """Stub: kick off the per-source connection flow. For now, return the slash
    command the operator should run. Future: spawn an OAuth-driving agent the
    way share/accept do — browser-bridge handles consent screens, then writes
    the token blob to ~/.mindframe/credentials/<source>.json.
    """
    known = next((s for s in KNOWN_SOURCES if s["id"] == source_id), None)
    if not known:
        return JSONResponse({"error": f"unknown source: {source_id}"}, status_code=404)
    # The connect-source agent isn't built yet. Be honest: hand the operator
    # the credential file path + format and let them paste a token in
    # manually. When the agent ships, this returns spawn:add-source instead.
    cred_path = _credentials_dir() / f"{source_id}.json"
    examples = {
        "github": '{"token": "ghp_..."}  // or run `gh auth login` (mindframe will detect it)',
        "google-drive": '{"access_token": "...", "refresh_token": "...", "client_id": "...", "client_secret": "..."}',
        "google-calendar": '{"access_token": "...", "refresh_token": "...", "client_id": "...", "client_secret": "..."}',
        "slack": '{"bot_token": "xoxb-...", "workspace": "your-workspace"}',
        "confluence": '{"base_url": "https://your.atlassian.net", "email": "you@team.com", "api_token": "..."}',
        "sentry": '{"auth_token": "...", "org": "your-org-slug"}',
        "pagerduty": '{"api_key": "...", "user_email": "you@team.com"}',
        "gmail": '{"access_token": "...", "refresh_token": "...", "client_id": "...", "client_secret": "..."}',
    }
    example = examples.get(source_id, '{}')
    return JSONResponse({
        "status": "manual",
        "source": known,
        "credential_path": str(cred_path),
        "example_blob": example,
        "instructions": (
            f"The agent-driven OAuth/credential flow for {known['name']} hasn't shipped yet. "
            f"For now, create the file {cred_path} with this shape:\n\n{example}\n\n"
            "Then click refresh."
        ),
    })


@app.post("/api/sources/{source_id}/disconnect")
def disconnect_source(source_id: str) -> Response:
    """Remove the stored credentials for a source. Doesn't revoke remote-side
    grants — the operator is told to do that in the source system's UI if they
    care about it (most won't)."""
    known = next((s for s in KNOWN_SOURCES if s["id"] == source_id), None)
    if not known:
        return JSONResponse({"error": f"unknown source: {source_id}"}, status_code=404)
    cred_path = _credentials_dir() / f"{source_id}.json"
    if cred_path.exists():
        try:
            cred_path.unlink()
        except OSError as e:
            return JSONResponse({"error": f"failed to delete credentials: {e}"}, status_code=500)
        return JSONResponse({"status": "disconnected", "source_id": source_id})
    return JSONResponse({"status": "not-connected", "source_id": source_id})


# --------------------------- connections (live discovery) ---------------------------
#
# Replaces the hardcoded KNOWN_SOURCES catalog with REAL discovery of what this
# machine can reach: MCPs Claude is connected to (`claude mcp list`) + authed
# CLIs (gh/gcloud/aws/az), minus mindframe's own runtime MCPs. See
# docs/onboarding-ux.md. Cached briefly so the probes don't run every poll.

# mindframe's own runtime — the installer brought these; never user-facing.
_BUNDLE_RUNTIME = {
    "daemon-manager", "claude-browser-bridge", "softwaresoftware",
    "tmux-session", "taskpilot", "session-bridge", "tokenboard",
    "mindframe", "email-triage", "desktop-channel",
}
_CONN_DISPLAY = {
    "gmail-organizer": "Gmail", "google-calendar": "Google Calendar",
    "slack": "Slack", "finance": "Finance", "stripe": "Stripe",
}
_conn_cache: dict[str, Any] = {"at": 0.0, "data": None}
_CONN_TTL_S = 30.0


def _conn_run(cmd: list[str], timeout: float = 20.0):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def _discover_connections() -> dict:
    conns: list[dict] = []

    # MCPs Claude is connected to.
    r = _conn_run(["claude", "mcp", "list"], timeout=45)
    for line in (r.stdout if r else "").splitlines():
        line = line.strip()
        if ": " not in line or " - " not in line:
            continue
        name_part, rest = line.split(": ", 1)
        status = rest.rsplit(" - ", 1)[-1].strip()
        base = name_part.split(":")[-1] if name_part.startswith("plugin:") else name_part
        if base in _BUNDLE_RUNTIME:
            continue
        state = ("connected" if "Connected" in status
                 else "needs-auth" if "auth" in status.lower() else "unknown")
        conns.append({"id": base, "kind": "mcp", "state": state,
                      "name": _CONN_DISPLAY.get(base, base.replace("-", " ").title())})

    # Authed CLIs (inherited identity).
    def add_cli(cmd, name, idd, check, acct=None):
        if not shutil.which(cmd):
            return
        rr = _conn_run(check)
        ok = bool(rr) and rr.returncode == 0
        c = {"id": idd, "name": name, "kind": "cli",
             "state": "connected" if ok else "needs-auth"}
        if ok and acct:
            a = _conn_run(acct)
            if a and a.returncode == 0 and a.stdout.strip():
                c["account"] = a.stdout.strip().splitlines()[0]
        conns.append(c)

    add_cli("gh", "GitHub", "github",
            ["gh", "auth", "status"], ["gh", "api", "user", "-q", ".login"])
    add_cli("gcloud", "GCP", "gcp",
            ["bash", "-c", "gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q ."],
            ["bash", "-c", "gcloud auth list --filter=status:ACTIVE --format='value(account)'"])
    add_cli("aws", "AWS", "aws", ["aws", "sts", "get-caller-identity"])
    add_cli("az", "Azure", "azure", ["az", "account", "show"])

    # connected first, CLIs before MCPs within a state, then by name
    conns.sort(key=lambda c: (c["state"] != "connected", c["kind"] != "cli", c["name"]))
    return {"connections": conns,
            "reachable": sum(1 for c in conns if c["state"] == "connected")}


@app.get("/api/connections")
def list_connections() -> Response:
    """Live-discovered connections (MCPs + authed CLIs), minus mindframe's own
    runtime. Cached for _CONN_TTL_S so the auth probes stay cheap. This is the
    real replacement for the hardcoded /api/sources catalog."""
    now = time.time()
    if _conn_cache["data"] is None or (now - _conn_cache["at"]) > _CONN_TTL_S:
        _conn_cache["data"] = _discover_connections()
        _conn_cache["at"] = now
    return JSONResponse(_conn_cache["data"])


@app.get("/api/shares/incoming")
def shares_incoming() -> Response:
    """List pending GitHub repository invitations (potential vaults to accept)."""
    try:
        r = subprocess.run(
            ["gh", "api", "/user/repository_invitations"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return JSONResponse({"error": "gh CLI not installed"}, status_code=500)
    if r.returncode != 0:
        return JSONResponse({
            "error": "gh api failed",
            "stderr": r.stderr[:300],
        }, status_code=502)
    try:
        invites = json.loads(r.stdout)
    except json.JSONDecodeError:
        invites = []
    out = []
    for inv in invites:
        repo = inv.get("repository", {}).get("full_name", "")
        # Heuristic: looks like a mindframe vault if name starts with vault-
        is_vault_shaped = repo.split("/")[-1].startswith("vault-")
        out.append({
            "id": inv.get("id"),
            "repo": repo,
            "inviter": inv.get("inviter", {}).get("login"),
            "permissions": inv.get("permissions"),
            "created_at": inv.get("created_at"),
            "html_url": inv.get("html_url"),
            "looks_like_vault": is_vault_shaped,
        })
    return JSONResponse({"invitations": out})


class AcceptBody(BaseModel):
    invitation_id: int
    vault_name: str | None = None
    vaults_root: str | None = None


@app.post("/api/shares/accept")
async def shares_accept(body: AcceptBody) -> Response:
    """Tell vault-sharing agent to accept a GitHub invite, clone, register."""
    # Fetch the invitation to derive a default vault name + repo
    try:
        r = subprocess.run(
            ["gh", "api", "/user/repository_invitations"],
            capture_output=True, text=True, timeout=10,
        )
        invites = json.loads(r.stdout) if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        invites = []
    match = next((i for i in invites if i.get("id") == body.invitation_id), None)
    if not match:
        return JSONResponse({
            "error": f"invitation {body.invitation_id} not in pending list. "
                     "If already accepted on GitHub, accept via CLI: "
                     "vault_sharing/accept.py --invitation <id> --repo <owner/name> --vault <name>",
        }, status_code=404)

    repo_full = match["repository"]["full_name"]
    vault_name = body.vault_name or repo_full.split("/")[-1].replace("vault-", "", 1)
    vaults_root = Path(body.vaults_root or str(
        Path.home() / "mindframe-vaults")).expanduser()
    vaults_root.mkdir(parents=True, exist_ok=True)

    queue = Path.home() / ".mindframe" / "vault-sharing" / "queue"
    responses = Path.home() / ".mindframe" / "vault-sharing" / "responses"
    queue.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    response_path = responses / f"accept-{job_id}.json"
    job = {
        "job_id": job_id, "kind": "accept",
        "invitation_id": body.invitation_id,
        "repo_full_name": repo_full,
        "vault_name": vault_name,
        "vaults_root": str(vaults_root),
        "response_path": str(response_path),
    }
    job_path = queue / f"{job_id}.json"
    job_path.write_text(json.dumps(job, indent=2))

    bridge = "http://127.0.0.1:8910"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{bridge}/sessions/vault-sharing/message",
                json={"text": f"vault-sharing job: {job_path}"},
            )
            if r.status_code >= 400:
                return JSONResponse({
                    "error": f"agent dispatch failed: {r.status_code} {r.text[:200]}",
                }, status_code=502)
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"session-bridge unreachable: {e}"},
                             status_code=502)

    return JSONResponse({
        "ok": True, "job_id": job_id, "vault_name": vault_name,
        "repo": repo_full, "response_path": str(response_path),
        "status": "queued",
    })


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
