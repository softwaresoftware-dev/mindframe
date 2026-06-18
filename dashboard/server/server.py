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
_MINDFRAME_HOME = Path(os.environ.get("MINDFRAME_HOME", str(Path.home() / ".mindframe")))
FRAMES_ROOT = Path(os.environ.get("MINDFRAME_FRAMES_ROOT", str(_MINDFRAME_HOME / "frames")))
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Knowledge-base vault. Defaults to <MINDFRAME_HOME>/vault; override with
# MINDFRAME_VAULT_DIR for named workspaces.
VAULT_DIR = Path(os.environ.get("MINDFRAME_VAULT_DIR", str(_MINDFRAME_HOME / "vault")))


PORT = int(os.environ.get("PORT", "5174"))

DISPATCHER_URL = os.environ.get("MINDFRAME_DISPATCHER_URL", "http://127.0.0.1:8911")
DISPATCHER_BEARER_FILE = Path(
    os.environ.get("MINDFRAME_DISPATCHER_BEARER_FILE", str(_MINDFRAME_HOME / "secrets" / "dispatcher-bearer.token"))
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


# Frame kinds — the operator-facing ontology:
#   created   — a CONVERSATION: a place the operator thinks with an agent;
#               the page is the agent's voice (default; launchpads, ad-hoc)
#   app       — an ARTIFACT: a functional, long-lived tool the agent built
#               and maintains; the operator USES it (fast loop only) and the
#               agent wakes only for maintenance. Full-bleed presentation.
#   delivered — a watch's deliverable; arrives unprompted, lives in the Inbox
#               until handled, carries origin {watch, event, at}
#   watch     — a singleton frame that manages one watch (recipe + route)
_FRAME_KINDS = {"created", "app", "delivered", "watch"}

# Sent to a frame's agent when the operator (or the agent's own judgment, via
# the promote endpoint) turns a conversation into an app.
APP_PROMOTION_NOTE = """This frame is now an APP — a long-lived functional tool the operator USES \
rather than converses with. Restructure your index.html into app-grade:
  - instant client-side interactions for everything the app does; persistent \
state in your data plane (data/<key>.json — read it at the start of every turn)
  - keep <meta name="mf-patch" content="safe"> and idempotent, event-delegated script
  - theming is yours: keep your own look, or build on the operator's design \
system (<link rel="stylesheet" href="/frame.css">) if it fits the tool
  - NO conversational flow-buttons in the app UI — the app's controls do app \
things; change requests reach you through the maintenance bar instead
  - give the app a real name in <title> and meta.json's title field
You are now its MAINTAINER: future messages are change requests or bug \
reports. Evolve the app with Edit; never replace it with a conversation page. \
Apply the restructure now."""

# Appended to the revival brief when the dead frame is an app, so the
# successor agent maintains instead of conversing.
APP_REVIVAL_NOTE = """\n\nIMPORTANT: this frame is an APP, not a conversation. The page is a \
functional tool the operator uses; its state lives in data/<key>.json. You \
are its maintainer — treat the operator's message as a change request or bug \
report, evolve the app with Edit, and never replace it with a conversation \
page."""


def _write_meta(fdir: Path, meta: dict[str, Any]) -> None:
    (fdir / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")


def _supersede_deliveries(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Within one watch, a newer unhandled delivery supersedes older ones —
    the old frames are auto-archived (page stays on disk; history lives in
    the watch's frame). Mutates meta on disk for the superseded; returns the
    list minus them."""
    newest_per_watch: dict[str, int] = {}
    for f in frames:
        if f["kind"] == "delivered" and not f["archived"] and f.get("watch"):
            newest_per_watch[f["watch"]] = max(
                newest_per_watch.get(f["watch"], 0), f["modified"])
    keep: list[dict[str, Any]] = []
    for f in frames:
        if (f["kind"] == "delivered" and not f["archived"] and f.get("watch")
                and f["modified"] < newest_per_watch.get(f["watch"], 0)):
            fdir = FRAMES_ROOT / f["id"]
            meta = _read_meta(fdir)
            meta["archived"] = True
            meta["superseded"] = True
            try:
                _write_meta(fdir, meta)
            except OSError:
                pass
            continue
        keep.append(f)
    return keep


@app.get("/api/frames")
async def api_frames(archived: int = 0) -> dict[str, Any]:
    """List surface mindframes — frame dirs under FRAMES_ROOT holding an
    index.html — newest-activity first, with the operator-facing kind
    (created / delivered / watch) and delivery provenance. Archived frames
    are hidden unless ?archived=1; superseded deliveries auto-archive."""
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
        # True recency is the latest of: the agent rewrote the page, the
        # agent's transcript moved, or the operator touched the frame's data
        # plane (moved a card, checked a box). The page mtime alone goes
        # stale the moment activity doesn't end in a rewrite.
        try:
            tp = _agent_transcript(fdir, _frame_task_id(fdir.name, fdir))
            if tp is not None:
                modified = max(modified, int(tp.stat().st_mtime * 1000))
        except OSError:
            pass
        try:
            ddir = fdir / "data"
            if ddir.is_dir():
                modified = max(modified, int(ddir.stat().st_mtime * 1000))
        except OSError:
            pass
        kind = meta.get("kind")
        if kind not in _FRAME_KINDS:
            kind = "created"
        origin = meta.get("origin") if isinstance(meta.get("origin"), dict) else {}
        out.append({
            "id": fdir.name,
            "title": _page_title(index) or meta.get("title") or fdir.name,
            "status": meta.get("status") or "active",
            "kind": kind,
            "archived": bool(meta.get("archived")),
            "watch": origin.get("watch") or (meta.get("watch") if kind == "watch" else None),
            "origin": origin or None,
            "modified": modified,
            "tags": meta.get("tags") or [],
        })
    out = _supersede_deliveries(out)
    if not archived:
        out = [f for f in out if not f["archived"]]
    out.sort(key=lambda f: f["modified"], reverse=True)
    return {"frames": out}


@app.post("/api/frame/{mid}/archive")
def archive_frame(mid: str) -> Response:
    """Mark a frame handled — hidden from the dock, page kept on disk."""
    return _set_archived(mid, True)


@app.post("/api/frame/{mid}/unarchive")
def unarchive_frame(mid: str) -> Response:
    return _set_archived(mid, False)


def _set_archived(mid: str, value: bool) -> Response:
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    meta = _read_meta(fdir)
    meta["archived"] = value
    try:
        _write_meta(fdir, meta)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "id": mid, "archived": value})


# Window (seconds) within which a transcript write counts as "the agent is
# working right now" — backs the surface dock's per-frame pulse markers.
_FRAME_ACTIVE_WINDOW_S = 8.0

# tmux-session set cache: the dock polls activity every ~2s per open tab; one
# `tmux ls` per TTL keeps that O(1) subprocesses regardless of tabs/frames.
_tmux_cache: dict[str, Any] = {"at": 0.0, "set": set()}
_TMUX_TTL_S = 2.0


def _live_tmux_cached() -> set[str]:
    now = time.time()
    if (now - _tmux_cache["at"]) > _TMUX_TTL_S:
        _tmux_cache["set"] = _tmux_sessions()
        _tmux_cache["at"] = now
    return _tmux_cache["set"]


@app.get("/api/frames/activity")
async def api_frames_activity() -> dict[str, Any]:
    """Per-frame liveness for the surface dock, two signals per frame:
      working — the agent's transcript was written within _FRAME_ACTIVE_WINDOW_S
      awake   — the agent's tmux session exists (asleep frames wake on message)
    Cheap: one transcript stat per frame + one cached `tmux ls`, so the dock
    can poll every couple seconds."""
    now = time.time()
    out: list[dict[str, Any]] = []
    if not FRAMES_ROOT.is_dir():
        return {"frames": []}
    try:
        entries = list(FRAMES_ROOT.iterdir())
    except OSError:
        return {"frames": []}
    live = _live_tmux_cached()
    for fdir in entries:
        if not fdir.is_dir() or not FRAME_ID_RE.match(fdir.name):
            continue
        if not (fdir / "index.html").is_file():
            continue
        working = False
        task_id = _frame_task_id(fdir.name, fdir)
        try:
            tp = _agent_transcript(fdir, task_id)
            if tp is not None and (now - tp.stat().st_mtime) < _FRAME_ACTIVE_WINDOW_S:
                working = True
        except OSError:
            pass
        out.append({"id": fdir.name, "working": working, "awake": task_id in live})
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
  - The file must ALWAYS be one complete, valid HTML document (no fragments). \
Start your <head> with <link rel="stylesheet" href="/frame.css"> — the \
operator's design system (calm dark; indigo = action, gold = identity; \
ready-made .card, .pill, .label, .pending-action, .actions, button styles). \
Build on it and add only page-specific CSS inline. Prefer the Edit tool for \
targeted changes — \
update a number, add a section, append a row. Use a full Write only when the \
page's structure genuinely needs recomposition. Like code: edit normally, \
refactor when it drifts.
  - Put <meta name="mf-patch" content="safe"> in <head> and keep your script \
idempotent (event delegation on document, init guarded so it can run once). \
The shell then patches your edits into the live page with no reload, no \
flicker, no lost state. If you change your <script>, the shell reloads — \
that's expected. If your script renders DOM from data, listen for the \
shell's 'mf:patched' window event and re-render on it.
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
  - Calm and legible: the design system carries the look; you carry the \
content hierarchy. No emoji. Include <meta name="viewport" \
content="width=device-width, initial-scale=1"> and keep the page readable on \
a phone. Draw irreversible-action approvals as a .pending-action card.
  - The page shows what matters NOW — it is the interface, not a log.
  - TWO CONCEPTS: if your mission is to BUILD A FUNCTIONAL TOOL the operator \
will use repeatedly (a board, a tracker, a calculator) rather than to hold a \
working conversation, set "kind": "app" in your meta.json. The shell then \
presents your page full-bleed as an app with a maintenance bar, and your role \
becomes its maintainer — app controls do app things; no conversational \
flow-buttons in an app's UI.
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
  - The file must ALWAYS be one complete, valid HTML document. Keep (or add) \
<link rel="stylesheet" href="/frame.css"> first in <head> — the operator's \
design system — and only page-specific CSS inline. Prefer the Edit tool for \
targeted changes; full Write only when the structure needs recomposition.
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
    # Workspace MCP isolation is handled at the agent-runtime level: the
    # workspace's taskpilot runs agents with HOME=<workspace root>, so the
    # workspace's ~/.claude (settings.json mcpServers + skills) is the agent's
    # user-scope config directly — no per-frame symlink needed. See the
    # mindframe `workspace` skill ("The isolation model").
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


_NOT_FOUND_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>mindframe — not found</title>
<link rel="stylesheet" href="/tokens.css">
<style>
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
         background: radial-gradient(ellipse at 50% 38%, #131318 0%, var(--color-bg) 62%);
         font-family: var(--font-ui); color: var(--color-text-soft); }}
  .box {{ text-align:center; padding:2rem; }}
  .label {{ font-family: var(--font-mono); font-size:.62rem; letter-spacing:.16em;
           text-transform:uppercase; color: var(--color-text-faint); }}
  h1 {{ font-family: var(--font-heading); color: var(--color-text);
       font-size:1.4rem; margin:.5rem 0 .4rem; }}
  p {{ color: var(--color-text-dim); font-size:14px; margin:0 0 1.6rem; line-height:1.6; }}
  .id {{ font-family: var(--font-mono); color: var(--color-gold); }}
  a.btn {{ display:inline-block; padding:.6rem 1.2rem; border-radius:8px;
          background: var(--color-accent); color:#fff; text-decoration:none; font-size:14px; }}
</style></head><body>
<div class="box">
  <div class="label">mindframe</div>
  <h1>This mindframe doesn't exist</h1>
  <p><span class="id">{mid}</span> — it may have been deleted, or the link is stale.</p>
  <a class="btn" href="/">back to home</a>
</div></body></html>"""


@app.get("/m/{mid}")
def surface_shell(mid: str) -> Response:
    """The mindframe surface shell — iframe over the agent's page + message rail
    + cognition log. The page's JS derives the id from the URL. A missing or
    deleted frame gets a real page with a way out, never raw JSON."""
    if _frame_dir(mid) is None:
        safe = mid.replace("&", "&amp;").replace("<", "&lt;")[:64]
        return Response(_NOT_FOUND_PAGE.format(mid=safe), media_type="text/html",
                        status_code=404, headers={"Cache-Control": "no-store"})
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


def _revival_brief(fdir: Path) -> str:
    """The resume-ownership brief for this frame, app-flavored when the frame
    is an app (the successor maintains; it doesn't converse)."""
    meta = _read_meta(fdir)
    brief = REVIVAL_BRIEF.format(
        index=str(fdir / "index.html"),
        prompt=(meta.get("prompt") or "(unrecorded)").strip(),
    )
    if meta.get("kind") == "app":
        brief += APP_REVIVAL_NOTE
    return brief


async def _deliver_to_frame(mid: str, fdir: Path, text: str,
                            from_session: str = "mindframe-surface") -> tuple[int, dict]:
    """Deliver text to the frame's agent, reviving or even re-defining it as
    needed. Returns (http_status, body) ready to wrap in a JSONResponse.
    Shared by the message endpoint and anything else that must reach the
    agent (e.g. the app-promotion note)."""
    task_id = _frame_task_id(mid, fdir)
    payload = {"text": text, "from_session": from_session}
    msg_url = f"{TASKPILOT_DAEMON}/tasks/{task_id}/message"
    try:
        # start blocks ~16s on a revival; size the client timeout for that.
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(msg_url, json=payload, timeout=20)
            revived = False
            if r.status_code == 409 and _daemon_error_code(r) == "agent_not_running":
                rs = await client.post(f"{TASKPILOT_DAEMON}/tasks/{task_id}/start",
                                       json={"prompt": _revival_brief(fdir)})
                if rs.status_code >= 300:
                    return 502, {"ok": False,
                                 "error": f"agent was dead and revival failed: daemon {rs.status_code}: {rs.text[:200]}"}
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
                    start_prompt = _revival_brief(fdir)
                ok, err = await _define_and_start(task_id, fdir, prompt, start_prompt)
                if not ok:
                    return 502, {"ok": False,
                                 "error": f"agent was never spawned and recovery failed: {err}"}
                revived = True
                r = await client.post(msg_url, json=payload, timeout=20)
            if r.status_code >= 300:
                code = _daemon_error_code(r)
                return 502, {"ok": False, "revived": revived,
                             "retryable": code == "channel_not_ready",
                             "error": f"daemon {r.status_code}: {r.text[:200]}"}
    except httpx.HTTPError as e:
        return 502, {"ok": False, "error": f"taskpilot daemon unreachable: {e}"}
    return 200, {"ok": True, "revived": revived}


@app.post("/api/frame/{mid}/message")
async def frame_message(mid: str, body: FrameMessage) -> Response:
    """Deliver a user message to the mindframe's agent via the taskpilot
    daemon — reviving (or re-defining) the agent first if needed. The
    response carries `revived: true` when that happened."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    status, payload = await _deliver_to_frame(mid, fdir, body.text)
    return JSONResponse(payload, status_code=status)


class FrameKind(BaseModel):
    kind: str


@app.post("/api/frame/{mid}/kind")
async def set_frame_kind(mid: str, body: FrameKind) -> Response:
    """Switch a frame between the two operator concepts: a conversation
    ('created') and an app ('app'). Delivered and watch frames keep their
    kinds. Promoting to app also tells the agent to restructure the page to
    app-grade (the maintenance contract)."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    if body.kind not in ("created", "app"):
        return JSONResponse({"error": "kind must be 'created' or 'app'"}, status_code=422)
    meta = _read_meta(fdir)
    if meta.get("kind") in ("delivered", "watch"):
        return JSONResponse({"error": f"a {meta['kind']} frame can't change kind"}, status_code=409)
    was = meta.get("kind") or "created"
    meta["kind"] = body.kind
    try:
        _write_meta(fdir, meta)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    delivered = False
    if body.kind == "app" and was != "app":
        status, _payload = await _deliver_to_frame(mid, fdir, APP_PROMOTION_NOTE,
                                                   from_session="mindframe-system")
        delivered = status == 200
    return JSONResponse({"ok": True, "id": mid, "kind": body.kind,
                         "agent_notified": delivered})


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


# --------------------------- export (frozen snapshots) ---------------------------
#
# Export = an immutable, self-contained snapshot of a frame's page, delivered
# as one .html file (host it anywhere, mail it, slack it). The stylesheets are
# inlined, the data plane is baked in (a fetch shim answers /data/<key> GETs
# from the embedded values), and anything that would reach the agent
# (/message, data PUTs) is politely blocked — an exported page never wakes or
# writes anything. One door:
#   GET  /api/frame/<id>/export — download the snapshot as an .html file

_EXPORT_SHIM = """<script>/* mindframe export shim — this is a frozen snapshot */
(function(){
  var D = __MF_DATA__;
  var of = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function(u, o){
    var s = String(u);
    var m = s.match(/\\/data\\/([a-z0-9_-]+)$/);
    if (m) {
      if (!o || !o.method || o.method === 'GET') {
        var v = D[m[1]];
        return Promise.resolve(new Response(JSON.stringify(v === undefined ? null : v),
          { status: v === undefined ? 404 : 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return Promise.resolve(new Response('{"ok":false,"shared":true}', { status: 403 }));
    }
    if (s.indexOf('/message') !== -1 || s.indexOf('/api/') !== -1) {
      try { var b = document.getElementById('mf-export-pill');
            if (b) { b.textContent = 'snapshot — buttons are disabled'; setTimeout(function(){ b.textContent = 'exported from mindframe'; }, 2200); } } catch(e){}
      return Promise.resolve(new Response('{"ok":false,"shared":true}', { status: 403 }));
    }
    return of ? of(u, o) : Promise.reject(new Error('offline snapshot'));
  };
})();</script>"""

_EXPORT_PILL = """<div id="mf-export-pill" style="position:fixed;bottom:12px;right:14px;z-index:9999;\
font:11.5px ui-monospace,monospace;color:#8a8a93;background:rgba(13,13,15,.88);\
border:1px solid #26262c;border-radius:999px;padding:.35rem .8rem;pointer-events:none">\
exported from mindframe</div>"""


def _inline_snapshot_css(html: str) -> str:
    """Replace /frame.css and /tokens.css links with inline <style> so the
    snapshot is self-contained anywhere."""
    try:
        tokens = (WEB_ROOT / "tokens.css").read_text("utf-8")
        framec = (WEB_ROOT / "frame.css").read_text("utf-8").replace('@import url("/tokens.css");', "")
    except OSError:
        return html
    html = re.sub(r'<link[^>]*href="/frame\.css"[^>]*>',
                  "<style>" + tokens + "\n" + framec + "</style>", html, count=1)
    html = re.sub(r'<link[^>]*href="/(frame|tokens)\.css"[^>]*>', "", html)
    return html


def _build_snapshot(fdir: Path) -> str:
    """The frozen, self-contained export HTML for a frame's current page."""
    html = (fdir / "index.html").read_text("utf-8", errors="replace")
    html = _inline_snapshot_css(html)
    data: dict[str, Any] = {}
    ddir = fdir / "data"
    if ddir.is_dir():
        for f in ddir.glob("*.json"):
            try:
                data[f.stem] = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                continue
    payload = json.dumps(data).replace("<", "\\u003c")
    shim = _EXPORT_SHIM.replace("__MF_DATA__", payload)
    if "<head>" in html:
        html = html.replace("<head>", "<head>" + shim, 1)
    else:
        html = shim + html
    if "</body>" in html:
        html = html.replace("</body>", _EXPORT_PILL + "</body>", 1)
    else:
        html += _EXPORT_PILL
    return html


@app.get("/api/frame/{mid}/export")
def export_frame(mid: str) -> Response:
    """Download this frame's page as one self-contained .html file,
    delivered as an attachment."""
    fdir = _frame_dir(mid)
    if fdir is None:
        return JSONResponse({"error": "mindframe not found"}, status_code=404)
    if not (fdir / "index.html").is_file():
        return JSONResponse({"error": "nothing to export yet"}, status_code=409)
    try:
        snapshot = _build_snapshot(fdir)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    title = _page_title(fdir / "index.html") or _read_meta(fdir).get("title") or mid
    fname = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-")[:60] or mid
    return Response(snapshot, media_type="text/html", headers={
        "Content-Disposition": f'attachment; filename="{fname}.html"',
        "Cache-Control": "no-store",
    })


# --- cognition log: tail the agent's Claude transcript (ported from surface/) ---

def _pretty_model(name: str) -> str:
    """Shorten a raw model id for display: drop the `claude-` prefix and the
    `-YYYYMMDD` date suffix, and turn `4-8` back into `4.8`."""
    name = name.replace("claude-", "")
    name = re.sub(r"-\d{8}$", "", name)
    return re.sub(r"(\d)-(\d)", r"\1.\2", name)


def _agent_transcript(fdir: Path, task_id: str) -> Path | None:
    """Newest Claude session JSONL for this mindframe's agent. Handles three
    spawn styles: a normal taskpilot spawn runs with the real $HOME and cwd =
    the frame dir, so Claude stores the transcript at
    ~/.claude/projects/<encoded-cwd>/ (each '/' and '.' becomes '-'); an
    ephemeral *deliverer* (an event agent that drops this frame as its
    deliverable) runs with cwd = its taskpilot task dir; an isolated spawn
    (e.g. setup) keeps it under ~/.taskpilot/<task_id>/.claude/projects/.

    In a named workspace the agent's $HOME is MINDFRAME_HOME (not the operator's
    real home), so the transcript lives under <MINDFRAME_HOME>/.claude/projects/.
    We search both home roots so the cognition log works in either mode."""
    files: list[Path] = []
    # Candidate $HOME roots the agent may have run under (deduped).
    home_roots = [Path.home()]
    if _MINDFRAME_HOME not in home_roots:
        home_roots.append(_MINDFRAME_HOME)
    for cwd in (fdir.resolve(), TASKPILOT_HOME / task_id):
        enc = re.sub(r"[/.]", "-", str(cwd))
        for root in home_roots:
            proj = root / ".claude" / "projects" / enc
            if proj.is_dir():
                files.extend(proj.glob("*.jsonl"))
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
        s = content.strip()
        return [{"kind": "text", "label": s[:2000]}] if s else []
    if not isinstance(content, list):
        return []
    out: list[dict[str, str]] = []
    for b in content:
        t = b.get("type")
        if t == "text" and b.get("text", "").strip():
            out.append({"kind": "text", "label": b["text"].strip()[:2000]})
        elif t == "thinking":
            out.append({"kind": "thinking", "label": "thinking…"})
        elif t == "tool_use":
            inp = b.get("input") or {}
            hint = (inp.get("command") or inp.get("file_path") or inp.get("description")
                    or inp.get("path") or inp.get("url") or "")
            label = b.get("name") or "tool"
            if hint:
                label += ": " + str(hint).replace("\n", " ")[:200]
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
    """Return MCPs for the connections panel.

    In workspace mode (MINDFRAME_HOME has a .claude/settings.json), reads MCPs
    from that file — so the panel shows only what this workspace has explicitly
    configured, starting empty on a fresh workspace. In default mode (no
    workspace settings file), falls back to `claude mcp list` for the full
    global view.
    """
    ws_settings = _MINDFRAME_HOME / ".claude" / "settings.json"
    if ws_settings.is_file():
        try:
            data = json.loads(ws_settings.read_text("utf-8"))
            mcp_servers = data.get("mcpServers", {})
            out = []
            for name in mcp_servers:
                base = name.split(":")[-1] if name.startswith("plugin:") else name
                out.append({
                    "id": base,
                    "name": _CONN_DISPLAY.get(base, base.replace("-", " ").title()),
                    "state": "configured",
                    "bundle": base in _BUNDLE_RUNTIME,
                })
            return out
        except (OSError, ValueError):
            pass

    # Default workspace: fall back to live `claude mcp list`.
    r = _conn_run(["claude", "mcp", "list"], timeout=45)
    out = []
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
    """Directories Claude Code loads skills from.

    In workspace mode (MINDFRAME_HOME has a .claude/settings.json), scans only
    the workspace-local skills dir — connections start empty and grow as the
    operator authors them via /mindframe:connect. In default mode, scans the
    global user-scope skills dir plus installed-plugin skills.
    """
    ws_settings = _MINDFRAME_HOME / ".claude" / "settings.json"
    if ws_settings.is_file():
        # Workspace mode: only workspace-local connector skills
        return [_MINDFRAME_HOME / ".claude" / "skills"]

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
        conns.append({"id": m["id"], "kind": "mcp", "name": m["name"], "state": m["state"]})

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
            "state": "unprobed",
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


# --------------------------- watches ---------------------------
#
# A WATCH is the operator-facing bundle of: a dispatcher route (when), a
# recipe (what), and the ephemeral runs it spawns (results, delivered as
# frames). The operator never edits recipe.yaml or channels.yaml directly —
# each watch has ONE singleton frame (id: watch-<recipe>) whose agent manages
# it: shows trigger + behavior + recent runs, and edits the config on
# instruction, drawing a pending action and waiting for approval.

WATCH_BRIEF = """You are the manager of ONE watch — an automation the operator owns. The watch \
is '{rid}': recipe at ~/.dispatcher/recipes/{rid}/ (recipe.yaml + brief.json) \
plus any routes targeting spawn:{rid} in ~/.dispatcher/channels.yaml. You own \
exactly ONE file, your page:

    {index}

YOUR JOB
  1. READ the recipe, its brief, and the channels.yaml routes. Also look at \
recent runs (task rows named like {rid}-* via `curl -s http://127.0.0.1:8912/tasks` \
or ~/.taskpilot/) and recent deliverables (frames under ~/.mindframe/frames/ \
whose meta.json origin.watch == "{rid}").
  2. COMPOSE your page as the watch's home: what triggers it (in plain words), \
what it does, whether it is currently wired (route present) or unwired, its \
recent runs and deliverables (link deliverable frames as /m/<id>), and any \
problems you can see.
  3. The operator CHANGES the watch by talking to you. When asked to change \
behavior (different trigger, different brief, pause, resume): draft the exact \
config edit, show it on the page as a PENDING ACTION with the diff and an \
explicit approval button, and only apply it to the files after the operator \
approves (button click or message). Pausing = removing/commenting its route \
in channels.yaml; the recipe stays.

PAGE RULES
  - One complete HTML document; <link rel="stylesheet" href="/frame.css"> \
first in <head> (the design system — use .card/.pill/.label/.pending-action), \
viewport meta, <meta name="mf-patch" content="safe">; no emoji. Prefer Edit \
for updates; full Write only for recomposition.
  - Buttons message you (swap /page for /message on your own URL):
      <button onclick="fetch(location.pathname.replace('/page','/message'),\
{{method:'POST',headers:{{'Content-Type':'application/json'}},\
body:JSON.stringify({{text:'A CLEAR INSTRUCTION TO YOU'}})}})\
.then(function(){{this.disabled=true;this.textContent='on it…'}}.bind(this))">Label</button>
  - NEVER declare yourself done; end with the watch's current state and a \
forward question.

Compose your watch home page now."""


def _paused_triggers() -> dict[str, list[str]]:
    """Recipe name -> (source/event_type) trigger strings parked under the
    channels.yaml `paused_routes:` key. The dispatcher reads only `routes:`,
    so a parked route is inert — that's what pausing IS."""
    chan = _load_yaml(DISPATCHER_HOME / "channels.yaml") or {}
    out: dict[str, list[str]] = {}
    for r in (chan.get("paused_routes") or []):
        if isinstance(r, dict):
            t = _split_target(r.get("target", ""))
            if t["kind"] == "spawn":
                out.setdefault(t["name"], []).append(
                    f"{r.get('source', '?')}/{r.get('event_type', '*')}")
    return out


def _watch_runs(rid: str, limit: int = 5) -> list[dict[str, Any]]:
    """Recent taskpilot runs spawned by this watch (task ids are
    '<recipe>-<event id>' by recipe convention)."""
    if not TASKPILOT_DB.is_file():
        return []
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{TASKPILOT_DB}?mode=ro", uri=True, timeout=3)
        rows = con.execute(
            "SELECT task_id, status, updated_at FROM tasks WHERE task_id LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?", (f"{rid}-%", limit)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    return [{"task_id": r[0], "status": r[1], "updated_at": r[2]} for r in rows]


def _watch_deliveries(rid: str, limit: int = 3) -> list[dict[str, Any]]:
    """This watch's recent delivered frames (newest first, archived included
    so history survives the inbox)."""
    out = []
    if not FRAMES_ROOT.is_dir():
        return out
    for fdir in FRAMES_ROOT.iterdir():
        if not fdir.is_dir() or not (fdir / "index.html").is_file():
            continue
        meta = _read_meta(fdir)
        origin = meta.get("origin") if isinstance(meta.get("origin"), dict) else {}
        if meta.get("kind") == "delivered" and origin.get("watch") == rid:
            try:
                mt = int((fdir / "index.html").stat().st_mtime * 1000)
            except OSError:
                mt = 0
            out.append({"id": fdir.name,
                        "title": _page_title(fdir / "index.html") or meta.get("title") or fdir.name,
                        "archived": bool(meta.get("archived")), "modified": mt})
    out.sort(key=lambda d: d["modified"], reverse=True)
    return out[:limit]


@app.get("/api/watches")
def list_watches() -> Response:
    """Watches = recipes joined with routes (active + paused), recent runs,
    recent deliveries, and the singleton manager frame. The operator-facing
    automation list — everything a management panel needs in one call."""
    defs = _agent_definitions()
    paused = _paused_triggers()
    out = []
    for d in defs:
        wid = f"watch-{d['id']}"[:64]
        out.append({
            "id": d["id"],
            "name": d["name"],
            "triggered_by": d["triggered_by"],
            "paused_triggers": paused.get(d["id"], []),
            "wired": bool(d["triggered_by"]),
            "paused": not d["triggered_by"] and bool(paused.get(d["id"])),
            "frame_id": wid if (FRAMES_ROOT / wid / "index.html").is_file() else None,
            "runs": _watch_runs(d["id"]),
            "deliveries": _watch_deliveries(d["id"]),
        })
    return JSONResponse({"watches": out})


def _move_watch_routes(rid: str, pause: bool) -> Response:
    """Move this watch's routes between `routes:` (live) and `paused_routes:`
    (inert) in channels.yaml. The file is rewritten via YAML (comments are
    lost — a one-time .bak preserves the original); a backup is also taken
    on every change to .bak-last."""
    try:
        import yaml
    except ImportError:
        return JSONResponse({"error": "PyYAML unavailable"}, status_code=500)
    chan_path = DISPATCHER_HOME / "channels.yaml"
    if not chan_path.is_file():
        return JSONResponse({"error": "no channels.yaml"}, status_code=404)
    data = _load_yaml(chan_path) or {}
    src_key, dst_key = ("routes", "paused_routes") if pause else ("paused_routes", "routes")
    src = [r for r in (data.get(src_key) or []) if isinstance(r, dict)]
    dst = [r for r in (data.get(dst_key) or []) if isinstance(r, dict)]
    moving = [r for r in src if _split_target(r.get("target", "")) == {"kind": "spawn", "name": rid}]
    if not moving:
        return JSONResponse({"error": f"no {'active' if pause else 'paused'} routes for '{rid}'"},
                            status_code=409)
    keep = [r for r in src if r not in moving]
    data[src_key] = keep
    data[dst_key] = dst + moving
    try:
        original = chan_path.read_text("utf-8")
        once = chan_path.with_suffix(".yaml.bak-original")
        if not once.exists():
            once.write_text(original, "utf-8")
        chan_path.with_suffix(".yaml.bak-last").write_text(original, "utf-8")
        chan_path.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "id": rid, "paused": pause, "moved": len(moving)})


@app.post("/api/watches/{rid}/pause")
def pause_watch(rid: str) -> Response:
    """Park this watch's routes — the dispatcher stops firing it. Reversible."""
    return _move_watch_routes(rid, pause=True)


@app.post("/api/watches/{rid}/resume")
def resume_watch(rid: str) -> Response:
    return _move_watch_routes(rid, pause=False)


@app.post("/api/watches/{rid}/open")
async def open_watch(rid: str, background_tasks: BackgroundTasks) -> Response:
    """Open (create-if-missing) the singleton frame that manages this watch.
    Instant, like create: mint + return; the agent spawns in the background.
    Re-opening an existing watch frame just returns its URL — no new agent."""
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,40}$", rid):
        return JSONResponse({"error": "bad watch id"}, status_code=422)
    if not (DISPATCHER_HOME / "recipes" / rid / "recipe.yaml").is_file():
        return JSONResponse({"error": f"no recipe '{rid}'"}, status_code=404)
    wid = f"watch-{rid}"[:64]
    fdir = FRAMES_ROOT / wid
    if (fdir / "index.html").is_file():
        return JSONResponse({"id": wid, "url": f"/m/{wid}", "spawn": "existing"})
    if not await _daemon_reachable():
        return JSONResponse({"error": "taskpilot daemon not reachable"}, status_code=503)
    prompt = WATCH_BRIEF.format(rid=rid, index=str(fdir / "index.html"))
    try:
        fdir = _mint_frame(wid, f"Watch: {rid}", prompt, {"kind": "watch-open"})
    except FileExistsError:
        return JSONResponse({"id": wid, "url": f"/m/{wid}", "spawn": "existing"})
    except OSError as e:
        return JSONResponse({"error": f"filesystem error: {e}"}, status_code=500)
    meta = _read_meta(fdir)
    meta["kind"] = "watch"
    meta["watch"] = rid
    _write_meta(fdir, meta)
    background_tasks.add_task(_spawn_frame_bg, wid, fdir, prompt)
    return JSONResponse({"id": wid, "url": f"/m/{wid}", "spawn": "starting"})


# --------------------------- runs (live agent management) ---------------------------
#
# "What is running right now, and what ran recently" — every taskpilot task,
# classified for the operator: a FRAME agent (interactive, belongs to a dock
# frame), a WATCH run (ephemeral, spawned by a route), or OTHER. Mechanical
# controls only: kill. Behavioral changes happen in the frame/watch surfaces.


def _frame_task_index() -> dict[str, str]:
    """task_id -> frame_id for every frame on disk."""
    out: dict[str, str] = {}
    if not FRAMES_ROOT.is_dir():
        return out
    for fdir in FRAMES_ROOT.iterdir():
        if fdir.is_dir() and (fdir / "index.html").is_file():
            out[_frame_task_id(fdir.name, fdir)] = fdir.name
    return out


@app.get("/api/runs")
def list_runs() -> Response:
    """Live + recent (48h) taskpilot tasks, classified. Live = tmux exists."""
    if not TASKPILOT_DB.is_file():
        return JSONResponse({"runs": [], "taskpilot_present": False})
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{TASKPILOT_DB}?mode=ro", uri=True, timeout=3)
        rows = con.execute(
            "SELECT task_id, name, status, updated_at FROM tasks "
            "ORDER BY updated_at DESC LIMIT 200").fetchall()
        con.close()
    except sqlite3.Error:
        return JSONResponse({"runs": [], "taskpilot_present": False})
    live = _live_tmux_cached()
    frame_of = _frame_task_index()
    recipes = set()
    rdir = DISPATCHER_HOME / "recipes"
    if rdir.is_dir():
        recipes = {d.name for d in rdir.iterdir() if d.is_dir()}
    now = time.time()
    out = []
    for task_id, name, status, updated_at in rows:
        try:
            dt = datetime.fromisoformat(str(updated_at).replace(" ", "T"))
            at = dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            at = 0.0
        alive = task_id in live
        if not alive and (now - at) >= _FEED_WINDOW_S:
            continue
        watch = next((r for r in recipes if task_id.startswith(r + "-")), None)
        out.append({
            "task_id": task_id,
            "name": name or task_id,
            "status": status,
            "alive": alive,
            "updated": int(at * 1000),
            "kind": "frame" if task_id in frame_of else "watch-run" if watch else "task",
            "frame_id": frame_of.get(task_id),
            "watch": watch,
        })
    out.sort(key=lambda r: (not r["alive"], -r["updated"]))
    return JSONResponse({"runs": out, "taskpilot_present": True})


@app.post("/api/runs/{task_id}/stop")
async def stop_run(task_id: str) -> Response:
    """Kill a run — proxies taskpilot's idempotent stop."""
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", task_id):
        return JSONResponse({"error": "bad task id"}, status_code=422)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{TASKPILOT_DAEMON}/tasks/{task_id}/stop")
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"taskpilot daemon unreachable: {e}"}, status_code=502)
    if r.status_code >= 300:
        return JSONResponse({"error": f"daemon {r.status_code}: {r.text[:200]}"}, status_code=502)
    return JSONResponse(r.json())


_FEED_WINDOW_S = 48 * 3600.0


@app.get("/api/activity")
def activity_feed(limit: int = 30) -> Response:
    """The home feed: what happened while you were away, newest first.
    One narrative over three stores that already exist —
      deliveries  (frames with kind=delivered; provenance from meta.origin)
      frame work  (a frame's transcript moved recently = its agent worked)
      runs        (taskpilot rows that are NOT frame agents = watch runs)
    Read-only; window capped at 48h so the feed is news, not archaeology."""
    now = time.time()
    items: list[dict[str, Any]] = []
    frame_ids: set[str] = set()

    if FRAMES_ROOT.is_dir():
        for fdir in FRAMES_ROOT.iterdir():
            if not fdir.is_dir() or not FRAME_ID_RE.match(fdir.name):
                continue
            if not (fdir / "index.html").is_file():
                continue
            frame_ids.add(fdir.name)
            meta = _read_meta(fdir)
            title = _page_title(fdir / "index.html") or meta.get("title") or fdir.name
            origin = meta.get("origin") if isinstance(meta.get("origin"), dict) else {}
            if meta.get("kind") == "delivered":
                try:
                    at = float(origin.get("at_epoch") or (fdir / "index.html").stat().st_mtime)
                except (OSError, ValueError):
                    at = 0.0
                if now - at < _FEED_WINDOW_S:
                    items.append({
                        "at": int(at * 1000), "kind": "delivery",
                        "text": f"{origin.get('watch') or 'a watch'} delivered: {title}",
                        "frame_id": fdir.name,
                    })
            try:
                tp = _agent_transcript(fdir, _frame_task_id(fdir.name, fdir))
                if tp is not None:
                    mt = tp.stat().st_mtime
                    if now - mt < _FEED_WINDOW_S:
                        items.append({
                            "at": int(mt * 1000), "kind": "frame",
                            "text": f"worked: {title}",
                            "frame_id": fdir.name,
                        })
            except OSError:
                pass

    # Watch runs: taskpilot tasks that aren't frame agents.
    if TASKPILOT_DB.is_file():
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{TASKPILOT_DB}?mode=ro", uri=True, timeout=3)
            rows = con.execute(
                "SELECT task_id, name, status, updated_at FROM tasks "
                "ORDER BY updated_at DESC LIMIT 100").fetchall()
            con.close()
        except sqlite3.Error:
            rows = []
        for task_id, name, status, updated_at in rows:
            if task_id in frame_ids:
                continue
            try:
                dt = datetime.fromisoformat(str(updated_at).replace(" ", "T"))
                at = dt.replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
            if now - at >= _FEED_WINDOW_S:
                continue
            items.append({
                "at": int(at * 1000), "kind": "run",
                "text": f"run: {name or task_id} · {status}",
                "task_id": task_id,
            })

    items.sort(key=lambda i: i["at"], reverse=True)
    return JSONResponse({"items": items[:limit]})


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
