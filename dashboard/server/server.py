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
  - POST /api/frame/<id>/message           — deliver a message to the agent (revives a dead one first)
  - GET  /api/frame/<id>/activity          — tail the agent's cognition log
  - POST /api/dashboard-event              — proxy to dispatcher (server holds the bearer)
  - GET  /api/vault[/entries|/graph]       — the single knowledge-base vault
  - GET  /api/connections                  — live connection discovery
  - GET  /api/events, /api/agents          — read-only system endpoints
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
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, Request
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

# The single, static knowledge-base vault. Not configurable — the dashboard and
# the skills hardcode the same path. Mindframe owns this vault directly.
VAULT_DIR = Path.home() / ".mindframe" / "vault"


PORT = int(os.environ.get("PORT", "5174"))

DISPATCHER_URL = os.environ.get("MINDFRAME_DISPATCHER_URL", "http://127.0.0.1:8911")
DISPATCHER_BEARER_FILE = Path(
    os.environ.get("MINDFRAME_DISPATCHER_BEARER_FILE", str(Path.home() / ".mindframe/secrets/dispatcher-bearer.token"))
)
# Agent-runtime daemon (taskpilot) — message delivery + spawn. Mindframe agents
# idle until messaged; /api/frame/<id>/message wakes one through this daemon.
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


def _warm_connections_loop() -> None:
    """Keep the connections cache warm in the background. `claude mcp list` alone
    takes ~7s, so a cold /api/connections blocks the home page's count for that
    long. Refreshing just under the TTL means the endpoint is virtually always a
    cache hit (instant); the slow probe runs here, off the request path."""
    while True:
        try:
            data = _discover_connections()
            with _conn_lock:
                _conn_cache["data"] = data
                _conn_cache["at"] = time.time()
        except Exception as e:  # never let the warmer die
            log(f"connections warm failed: {e}")
        time.sleep(max(5.0, _CONN_TTL_S - 5.0))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
    log(f"server on http://127.0.0.1:{PORT}")
    log(f"artifacts: {ARTIFACTS_ROOT}")
    threading.Thread(target=_warm_connections_loop, daemon=True).start()
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
# A mindframe is a *surface*: the agent owns one index.html it rewrites in place
# (see docs/onboarding-ux.md). This endpoint lists surface mindframes: frame dirs
# under FRAMES_ROOT that hold an index.html. Per-mindframe viewing is served at
# /m/<id> and creation at POST /api/frames/create.


def _read_meta(fdir: Path) -> dict[str, Any]:
    """Parse a frame dir's meta.json, returning {} if it's missing or unreadable."""
    meta_path = fdir / "meta.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


# The composing placeholder's <title> (and other non-labels) — skip these so a
# frame mid-compose falls back to its meta title instead of showing "composing…".
_PLACEHOLDER_TITLES = {"composing…", "composing...", "mindframe", ""}


def _page_title(index: Path) -> str | None:
    """The <title> of the agent's current page, when it's a real label. The agent
    rewrites a complete HTML document each turn, so its <title> is the freshest
    description of what the frame is actually about — far better than the
    creation-time meta title (which for every launchpad is just "Where to start").
    Reads only the head, so page size doesn't matter."""
    try:
        with open(index, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return None
    m = re.search(r"<title>(.*?)</title>", head, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t if t.lower() not in _PLACEHOLDER_TITLES else None


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
            "title": _page_title(index) or meta.get("title") or fdir.name,
            "status": meta.get("status") or "active",
            "modified": modified,
            "tags": meta.get("tags") or [],
        })
    out.sort(key=lambda f: f["modified"], reverse=True)
    return {"frames": out}


# Window (seconds) within which a transcript write counts as "the agent is
# working right now" — backs the surface dock's per-frame pulse markers.
_FRAME_ACTIVE_WINDOW_S = 8.0


@app.get("/api/frames/activity")
async def api_frames_activity() -> dict[str, Any]:
    """Per-frame 'is the agent working right now' signal for the surface dock.
    Working = the agent's transcript was written within _FRAME_ACTIVE_WINDOW_S.
    Cheap: one transcript stat per frame, no parsing — so the dock can poll it
    every couple seconds to pulse whichever frames are live, even unfocused ones."""
    now = time.time()
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
        if not (fdir / "index.html").is_file():
            continue
        working = False
        try:
            tp = _agent_transcript(fdir, _frame_task_id(fdir.name, fdir))
            if tp is not None and (now - tp.stat().st_mtime) < _FRAME_ACTIVE_WINDOW_S:
                working = True
        except OSError:
            pass
        out.append({"id": fdir.name, "working": working})
    return {"frames": out}


# --------------------------- mindframe creation ---------------------------
#
# Create = mint a frame dir + spawn a persistent agent whose cwd is that dir and
# whose one job is to own index.html. The agent is spawned through taskpilot's
# spawner CLI (the agent-spawning provider), located via the installed-plugins
# manifest. The agent writes its page with the plain Write tool — no MCP.

MINDFRAME_BRIEF = """You are a mindframe — an autonomous agent that builds and evolves ONE small \
living web app for the operator. You own exactly ONE file:

    {index}

It is a real app with two loops. The FAST loop is the page itself: its own \
JavaScript handles instant interaction while you sleep. The SLOW loop is you: \
you wake when messaged, do real work (Bash, files, the MCPs and CLIs available \
to you — never fabricate; if you can't reach something, say so on the page), \
and evolve the page to match the new state. Then you stop and wait.

EVOLVE, DON'T REPLACE
  - The file must ALWAYS be one complete, valid, self-contained HTML document \
(inline CSS; no fragments). But prefer the Edit tool for targeted changes — \
update a number, add a section, append a row. Use a full Write only when the \
page's structure genuinely needs recomposition. Like code: edit normally, \
refactor when it drifts.
  - Put <meta name="mf-patch" content="safe"> in <head> and keep your script \
idempotent (event delegation on document, init guarded so it can run once). \
The shell then patches your edits into the live page with no reload, no \
flicker, no lost state. If you change your <script>, the shell reloads — \
that's expected.
  - While a long turn is in progress, you may make one early Edit that marks \
the affected section ("updating…") so the operator sees where you're working.

THREE KINDS OF INTERACTION — choose the cheapest that works:
  1. INSTANT (no you): plain client-side JS in the page — filtering, sorting, \
toggling, calculating. Build real app behavior here; you are allowed and \
encouraged to write substantial JS.
  2. STATE (no you, remembered): persist operator input to your data plane so \
you see it next time you wake. From page JS (your page is served at \
/api/frame/<id>/page; swap /page for /data/<key>):
      fetch(location.pathname.replace('/page','/data/board'),{{method:'PUT',\
headers:{{'Content-Type':'application/json'}},body:JSON.stringify(state)}})
     The same values are plain files in YOUR cwd at data/<key>.json — read \
them at the start of every turn; the operator may have changed things while \
you slept.
  3. WAKE ME (intelligence needed): a button that messages you. Use EXACTLY \
this pattern (swap /page for /message):
      <button onclick="fetch(location.pathname.replace('/page','/message'),\
{{method:'POST',headers:{{'Content-Type':'application/json'}},\
body:JSON.stringify({{text:'A CLEAR INSTRUCTION TO YOU'}})}})\
.then(function(){{this.disabled=true;this.textContent='on it…'}}.bind(this))">Label</button>
     Reserve these for work that needs thinking; never use a slow button where \
instant JS or state would do. The message box remains for free-form asks.

RULES
  - Calm and legible: type, weight, colour, spacing carry meaning. No emoji. \
Include <meta name="viewport" content="width=device-width, initial-scale=1"> \
and keep the page readable on a phone.
  - The page shows what matters NOW — it is the interface, not a log.
  - NEVER declare yourself done. End every page with a forward question or a \
clear next step.
  - Anything irreversible or outward-facing: draw the pending action on the \
page with an explicit approval button and wait for the operator to approve \
before doing it.

THE OPERATOR'S FIRST REQUEST
{prompt}

Compose your first index.html now: acknowledge the request, show what you \
understand and your first concrete step, and end with a question."""

# Starter prompt for a *revived* agent — a frame whose original agent died
# (reboot, crash, context exhaustion). The page on disk is the state; the new
# agent resumes ownership instead of composing a "first" page over it. Sent as
# the `prompt` override on POST /tasks/<id>/start; the operator's actual
# message follows as a normal channel message right after.
REVIVAL_BRIEF = """You are a mindframe — an autonomous agent that builds and evolves ONE small \
living web app for the operator. You are RESUMING ownership of an existing \
mindframe whose previous agent session ended (machine reboot or crash). You \
own exactly ONE file:

    {index}

It already holds the current state of your work — READ IT FIRST to recover \
context. Also read any data/<key>.json files in your cwd: that is your data \
plane, and the operator may have changed state through the page while no \
agent was alive. The request that originally created this mindframe was:

{prompt}

THE MODEL — two loops:
  - FAST loop: the page's own JavaScript handles instant interaction while \
you sleep, and persists operator input to the data plane \
(fetch PUT on location.pathname.replace('/page','/data/<key>') from page JS \
= data/<key>.json in your cwd).
  - SLOW loop: you. You wake when messaged, do real work (Bash, files, MCPs, \
CLIs — never fabricate), and evolve the page to match the new state. Then \
you stop and wait.

EVOLVE, DON'T REPLACE
  - The file must ALWAYS be one complete, valid, self-contained HTML document \
(inline CSS). Prefer the Edit tool for targeted changes; full Write only when \
the structure needs recomposition.
  - Keep <meta name="mf-patch" content="safe"> in <head> with idempotent, \
event-delegated script so the shell can patch updates in without reloading.
  - Buttons that need your intelligence message you (swap /page for /message \
in your page's own URL); anything instant or stateful belongs in page JS + \
the data plane instead.

RULES
  - Calm and legible, no emoji, viewport meta, readable on a phone.
  - NEVER declare yourself done; end with a forward question or next step.
  - Irreversible or outward-facing actions: draw a pending action with an \
approval button and wait for operator approval.

Do NOT rewrite the page yet — the operator's message arrives immediately \
after this brief. Read the current page and data plane, act on that message, \
then evolve the page."""


def _mint_frame_id(n: int = 10) -> str:
    """Generate a random n-char lowercase-alphanumeric frame id."""
    return "".join(secrets.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(n))


def _mint_frame(mid: str, title: str, prompt: str,
                spawned_by: dict[str, Any]) -> Path:
    """Mint frames/<mid> on disk: placeholder page + meta.json. Instant — no
    daemon calls. mkdir raises FileExistsError if the dir appeared since the
    caller checked (caller decides what a lost race means)."""
    fdir = FRAMES_ROOT / mid
    fdir.mkdir(parents=True, mode=0o755)   # no exist_ok: surface a lost race to the caller
    index = fdir / "index.html"
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;")
    index.write_text(
        "<!doctype html><meta charset=utf-8><title>composing…</title>"
        "<body style='margin:0;height:100vh;display:grid;place-items:center;"
        "font:16px system-ui;color:#888;background:#0d0d0f'>"
        "<style>@keyframes b{0%,100%{opacity:.25}50%{opacity:1}}</style>"
        "<div style='text-align:center'>"
        "<div style='width:10px;height:10px;border-radius:50%;background:#5b6cff;"
        "margin:0 auto 14px;animation:b 1.1s ease-in-out infinite'></div>"
        f"Starting this mindframe&hellip;<br><small style='color:#555'>{safe_title}</small><br>"
        "<small style='color:#555'>the agent boots (~20s), then composes this page — "
        "watch it think in the strip below</small></div>",
        "utf-8",
    )
    (fdir / "meta.json").write_text(json.dumps({
        "id": mid, "title": title, "task_id": mid, "status": "active",
        "prompt": prompt, "spawned_by": spawned_by,
    }, indent=2), "utf-8")
    return fdir


async def _define_and_start(mid: str, fdir: Path, prompt: str,
                            start_prompt: str | None = None) -> tuple[bool, str]:
    """PUT the task definition + ensure it's running. Returns (ok, error).
    Blocks ~16s on a real spawn — callers run it in the background."""
    brief = MINDFRAME_BRIEF.format(index=str(fdir / "index.html"), prompt=prompt.strip())
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.put(
                f"{TASKPILOT_DAEMON}/tasks/{mid}",
                json={"name": mid, "description": brief, "cwd": str(fdir)},
            )
            if r.status_code < 300:
                body = {"prompt": start_prompt} if start_prompt is not None else {}
                r = await client.post(f"{TASKPILOT_DAEMON}/tasks/{mid}/start", json=body)
    except httpx.HTTPError as e:
        return False, f"spawn failed: {e}"
    if r.status_code >= 300:
        return False, f"daemon {r.status_code}: {r.text[:400]}"
    return True, ""


async def _spawn_frame_bg(mid: str, fdir: Path, prompt: str) -> None:
    """Background half of create: define + start the agent. A failure is
    recorded in meta.json (the surface shows it; the next operator message
    self-heals by re-running define+start through the message path)."""
    ok, err = await _define_and_start(mid, fdir, prompt)
    if not ok:
        log(f"background spawn failed for {mid}: {err}")
        try:
            meta = _read_meta(fdir)
            meta["status"] = "spawn_failed"
            meta["spawn_error"] = err
            (fdir / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
        except OSError:
            pass


async def _daemon_reachable() -> bool:
    """True if the taskpilot (agent-spawning) daemon answers a health probe."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            health = await client.get(f"{TASKPILOT_DAEMON}/health")
        return health.status_code < 300
    except httpx.HTTPError:
        return False


class CreateMindframe(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    title: str | None = None


@app.post("/api/frames/create")
async def create_mindframe(body: CreateMindframe, background_tasks: BackgroundTasks) -> Response:
    """Create a surface mindframe INSTANTLY: mint the frame dir + placeholder
    and return the id at once; the agent spawn (define + start, ~16s) runs in
    the background. The SPA navigates to /m/<id> immediately and the surface
    narrates the boot. Task_id == mid, so /api/frame/<id>/message routes to
    the same agent — and if the background spawn failed, that message path
    re-runs define+start, so a frame can never get permanently stuck."""
    # Probe the daemon before minting, so a down spawner doesn't mint frames
    # whose agents can never start. Fast (~ms) when the daemon is up.
    if not await _daemon_reachable():
        return JSONResponse(
            {"error": "taskpilot daemon (agent-spawning) not reachable — can't spawn a mindframe agent."},
            status_code=503)

    for _ in range(5):
        mid = _mint_frame_id()
        if not (FRAMES_ROOT / mid).exists():
            break
    else:
        return JSONResponse({"error": "could not mint a unique frame id"}, status_code=500)

    title = (body.title or body.prompt.strip().split("\n", 1)[0])[:120]
    try:
        fdir = _mint_frame(mid, title, body.prompt, {"kind": "dashboard"})
    except OSError as e:
        return JSONResponse({"error": f"filesystem error: {e}"}, status_code=500)
    background_tasks.add_task(_spawn_frame_bg, mid, fdir, body.prompt)
    return JSONResponse({"id": mid, "url": f"/m/{mid}", "spawn": "starting"})


# --------------------------- mindframe surface (viewing) ---------------------------
#
# The surface: one server serves every mindframe. A mindframe is a conversation
# where the agent's replies are full web pages — the agent owns ONE
# <framedir>/index.html and rewrites it in place; the user has ONE message box.
# /m/<id> renders the shell; the agent owns <framedir>/index.html and
# rewrites it in place; the shell polls /api/frame/<id>/rev and reloads. User
# messages reach the agent through the taskpilot daemon (which wakes a dormant
# agent on contact); /activity tails the agent's transcript for the cognition log.


def _frame_dir(mid: str) -> Path | None:
    """Resolve a frame id to its directory, or None if the id is malformed or
    the directory doesn't exist. Also guards against path traversal via the id."""
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
    try:
        rev = index.stat().st_mtime_ns
    except OSError:
        # The agent may rewrite/delete index.html between an is_file() check
        # and the stat — treat any race as "no revision yet".
        rev = 0
    return JSONResponse({"rev": rev})


class FrameMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


def _daemon_error_code(r: httpx.Response) -> str:
    """The machine-readable `detail.code` of a taskpilot error response, or ''."""
    try:
        detail = r.json().get("detail")
        return detail.get("code", "") if isinstance(detail, dict) else ""
    except ValueError:
        return ""


@app.post("/api/frame/{mid}/message")
async def frame_message(mid: str, body: FrameMessage) -> Response:
    """Deliver a user message to the mindframe's agent via the taskpilot
    daemon — reviving the agent first if it died.

    A frame outlives its agent process (reboots, crashes, context
    exhaustion); the page on disk is the durable state. When taskpilot says
    `agent_not_running` (409), we respawn the task with a revival brief —
    "resume ownership of the existing page" — and deliver the message to the
    fresh agent. The response carries `revived: true` so the surface can say
    what happened."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    task_id = _frame_task_id(mid, fdir)
    payload = {"text": body.text, "from_session": "mindframe-surface"}
    msg_url = f"{TASKPILOT_DAEMON}/tasks/{task_id}/message"

    try:
        # start blocks ~16s on a revival; size the client timeout for that.
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(msg_url, json=payload, timeout=20)
            revived = False
            if r.status_code == 409 and _daemon_error_code(r) == "agent_not_running":
                brief = REVIVAL_BRIEF.format(
                    index=str(fdir / "index.html"),
                    prompt=(_read_meta(fdir).get("prompt") or "(unrecorded)").strip(),
                )
                rs = await client.post(f"{TASKPILOT_DAEMON}/tasks/{task_id}/start",
                                       json={"prompt": brief})
                if rs.status_code >= 300:
                    return JSONResponse(
                        {"ok": False,
                         "error": f"agent was dead and revival failed: daemon {rs.status_code}: {rs.text[:200]}"},
                        status_code=502)
                revived = True
                r = await client.post(msg_url, json=payload, timeout=20)
            elif r.status_code == 404:
                # The task row doesn't exist at all — the background spawn
                # after create failed (or the taskpilot DB was reset). Define
                # + start from meta.json, then deliver. A frame that still
                # shows the boot placeholder gets the normal first-compose
                # flow; one with a real page gets the revival brief.
                meta = _read_meta(fdir)
                prompt = (meta.get("prompt") or "(unrecorded)").strip()
                start_prompt = None
                if _page_title(fdir / "index.html") is not None:
                    start_prompt = REVIVAL_BRIEF.format(
                        index=str(fdir / "index.html"), prompt=prompt)
                ok, err = await _define_and_start(task_id, fdir, prompt, start_prompt)
                if not ok:
                    return JSONResponse(
                        {"ok": False, "error": f"agent was never spawned and recovery failed: {err}"},
                        status_code=502)
                revived = True
                r = await client.post(msg_url, json=payload, timeout=20)
            if r.status_code >= 300:
                code = _daemon_error_code(r)
                retryable = code == "channel_not_ready"
                return JSONResponse(
                    {"ok": False, "revived": revived, "retryable": retryable,
                     "error": f"daemon {r.status_code}: {r.text[:200]}"},
                    status_code=502)
    except httpx.HTTPError as e:
        return JSONResponse({"ok": False, "error": f"taskpilot daemon unreachable: {e}"},
                            status_code=502)
    return JSONResponse({"ok": True, "revived": revived})


@app.delete("/api/frame/{mid}")
async def delete_frame(mid: str) -> Response:
    """Tear a mindframe down: delete its task (stops the agent AND frees the
    task id for reuse), then remove the frame dir. A mindframe is two things —
    a persistent taskpilot agent (task_id == mid) and its frame dir — so
    deleting the dir alone would orphan a live agent. The taskpilot DELETE is
    best-effort: a non-2xx/transport failure still removes the dir so the
    frame stops showing up. The Claude transcript under ~/.claude/projects is
    left alone."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    task_id = _frame_task_id(mid, fdir)

    killed = False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.delete(f"{TASKPILOT_DAEMON}/tasks/{task_id}")
        killed = r.status_code < 300
    except httpx.HTTPError:
        killed = False

    try:
        shutil.rmtree(fdir)
    except OSError as e:
        return JSONResponse(
            {"ok": False, "killed": killed, "error": f"agent killed={killed}, but couldn't remove frame dir: {e}"},
            status_code=500)
    return JSONResponse({"ok": True, "id": mid, "killed": killed})


# --------------------------- frame data plane ---------------------------
#
# Shared state between a frame's PAGE (fast loop: client JS, instant) and its
# AGENT (slow loop: thinking, wakes on messages). Keys are JSON files at
# <framedir>/data/<key>.json — the page reads/writes them over HTTP
# (relative to its own URL: /page → /data/<key>); the agent reads/writes the
# same files directly in its cwd and sees operator input on its next turn.
# Deliberately tiny — a per-frame KV store, not a backend.

DATA_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DATA_MAX_BYTES = 256 * 1024
DATA_MAX_KEYS = 200


def _data_dir(fdir: Path) -> Path:
    return fdir / "data"


@app.get("/api/frame/{mid}/data")
def frame_data_list(mid: str) -> Response:
    """List this frame's data keys with sizes + mtimes."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    out = []
    ddir = _data_dir(fdir)
    if ddir.is_dir():
        for f in sorted(ddir.glob("*.json")):
            try:
                st = f.stat()
            except OSError:
                continue
            out.append({"key": f.stem, "size": st.st_size,
                        "modified": int(st.st_mtime * 1000)})
    return JSONResponse({"keys": out}, headers={"Cache-Control": "no-store"})


@app.get("/api/frame/{mid}/data/{key}")
def frame_data_get(mid: str, key: str) -> Response:
    """Read one data key (the JSON value, verbatim)."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    if not DATA_KEY_RE.match(key):
        return JSONResponse({"error": "bad key"}, status_code=422)
    f = _data_dir(fdir) / f"{key}.json"
    if not f.is_file():
        return JSONResponse({"error": "no such key"}, status_code=404)
    try:
        body = f.read_bytes()
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return Response(body, media_type="application/json",
                    headers={"Cache-Control": "no-store"})


@app.put("/api/frame/{mid}/data/{key}")
async def frame_data_put(mid: str, key: str, request: Request) -> Response:
    """Write one data key. Body must be JSON; capped at DATA_MAX_BYTES.
    Atomic (tmp + rename) so the agent never reads a torn write."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    if not DATA_KEY_RE.match(key):
        return JSONResponse({"error": "bad key"}, status_code=422)
    body = await request.body()
    if len(body) > DATA_MAX_BYTES:
        return JSONResponse({"error": f"value too large (max {DATA_MAX_BYTES} bytes)"},
                            status_code=413)
    try:
        json.loads(body)
    except ValueError:
        return JSONResponse({"error": "body must be JSON"}, status_code=422)
    ddir = _data_dir(fdir)
    ddir.mkdir(exist_ok=True)
    if not (ddir / f"{key}.json").exists() and len(list(ddir.glob("*.json"))) >= DATA_MAX_KEYS:
        return JSONResponse({"error": f"too many keys (max {DATA_MAX_KEYS})"}, status_code=409)
    tmp = ddir / f".{key}.json.tmp"
    try:
        tmp.write_bytes(body)
        tmp.replace(ddir / f"{key}.json")
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "key": key, "size": len(body)})


# --- cognition log: tail the agent's Claude transcript (ported from surface/) ---

def _pretty_model(name: str) -> str:
    """Shorten a raw model id for display: drop the `claude-` prefix and the
    `-YYYYMMDD` date suffix, and turn `4-8` back into `4.8`."""
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
    """Read the dispatcher bearer token from disk, or None if absent/empty. The
    server holds this so the browser never sees it (see /api/dashboard-event)."""
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
# (~/.mindframe/vault). There is no vault catalog and no sharing —
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


def _vault_last_modified(vault_path: Path) -> str | None:
    """ISO timestamp of the most recently modified note, or None if empty.

    The vault is a plain local directory (no git), so its freshness signal is
    simply the newest note's mtime.
    """
    newest = 0.0
    for child in vault_path.iterdir():
        if child.is_dir() and not child.name.startswith((".", "_")):
            for md in child.glob("*.md"):
                try:
                    newest = max(newest, md.stat().st_mtime)
                except OSError:
                    continue
    if newest == 0.0:
        return None
    return datetime.fromtimestamp(newest, tz=timezone.utc).isoformat()


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
        "last_modified": _vault_last_modified(path) if exists else None,
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


# --------------------------- connections (live discovery) ---------------------------
#
# Live discovery of what this machine can reach: MCPs Claude is connected to
# (`claude mcp list`) plus connector skills carrying a `connection:`
# fingerprint, minus mindframe's own runtime MCPs. See docs/onboarding-ux.md.
# Cached briefly so the probes don't run every poll.
#
# Connections never store credentials in mindframe: agents act through the
# operator's existing CLIs/MCPs (identity inheritance). The only secrets
# mindframe itself creates live under ~/.mindframe/secrets/ (file-handoff,
# e.g. the dispatcher bearer above and connector-skill access files).

# mindframe's own runtime — the bundle's composed plugins; never user-facing.
_BUNDLE_RUNTIME = {
    "daemon-manager", "claude-browser-bridge", "softwaresoftware",
    "tmux-session", "taskpilot", "session-bridge", "mindframe",
}
# Display-name overrides for ids whose title-cased form reads wrong.
_CONN_DISPLAY = {
    "claude-browser-bridge": "Browser",
}
# Bundle MCPs that ARE shown as connections despite being bundle runtime.
# browser-bridge is a real access door (it can reach any web system), so we
# surface it even though the installer brought it. Everything else in
# _BUNDLE_RUNTIME stays hidden as plumbing.
_CONN_BUNDLE_KEEP = {"claude-browser-bridge"}
_conn_cache: dict[str, Any] = {"at": 0.0, "data": None}
_conn_lock = threading.Lock()
_CONN_TTL_S = 30.0


def _conn_run(cmd: list[str], timeout: float = 20.0):
    """Run a subprocess and return its CompletedProcess, or None if it couldn't
    run at all (binary missing, timeout, etc.). Callers treat None as "tool
    unavailable" and a non-zero returncode as "ran but failed"."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def _parse_mcp_list() -> list[dict[str, Any]]:
    """Run `claude mcp list` and normalize each line to {id, name, state, bundle}.

    Used by /api/connections (live discovery). Lines look like
    `name: <url-or-cmd> - <status>`, where status carries 'Connected' or an
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


# --- connector skills: SKILL.md files carrying a `connection:` fingerprint ---
#
# A connection can be declared as a skill instead of hardcoded here: any SKILL.md
# whose frontmatter has a `connection:` block is a connector. We scan the
# operator's user-scope skills (~/.claude/skills) plus installed-plugin skills,
# parse the fingerprint, and probe each one's `check`. This is how any connector
# /mindframe:connect authors shows up — no code change to add one, just a new
# SKILL.md in ~/.claude/skills/.


def _skill_dirs() -> list[Path]:
    """Directories Claude Code loads skills from: user scope + installed plugins."""
    dirs = [Path.home() / ".claude" / "skills"]
    manifest = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(manifest.read_text("utf-8"))
        for installs in (data.get("plugins") or {}).values():
            if installs and installs[0].get("installPath"):
                dirs.append(Path(installs[0]["installPath"]) / "skills")
    except (OSError, ValueError):
        pass
    return dirs


def _read_connector_skill(skill_md: Path) -> dict | None:
    """Parse a SKILL.md; return {id, connection} if it carries a fingerprint, else None."""
    try:
        text = skill_md.read_text("utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        import yaml
        fm = yaml.safe_load(text[3:end]) or {}
    except Exception:
        return None
    conn = fm.get("connection") if isinstance(fm, dict) else None
    if not isinstance(conn, dict):
        return None
    return {"id": str(fm.get("name") or skill_md.parent.name), "connection": conn}


def _connector_skills() -> list[dict]:
    """Every skill carrying a `connection:` fingerprint, de-duped by id."""
    out: list[dict] = []
    seen: set[str] = set()
    for d in _skill_dirs():
        if not d.is_dir():
            continue
        for sk in sorted(d.glob("*/SKILL.md")):
            rec = _read_connector_skill(sk)
            if not rec or rec["id"].lower() in seen:
                continue
            seen.add(rec["id"].lower())
            out.append(rec)
    return out


def _discover_connections() -> dict:
    """List connections — presence only, no live status. A connection is either an
    MCP Claude is connected to or a skill carrying a `connection:` fingerprint. We
    do NOT run the connectors' `check`/`account` commands yet (status feature is
    deferred), so this is just `claude mcp list` + a fingerprint scan. The mcp list
    is still ~7s, so the cache is warmed in the background (_warm_connections_loop)."""
    conns: list[dict] = []
    seen: set[str] = set()

    # MCPs. Hide the bundle's own runtime (taskpilot, session-bridge, …) as
    # plumbing — except _CONN_BUNDLE_KEEP (browser-bridge), a real access door.
    for m in _parse_mcp_list():
        if m["bundle"] and m["id"] not in _CONN_BUNDLE_KEEP:
            continue
        if m["id"].lower() in seen:
            continue
        seen.add(m["id"].lower())
        conns.append({"id": m["id"], "kind": "mcp", "name": m["name"]})

    # Connector skills — any SKILL.md with a `connection:` fingerprint. Listed
    # whether or not their tool is installed/authed; that's the deferred status.
    for sk in _connector_skills():
        sid = sk["id"]
        if sid.lower() in seen:
            continue
        seen.add(sid.lower())
        fp = sk["connection"]
        conns.append({
            "id": sid,
            "kind": str(fp.get("kind") or "skill"),
            "name": fp.get("label") or _CONN_DISPLAY.get(sid, sid.replace("-", " ").title()),
        })

    conns.sort(key=lambda c: (c["kind"] != "cli", c["name"]))
    return {"connections": conns, "total": len(conns)}


@app.get("/api/connections")
def list_connections() -> Response:
    """Live-discovered connections (MCPs + connector skills), minus mindframe's
    own runtime. Cached for _CONN_TTL_S so the discovery probes stay cheap."""
    now = time.time()
    with _conn_lock:
        data, at = _conn_cache["data"], _conn_cache["at"]
    if data is None or (now - at) > _CONN_TTL_S:
        data = _discover_connections()
        with _conn_lock:
            _conn_cache["data"] = data
            _conn_cache["at"] = now
    return JSONResponse(data)


# --------------------------- read-only system endpoints ---------------------------
#
# Two endpoints that back the hub's Events and Agents drawers. Each maps one
# bucket of the bundle's mental model to its real on-disk source of truth:
#
#   /api/events  — dispatcher routes      (~/.dispatcher/channels.yaml)
#   /api/agents  — recipes + taskpilot db (~/.dispatcher/recipes, ~/.taskpilot)
#
# (The former /api/capabilities — MCPs + plugin skills — was removed with the
# /system overview it backed; deprecated 2026-06-08.)
#
# Both read-only, both defensive: a missing dispatcher / taskpilot / claude CLI
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
    """The set of live tmux session names — ground truth for which agents are
    actually running. Empty set if tmux is absent or has no sessions."""
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
        """True if an ISO/space-separated timestamp is within the 3-day cutoff
        (treating a naive timestamp as UTC). Used to drop stale pending zombies."""
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
