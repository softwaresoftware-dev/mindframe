# Mindframe — Dashboard (the Surface layer)

The **Surface** layer of the mindframe stack, and the one piece of business
logic mindframe owns directly. A FastAPI server (no build step; `public/` is
plain HTML/CSS/JS) that hosts every mindframe, surfaces the single knowledge
vault, lists live-discovered connections, and exposes read-only agents/events
feeds. In a deployment it runs as the managed daemon `mindframe-dashboard`
(the `daemon` capability) for reboot-persistence.

## What a mindframe is

A mindframe is a **surface**: a persistent agent that owns one live HTML page
it rewrites in place, plus a message box, nothing else. The dashboard mints
them (`POST /api/frames/create` defines + starts the agent through taskpilot;
task id == frame id), lists them, serves each one's shell
at `/m/<id>`, and proxies operator messages to its agent
(`POST /api/frame/<id>/message` → `:8912/tasks/<id>/message` → the agent's
mesh channel).

## The home hub

`/` is the **calm launcher**: one "What should we work on?" input — typed
text creates a purposeful frame directly, empty opens a launchpad — then the
operator's attention in a few lines (inbox deliveries with provenance, resume,
recent activity), app chips, and a footer opening drawers for frames, agents, runs,
agents, knowledge, and connections.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | `{ok, port, dispatcher_url, dispatcher_bearer_present}`. |
| `GET /api/frames` | List surface mindframes (frame dirs with an `index.html`), newest-activity first. |
| `POST /api/frames/create` | `{prompt, title?}` — mint a frame dir + spawn its persistent agent. |
| `GET /api/frames/activity` | Per-frame `working` flag (transcript written in the last 8s) for the dock. |
| `GET /m/<id>` | A mindframe's surface shell (iframe over the agent's page + message rail + cognition log). |
| `GET /api/frame/<id>/page` | The agent's current page (or a composing placeholder). |
| `GET /api/frame/<id>/rev` | Revision counter = the page file's `mtime_ns`; the shell polls it to reload. |
| `POST /api/frame/<id>/message` | `{text}` — deliver a message to the frame's agent via taskpilot. |
| `GET /api/frame/<id>/activity` | Tail the agent's transcript (`?offset=`, `?file=`); reports cognition events + `mtime`/`model`/`context`. |
| `DELETE /api/frame/<id>` | Kill the frame's agent (best-effort), then remove the frame dir. |
| `POST /api/dashboard-event` | `{event_type, data?}` — proxy to the dispatcher's `/api/event`; the server holds the bearer, the browser never sees it. |
| `GET /api/vault` | The single vault at `~/.mindframe/vault`: counts per entity type, last modified. |
| `GET /api/vault/entries` | Recent entries (`?limit=`, default 50). |
| `GET /api/vault/graph` | Node-link graph from `[[wikilinks]]` + frontmatter foreign keys (`?limit=` nodes, default 500). |
| `GET /api/connections` | Live discovery, presence only: `claude mcp list` + connector-skill `connection:` fingerprint scan, minus the bundle's own runtime (browser-bridge kept). Cached ~30s, background-warmed. |
| `GET /api/events` | Dispatcher routes from the active workspace's `channels.yaml` (`~/.mindframe/dispatcher/`), grouped by source. Read-only. |
| `GET /api/agents` | Standing agents — recipes joined with their routes (live + paused), recent runs, and deliveries. The operator-facing automation list. |
| `POST /api/agents/<id>/pause` · `/resume` · `/open` | Park / unpark an agent's routes (the dispatcher stops / resumes firing it), or open its singleton manager mindframe. |
| `GET /api/runs` | Live + recent (48h) taskpilot runs, classified `frame` / `agent-run` / `task`. `POST /api/runs/<id>/stop` kills one. |
| `GET /api/activity` | The home feed: deliveries, frame work, and agent runs from the last 48h, newest first. Read-only. |
| `GET /artifacts/<id>/<path>` | Sibling files an agent writes next to its `index.html` in the workspace's frame dir (`frames_root()/<id>/`, traversal-checked). |
| `GET /<path>` | SPA fallback — serves `public/`. |

The dispatcher and taskpilot daemons are optional: the dashboard runs without
them; only the endpoints that talk to each fail when that daemon is down.

## Run

No build step, no frontend toolchain. `public/` is served as-is.

```bash
pip install -r server/requirements.txt
python3 server/server.py     # http://127.0.0.1:5174
```

In a deployment it runs under the `daemon` capability instead (a venv at
`~/.mindframe/dashboard-venv`, registered as `mindframe-dashboard`).

## Security posture

The server binds `127.0.0.1` only and has **no authentication, by design**:
any local process — including any agent-authored page it serves — has full API
authority. Never expose it beyond localhost. The dispatcher bearer lives on
disk at `~/.mindframe/secrets/dispatcher-bearer.token` and is read
server-side only.

## Environment

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `5174` | Backend port (also serves the UI). |
| `MINDFRAME_FRAMES_ROOT` | `~/.mindframe/frames` | Where surface mindframes live (frame dirs holding an `index.html`). |
| `MINDFRAME_DISPATCHER_URL` | `http://127.0.0.1:8911` | Dispatcher base URL for the `/api/dashboard-event` proxy. |
| `MINDFRAME_DISPATCHER_BEARER_FILE` | `~/.mindframe/secrets/dispatcher-bearer.token` | File the server reads the dispatcher bearer from. |
| `MINDFRAME_TASKPILOT_DAEMON` | `http://127.0.0.1:8912` | Agent-runtime daemon for spawn/message/kill. |
| `MINDFRAME_TASKPILOT_HOME` | `~/.taskpilot` | Taskpilot home, used to locate isolated-spawn transcripts. |
| `MINDFRAME_DISPATCHER_HOME` | `~/.dispatcher` | Dispatcher home (`channels.yaml`, `recipes/`) for the events/agents feeds. |
| `MINDFRAME_TASKPILOT_DB` | `~/.taskpilot/taskpilot.db` | Taskpilot DB read (read-only) by `/api/runs` and `/api/agents`. |
| `MINDFRAME_CORS_ORIGINS` | _(none)_ | Cross-origin allowlist. Unset by default — the UI is same-origin; CORS middleware mounts only if set. |

The vault path is **not** configurable — `~/.mindframe/vault`, hardcoded.

## Files

| File | What |
|---|---|
| `server/server.py` | The FastAPI server — every endpoint above. |
| `server/requirements.txt` | FastAPI + uvicorn + httpx + PyYAML. |
| `public/index.html` | SPA shell — the home (`/`) calm launcher. |
| `public/main.js` | SPA logic — calm launcher, activity feed, management drawers. |
| `public/surface.html` | Per-mindframe shell served at `/m/<id>`. The "working" indicator derives from the transcript's mtime; Send re-enables when the agent goes idle after replying, or after a 40s no-response warning. |
| `public/style.css` | Chrome. |

| `tests/test_graph.py` | Unit tests for the vault graph builder. |
