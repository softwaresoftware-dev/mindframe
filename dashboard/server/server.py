"""Mindframe dashboard backend — surface mindframes + system overview.

A mindframe is a *surface*: a persistent agent that owns one index.html it
rewrites in place (see surface/ and docs/onboarding-ux.md). The dashboard mints
them, lists them, serves their pages, and proxies operator messages to their
agents via the taskpilot daemon. It also surfaces the single knowledge-base
vault, live connection discovery, and a read-only system overview.

Action buttons inside agent-authored HTML POST to /api/dashboard-event, which
proxies to the dispatcher with a bearer the server reads from disk.

Endpoints:

  - GET  /api/health                       — liveness + dispatcher bearer presence
  - GET  /api/frames                       — list surface mindframes
  - POST /api/frames/create                — mint a frame + spawn its agent
  - GET  /m/<id>                           — the per-mindframe surface shell
  - GET  /api/frame/<id>/page|rev          — the agent's page + its revision
  - POST /api/frame/<id>/message           — deliver a message to the agent
  - GET  /api/frame/<id>/activity          — tail the agent's cognition log
  - POST /api/dashboard-event              — proxy to dispatcher (server holds the bearer)
  - GET  /api/vault[/entries|/graph]       — the single knowledge-base vault
  - GET  /api/sources, /api/connections    — known-source catalog + live discovery
  - GET  /api/events|/agents|/capabilities — read-only system overview
  - GET  /artifacts/<id>/<path>            — serve a frame's sibling files
  - GET  /<path>                           — SPA fallback

FastAPI + uvicorn + httpx. The dispatcher and taskpilot daemons are optional —
the dashboard runs without them; only the endpoints that talk to each
(dashboard-event, frame message) fail when its daemon is unreachable.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import sys

import httpx
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

# --------------------------- config ---------------------------

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
ARTIFACTS_ROOT = ROOT / "artifacts"
WEB_ROOT = ROOT / "public"
FRAMES_ROOT = Path(os.environ.get("MINDFRAME_FRAMES_ROOT", str(Path.home() / ".mindframe" / "frames")))
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TAIL_POLL_S = float(os.environ.get("MINDFRAME_TAIL_POLL_S", "0.25"))

# The single, static knowledge-base vault. Not configurable — keeper and the
# skills hardcode the same path.
VAULT_DIR = Path.home() / ".mindframe" / "vault"


# Plugin libs (lib/*.py) live one level above the dashboard.
sys.path.insert(0, str(ROOT.parent))

PORT = int(os.environ.get("PORT", "5174"))

DISPATCHER_URL = os.environ.get("MINDFRAME_DISPATCHER_URL", "http://127.0.0.1:8911")
DISPATCHER_BEARER_FILE = Path(
    os.environ.get("MINDFRAME_DISPATCHER_BEARER_FILE", str(Path.home() / ".mindframe/secrets/dispatcher-bearer.token"))
)
# Agent-runtime daemon (taskpilot) — message delivery + spawn. Same endpoint the
# surface substrate uses (MF_DAEMON). Mindframe agents idle until messaged.
TASKPILOT_DAEMON = os.environ.get("MINDFRAME_TASKPILOT_DAEMON", "http://127.0.0.1:8912")
TASKPILOT_HOME = Path(os.environ.get("MINDFRAME_TASKPILOT_HOME", str(Path.home() / ".taskpilot")))

# The SPA is served by this server and calls the API with relative paths, so it
# is always same-origin — CORS is unnecessary by default. Set
# MINDFRAME_CORS_ORIGINS (comma-separated) only if a separate-origin frontend
# ever needs cross-origin access; the middleware is mounted only then.
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("MINDFRAME_CORS_ORIGINS", "").split(",")
    if o.strip()
]

SID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def log(msg: str) -> None:
    print(f"[mindframe-dashboard] {msg}", flush=True)


# --------------------------- app ---------------------------


def _configure_logging() -> None:
    """Surface library log messages (httpx, asyncio, etc.) to
    journald / launchd's StandardOutPath. The dashboard's own log() helper
    print()s with flush=True and doesn't need this, but anything that uses
    the standard logging library (fastapi internals, httpx) goes
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
    log(f"server on http://127.0.0.1:{PORT}")
    log(f"artifacts: {ARTIFACTS_ROOT}")
    yield


app = FastAPI(lifespan=lifespan)

if CORS_ORIGINS:
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


# --------------------------- mindframe surface listing ---------------------------
#
# Block-stream removed (2026-06-04): a mindframe is now a *surface* — the agent
# owns one index.html it rewrites in place (see surface/ and
# docs/onboarding-ux.md). This endpoint lists surface mindframes: frame dirs
# under FRAMES_ROOT that hold an index.html. Proper per-mindframe viewing and
# prompt->surface creation are rebuilt in a later migration step; for now each
# frame's page is reachable via /artifacts/<id>/index.html.


def _read_meta(fdir: Path) -> dict[str, Any]:
    meta_path = fdir / "meta.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


@app.get("/api/frames")
async def api_frames() -> dict[str, Any]:
    """List surface mindframes — frame dirs under FRAMES_ROOT holding an
    index.html — newest-activity first."""
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
        index = fdir / "index.html"
        if not index.is_file():
            continue
        meta = _read_meta(fdir)
        try:
            modified = int(index.stat().st_mtime * 1000)
        except OSError:
            modified = 0
        out.append({
            "id": fdir.name,
            "title": meta.get("title") or fdir.name,
            "status": meta.get("status") or "active",
            "modified": modified,
            "tags": meta.get("tags") or [],
        })
    out.sort(key=lambda f: f["modified"], reverse=True)
    return {"frames": out}


# --------------------------- mindframe creation ---------------------------
#
# Create = mint a frame dir + spawn a persistent agent whose cwd is that dir and
# whose one job is to own index.html. The agent is spawned through taskpilot's
# spawner CLI (the agent-spawning provider), located via the installed-plugins
# manifest. The agent writes its page with the plain Write tool — no MCP.

MINDFRAME_BRIEF = """You are a mindframe — an autonomous agent that works for the operator by \
composing a single live web page. You own exactly ONE file:

    {index}

THE LOOP
  1. The operator sends you a message (their first request is below).
  2. You do the real work it implies — run Bash, read files, query the MCPs and \
CLIs available to you. Never fabricate; if you can't reach something, say so on the page.
  3. You use the Write tool to rewrite the ENTIRE file above as one complete, \
valid, self-contained HTML document that reflects the new state.
  4. You stop and wait for the next message. The operator's browser reloads \
automatically when the file changes.

RULES
  - ALWAYS write the COMPLETE document — never a fragment, never an append. Inline \
all CSS. The page is the whole interface; there is no chat transcript, so render \
what matters now, not a log.
  - Make it calm and legible: type, weight, colour, and spacing carry meaning. No emoji.
  - NEVER declare yourself done. End every page with a forward question or a clear \
next step so the conversation keeps going.
  - Anything irreversible or outward-facing: draw the pending action on the page \
and wait for the operator to approve it in a message before doing it.

THE OPERATOR'S FIRST REQUEST
{prompt}

Compose your first index.html now: acknowledge the request, show what you \
understand and your first concrete step, and end with a question."""


def _mint_frame_id(n: int = 10) -> str:
    return "".join(secrets.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(n))


def _find_spawner_cli() -> Path | None:
    """Locate taskpilot's spawner_cli.py via the installed-plugins manifest."""
    manifest = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(manifest.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    for key, installs in (data.get("plugins") or {}).items():
        if key.split("@")[0] == "taskpilot" and installs:
            p = Path(installs[0].get("installPath", "")) / "spawner_cli.py"
            if p.is_file():
                return p
    return None


class CreateMindframe(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    title: str | None = None


@app.post("/api/frames/create")
def create_mindframe(body: CreateMindframe) -> Response:
    """Create a surface mindframe: mint a frame dir, drop a placeholder page,
    then spawn a persistent agent (cwd = frame dir) that owns index.html.
    Returns the new id + url so the SPA can open /m/<id> immediately."""
    spawner_cli = _find_spawner_cli()
    if spawner_cli is None:
        return JSONResponse(
            {"error": "taskpilot (agent-spawning) not found — can't spawn a mindframe agent."},
            status_code=503)

    for _ in range(5):
        mid = _mint_frame_id()
        fdir = FRAMES_ROOT / mid
        if not fdir.exists():
            break
    else:
        return JSONResponse({"error": "could not mint a unique frame id"}, status_code=500)
    try:
        fdir.mkdir(parents=True, mode=0o755)
    except OSError as e:
        return JSONResponse({"error": f"filesystem error: {e}"}, status_code=500)

    index = fdir / "index.html"
    title = (body.title or body.prompt.strip().split("\n", 1)[0])[:120]
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;")
    index.write_text(
        "<!doctype html><meta charset=utf-8><title>composing…</title>"
        "<body style='margin:0;height:100vh;display:grid;place-items:center;"
        "font:16px system-ui;color:#888;background:#0d0d0f'>"
        f"<div style='text-align:center'>Composing this mindframe…<br>"
        f"<small style='color:#555'>{safe_title}</small></div>",
        "utf-8",
    )
    (fdir / "meta.json").write_text(json.dumps({
        "id": mid, "title": title, "task_id": mid, "status": "active",
        "prompt": body.prompt, "spawned_by": {"kind": "dashboard"},
    }, indent=2), "utf-8")

    brief = MINDFRAME_BRIEF.format(index=str(index), prompt=body.prompt.strip())
    try:
        proc = subprocess.run(
            ["python3", str(spawner_cli), brief, "--name", mid, "--cwd", str(fdir)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return JSONResponse({"id": mid, "url": f"/m/{mid}",
                             "spawn": "error", "error": f"spawn failed: {e}"})
    spawn_result: dict = {}
    try:
        spawn_result = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        spawn_result = {"ok": False,
                        "error": (proc.stderr or proc.stdout or "no spawner output")[:400]}
    return JSONResponse({
        "id": mid, "url": f"/m/{mid}",
        "spawn": "ok" if spawn_result.get("ok") else "error",
        "spawn_result": spawn_result,
    })


# --------------------------- mindframe surface (viewing) ---------------------------
#
# Multi-tenant fold-in of surface/server.py: one dashboard serves every
# mindframe. /m/<id> renders the shell; the agent owns <framedir>/index.html and
# rewrites it in place; the shell polls /api/frame/<id>/rev and reloads. User
# messages reach the agent through the taskpilot daemon (which wakes a dormant
# agent on contact); /activity tails the agent's transcript for the cognition log.


def _frame_dir(mid: str) -> Path | None:
    if not FRAME_ID_RE.match(mid):
        return None
    d = FRAMES_ROOT / mid
    return d if d.is_dir() else None


def _frame_task_id(mid: str, fdir: Path) -> str:
    """The taskpilot task that owns this frame — meta.json `task_id`, else the
    frame id itself (creation names the task after the frame)."""
    return _read_meta(fdir).get("task_id") or mid


@app.get("/m/{mid}")
def surface_shell(mid: str) -> Response:
    """The mindframe surface shell — iframe over the agent's page + message rail
    + cognition log. The page's JS derives the id from the URL."""
    if _frame_dir(mid) is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    shell = WEB_ROOT / "surface.html"
    if not shell.is_file():
        return JSONResponse({"error": "surface shell missing"}, status_code=500)
    return FileResponse(shell, headers={"Cache-Control": "no-store"})


@app.get("/api/frame/{mid}/page")
def frame_page(mid: str) -> Response:
    """Serve the agent's index.html (or a 'composing…' placeholder)."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    index = fdir / "index.html"
    headers = {"Cache-Control": "no-store"}
    if index.is_file():
        return FileResponse(index, media_type="text/html", headers=headers)
    return Response(
        "<!doctype html><meta charset=utf-8>"
        "<body style='margin:0;height:100vh;display:grid;place-items:center;"
        "font:16px system-ui;color:#777;background:#0d0d0f'>"
        "<div>Composing this mindframe&hellip;</div></body>",
        media_type="text/html", headers=headers,
    )


@app.get("/api/frame/{mid}/rev")
def frame_rev(mid: str) -> Response:
    """Revision = the surface file's mtime_ns. Bumps when the agent rewrites."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    index = fdir / "index.html"
    rev = index.stat().st_mtime_ns if index.is_file() else 0
    return JSONResponse({"rev": rev})


class FrameMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@app.post("/api/frame/{mid}/message")
async def frame_message(mid: str, body: FrameMessage) -> Response:
    """Deliver a user message to the mindframe's agent via the taskpilot daemon."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    task_id = _frame_task_id(mid, fdir)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{TASKPILOT_DAEMON}/tasks/{task_id}/message",
                json={"text": body.text, "from_session": "mindframe-surface"},
            )
        if r.status_code >= 300:
            return JSONResponse(
                {"ok": False, "error": f"daemon {r.status_code}: {r.text[:200]}"},
                status_code=502)
    except httpx.HTTPError as e:
        return JSONResponse({"ok": False, "error": f"taskpilot daemon unreachable: {e}"},
                            status_code=502)
    return JSONResponse({"ok": True})


# --- cognition log: tail the agent's Claude transcript (ported from surface/) ---

def _pretty_model(name: str) -> str:
    name = name.replace("claude-", "")
    name = re.sub(r"-\d{8}$", "", name)
    return re.sub(r"(\d)-(\d)", r"\1.\2", name)


def _agent_transcript(fdir: Path, task_id: str) -> Path | None:
    """Newest Claude session JSONL for this mindframe's agent. Handles both spawn
    styles: a normal taskpilot spawn runs with the real $HOME and cwd = the frame
    dir, so Claude stores the transcript at ~/.claude/projects/<encoded-cwd>/
    (each '/' and '.' becomes '-'); an isolated spawn (e.g. setup) keeps it under
    ~/.taskpilot/<task_id>/.claude/projects/<proj>/."""
    files: list[Path] = []
    enc = re.sub(r"[/.]", "-", str(fdir.resolve()))
    real_proj = Path.home() / ".claude" / "projects" / enc
    if real_proj.is_dir():
        files.extend(real_proj.glob("*.jsonl"))
    iso_proj = TASKPILOT_HOME / task_id / ".claude" / "projects"
    if iso_proj.is_dir():
        files.extend(iso_proj.glob("*/*.jsonl"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _parse_cognition(line: str) -> list[dict[str, str]]:
    """One transcript line -> 0+ compact cognition events (thinking/tool/text)."""
    try:
        e = json.loads(line)
    except ValueError:
        return []
    if e.get("isSidechain"):
        return []
    msg = e.get("message") or {}
    if (msg.get("role") or e.get("type")) == "user":
        return []
    content = msg.get("content")
    if isinstance(content, str):
        s = content.strip().replace("\n", " ")
        return [{"kind": "text", "label": s[:160]}] if s else []
    if not isinstance(content, list):
        return []
    out: list[dict[str, str]] = []
    for b in content:
        t = b.get("type")
        if t == "text" and b.get("text", "").strip():
            out.append({"kind": "text", "label": b["text"].strip().replace("\n", " ")[:160]})
        elif t == "thinking":
            out.append({"kind": "thinking", "label": "thinking…"})
        elif t == "tool_use":
            inp = b.get("input") or {}
            hint = (inp.get("command") or inp.get("file_path") or inp.get("description")
                    or inp.get("path") or inp.get("url") or "")
            label = b.get("name") or "tool"
            if hint:
                label += ": " + str(hint)[:100]
            out.append({"kind": "tool", "label": label})
    return out


def _latest_turn_meta(tp: Path) -> dict[str, Any]:
    """Most recent assistant turn's model + live context size (window fullness)."""
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 131072))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return {}
    for ln in reversed(tail.split("\n")):
        if not ln.strip() or '"usage"' not in ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        m = e.get("message") or {}
        if (m.get("role") or e.get("type")) != "assistant":
            continue
        u = m.get("usage") or {}
        if not u:
            continue
        ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
               + u.get("cache_creation_input_tokens", 0))
        return {"model": _pretty_model(m.get("model") or ""), "context": ctx}
    return {}


@app.get("/api/frame/{mid}/activity")
def frame_activity(mid: str, offset: int = 0, file: str = "") -> Response:
    """Tail the mindframe agent's transcript and return cognition events since
    `offset`; also reports `mtime` + live `model`/`context`."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    tp = _agent_transcript(fdir, _frame_task_id(mid, fdir))
    if tp is None:
        return JSONResponse({"events": [], "offset": 0, "file": "", "mtime": 0})
    fid = tp.name
    if fid != file:
        offset = 0
    events: list = []
    new_offset = offset
    try:
        mtime = tp.stat().st_mtime
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if offset > size:
                offset = 0
            f.seek(offset)
            chunk = f.read()
        text = chunk.decode("utf-8", "replace")
        if "\n" in text:
            complete, _, remainder = text.rpartition("\n")
            new_offset = offset + (len(chunk) - len(remainder.encode("utf-8")))
            for ln in complete.split("\n"):
                if ln.strip():
                    events.extend(_parse_cognition(ln))
    except OSError:
        return JSONResponse({"events": [], "offset": offset, "file": fid, "mtime": 0})
    out: dict[str, Any] = {"events": events, "offset": new_offset, "file": fid, "mtime": mtime}
    out.update(_latest_turn_meta(tp))
    return JSONResponse(out)


# --------------------------- dispatcher proxy ---------------------------


class DashboardEvent(BaseModel):
    """Action-button payload from agent-authored HTML.

    The agent embeds `<button onclick="postEvent({...})">` in its page; the
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


@app.get("/artifacts/{sid}/{path:path}")
def serve_artifact(sid: str, path: str) -> Response:
    """Serve sibling files referenced by a mindframe's page (images, data,
    sub-pages an agent writes next to its index.html).

    Resolution order (first hit wins):
      1. dashboard/artifacts/<sid>/<path>
      2. <FRAMES_ROOT>/<sid>/<path>

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


# --------------------------- vault panel ---------------------------
#
# Surfaces the single knowledge-base vault to the UI: its metadata, recent
# entries, and node-link graph. The path is fixed at VAULT_DIR
# (~/.mindframe/vault). There is no multi-vault catalog and no sharing —
# one deployment, one vault.

import subprocess
from datetime import datetime, timezone


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


def _resolve_vault_or_error() -> tuple[Path, str, Response | None]:
    """The vault path + name. Returns (path, name, error_response).

    The path is static (~/.mindframe/vault), so error_response is non-None only
    when the vault dir doesn't exist yet (fresh install, pre-setup) — handlers
    should return it directly.
    """
    path = VAULT_DIR
    name = path.name
    if not path.is_dir():
        return path, name, JSONResponse(
            {"error": f"vault not created yet at {path} — run /mindframe:setup"},
            status_code=404)
    return path, name, None


@app.get("/api/vault")
def vault_info() -> Response:
    """The single knowledge-base vault: path, entry counts, recent activity.

    The path is fixed at ~/.mindframe/vault, so this always returns 200 — the
    `exists` flag is false until /mindframe:setup creates it.
    """
    path = VAULT_DIR
    name = path.name
    exists = path.is_dir()
    counts = _count_entries_per_type(path) if exists else {}
    return JSONResponse({
        "name": name,
        "path": str(path),
        "exists": exists,
        "entry_counts": counts,
        "total_entries": sum(counts.values()),
        "remote": _vault_remote(path) if exists else None,
        "last_commit": _vault_last_commit(path) if exists else None,
    })


@app.get("/api/vault/entries")
def vault_entries(limit: int = 50) -> Response:
    """Recent entries in the vault, grouped by entity-type, ordered by mtime.

    Returns a flat list (name, type, path, modified_at, title from frontmatter
    if available) for the home view's "recent activity" feed.
    """
    path, name, err = _resolve_vault_or_error()
    if err is not None:
        return err
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


@app.get("/api/vault/graph")
def vault_graph(limit: int = 500) -> Response:
    """Return a node-link graph of the vault.

    Nodes: one per .md entry. Each carries id (relative path), label
    (frontmatter title or stem), type (parent dir), mtime, slug.
    Edges: one per [[wikilink]] in entry body. Resolves wikilinks to
    existing entries by exact stem match (case-insensitive). Unresolved
    wikilinks become dangling-edge hints attached to the source node.

    Cap at `limit` nodes (default 500) so a 10k-entry vault doesn't
    explode the payload. Sampled by mtime (newest first) on overflow.
    """
    path, name, err = _resolve_vault_or_error()
    if err is not None:
        return err
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


def _parse_mcp_list() -> list[dict[str, Any]]:
    """Run `claude mcp list` and normalize each line to {id, name, state, bundle}.

    Shared by /api/connections (live discovery) and /api/capabilities. Lines look
    like `name: <url-or-cmd> - <status>`, where status carries 'Connected' or an
    auth hint; a `plugin:<pkg>:<name>` prefix is reduced to its base name.
    """
    r = _conn_run(["claude", "mcp", "list"], timeout=45)
    out: list[dict[str, Any]] = []
    for line in (r.stdout if r else "").splitlines():
        line = line.strip()
        if ": " not in line or " - " not in line:
            continue
        name_part, rest = line.split(": ", 1)
        status = rest.rsplit(" - ", 1)[-1].strip()
        base = name_part.split(":")[-1] if name_part.startswith("plugin:") else name_part
        state = ("connected" if "Connected" in status
                 else "needs-auth" if "auth" in status.lower() else "unknown")
        out.append({
            "id": base,
            "name": _CONN_DISPLAY.get(base, base.replace("-", " ").title()),
            "state": state,
            "bundle": base in _BUNDLE_RUNTIME,
        })
    return out


def _discover_connections() -> dict:
    conns: list[dict] = []

    # MCPs Claude is connected to (excluding the bundle's own runtime).
    for m in _parse_mcp_list():
        if m["bundle"]:
            continue
        conns.append({"id": m["id"], "kind": "mcp",
                      "state": m["state"], "name": m["name"]})

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


# --------------------------- system overview (read-only) ---------------------------
#
# Three endpoints that, together with the existing /api/frames, /api/vault and
# /api/connections, back the structured "System" view. Each maps one bucket of
# the bundle's mental model to its real on-disk source of truth:
#
#   /api/events       — dispatcher routes      (~/.dispatcher/channels.yaml)
#   /api/agents       — recipes + taskpilot db (~/.dispatcher/recipes, ~/.taskpilot)
#   /api/capabilities — MCPs + plugin skills   (claude mcp list, installed_plugins.json)
#
# All read-only, all defensive: a missing dispatcher / taskpilot / claude CLI
# degrades to an empty list with a `present: false` flag, never a 500.

DISPATCHER_HOME = Path(os.environ.get("MINDFRAME_DISPATCHER_HOME", str(Path.home() / ".dispatcher")))
TASKPILOT_DB = Path(os.environ.get("MINDFRAME_TASKPILOT_DB", str(Path.home() / ".taskpilot" / "taskpilot.db")))

_sys_cache: dict[str, dict[str, Any]] = {}
_SYS_TTL_S = 20.0


def _load_yaml(path: Path) -> Any:
    """Parse a YAML file, tolerating a missing PyYAML or unreadable file."""
    try:
        import yaml  # transitive dep of the bundle (schema.yaml, channels.yaml)
    except ImportError:
        return None
    try:
        return yaml.safe_load(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _split_target(target: str) -> dict[str, str]:
    """`spawn:meeting-prep` -> {kind: spawn, name: meeting-prep}."""
    if isinstance(target, str) and ":" in target:
        kind, name = target.split(":", 1)
        return {"kind": kind, "name": name}
    return {"kind": "session", "name": str(target)}


@app.get("/api/events")
def list_event_sources() -> Response:
    """Dispatcher event-source routes, grouped by source. Each route says which
    (source, event_type) pair fires which target (spawn:<recipe> | session:<name>)."""
    chan = DISPATCHER_HOME / "channels.yaml"
    if not chan.is_file():
        return JSONResponse({"sources": [], "route_count": 0, "dispatcher_present": False})
    data = _load_yaml(chan) or {}
    routes = data.get("routes") or []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in routes:
        if not isinstance(r, dict):
            continue
        src = str(r.get("source", "unknown"))
        tgt = _split_target(r.get("target", ""))
        grouped.setdefault(src, []).append({
            "event_type": str(r.get("event_type", "*")),
            "target_kind": tgt["kind"],
            "target_name": tgt["name"],
            "brief_keys": sorted((r.get("brief") or {}).keys()),
        })
    sources = [{"source": s, "routes": rs} for s, rs in sorted(grouped.items())]
    return JSONResponse({
        "sources": sources,
        "route_count": sum(len(rs) for rs in grouped.values()),
        "dispatcher_present": True,
    })


def _tmux_sessions() -> set[str]:
    r = _conn_run(["tmux", "ls", "-F", "#{session_name}"], timeout=5)
    if not r or r.returncode != 0:
        return set()
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


def _agent_definitions() -> list[dict[str, Any]]:
    """Recipes under ~/.dispatcher/recipes/<name>/recipe.yaml — the spawn
    templates an event route can target. These are what CAN run."""
    recipes_dir = DISPATCHER_HOME / "recipes"
    out: list[dict[str, Any]] = []
    if not recipes_dir.is_dir():
        return out
    # Map recipe name -> the (source, event_type) routes that trigger it.
    triggers: dict[str, list[str]] = {}
    chan = _load_yaml(DISPATCHER_HOME / "channels.yaml") or {}
    for r in (chan.get("routes") or []):
        if isinstance(r, dict):
            t = _split_target(r.get("target", ""))
            if t["kind"] == "spawn":
                triggers.setdefault(t["name"], []).append(
                    f"{r.get('source', '?')}/{r.get('event_type', '*')}")
    for d in sorted(recipes_dir.iterdir()):
        rec = d / "recipe.yaml"
        if not d.is_dir() or not rec.is_file():
            continue
        meta = _load_yaml(rec) or {}
        out.append({
            "id": d.name,
            "name": meta.get("task_name") or d.name,
            "kind": meta.get("kind") or "task",
            "model": meta.get("model"),
            "when_to_use": meta.get("when_to_use") or [],
            "triggered_by": triggers.get(d.name, []),
        })
    return out


def _live_agents(limit: int = 40) -> list[dict[str, Any]]:
    """Taskpilot tasks — what IS (or recently was) running. Read straight from
    taskpilot.db, newest first, cross-referenced with live tmux sessions."""
    if not TASKPILOT_DB.is_file():
        return []
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{TASKPILOT_DB}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return []
    try:
        cols = ("task_id", "name", "description", "status", "kind", "model",
                "cwd", "updated_at")
        rows = con.execute(
            f"SELECT {', '.join(cols)} FROM tasks "
            "ORDER BY updated_at DESC LIMIT 400"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)

    def _recent(ts: str | None) -> bool:
        if not ts:
            return False
        try:
            dt = datetime.fromisoformat(ts.replace(" ", "T"))
        except ValueError:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff

    sessions = _tmux_sessions()
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(zip(cols, row))
        rec["live"] = rec["task_id"] in sessions
        # "Live tasks" = what is running right now or freshly queued:
        #   - a live tmux session (ground truth for "running"), always kept; or
        #   - a non-terminal db status (running/pending) updated in the last 3d.
        # Terminal runs (killed/crashed/completed) and ancient pending zombies
        # are history, not live — drop them.
        if not (rec["live"] or
                (rec["status"] in ("running", "pending") and _recent(rec["updated_at"]))):
            continue
        desc = rec.get("description") or ""
        rec["description"] = desc[:140] + ("…" if len(desc) > 140 else "")
        out.append(rec)
    # Live first, then by recency (already DESC), capped.
    out.sort(key=lambda a: not a["live"])
    return out[:limit]


@app.get("/api/agents")
def list_agents() -> Response:
    """Agents in two groups: definitions (recipes = what can run) and live
    (taskpilot tasks = what is running)."""
    now = time.time()
    c = _sys_cache.get("agents")
    if not c or (now - c["at"]) > _SYS_TTL_S:
        defs = _agent_definitions()
        live = _live_agents()
        c = {"at": now, "data": {
            "definitions": defs,
            "live": live,
            "definition_count": len(defs),
            "live_count": len(live),
            "running_count": sum(1 for a in live if a["live"]),
        }}
        _sys_cache["agents"] = c
    return JSONResponse(c["data"])


def _list_mcps() -> list[dict[str, Any]]:
    """All MCPs from `claude mcp list`, flagged bundle-runtime vs external."""
    out = _parse_mcp_list()
    out.sort(key=lambda m: (m["state"] != "connected", m["bundle"], m["name"]))
    return out


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def _list_skills() -> list[dict[str, Any]]:
    """Skills shipped by installed plugins, grouped by plugin."""
    manifest = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(manifest.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for plugin_key, installs in (data.get("plugins") or {}).items():
        if not installs:
            continue
        install_path = installs[0].get("installPath")
        if not install_path:
            continue
        skills_dir = Path(install_path) / "skills"
        if not skills_dir.is_dir():
            continue
        skills: list[dict[str, str]] = []
        for sk in sorted(skills_dir.glob("*/SKILL.md")):
            try:
                fm = _parse_frontmatter(sk.read_text("utf-8")[:600])
            except OSError:
                continue
            desc = fm.get("description", "")
            skills.append({
                "name": fm.get("name") or sk.parent.name,
                "description": desc[:120] + ("…" if len(desc) > 120 else ""),
            })
        if skills:
            out.append({
                "plugin": plugin_key.split("@")[0],
                "version": installs[0].get("version", ""),
                "skills": skills,
            })
    out.sort(key=lambda p: p["plugin"])
    return out


@app.get("/api/capabilities")
def list_capabilities() -> Response:
    """Skills + MCPs the bundle can act through — the capability surface."""
    now = time.time()
    c = _sys_cache.get("caps")
    if not c or (now - c["at"]) > _SYS_TTL_S:
        mcps = _list_mcps()
        skills = _list_skills()
        c = {"at": now, "data": {
            "mcps": mcps,
            "skills": skills,
            "mcp_count": len(mcps),
            "skill_count": sum(len(p["skills"]) for p in skills),
        }}
        _sys_cache["caps"] = c
    return JSONResponse(c["data"])


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
