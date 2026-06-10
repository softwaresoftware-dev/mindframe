# Mindframe — Dashboard (the Surface layer)

The **Surface** layer of the mindframe stack, and the one piece of business
logic mindframe owns directly. A FastAPI server (no build step; `public/` is
plain HTML/CSS/JS) that is the bundle's human-facing home: it hosts every
mindframe, surfaces the single knowledge base, lists connected sources, and
exposes read-only system info (agents, events). It runs as a managed daemon
(the `daemon` capability) for reboot-persistence.

## What a mindframe is

A mindframe is a **surface**: a persistent agent that owns one live HTML page it
rewrites in place, plus a message box, nothing else. The dashboard mints them
(`POST /api/frames/create` spawns the agent through the Agent runtime daemon),
lists them (`/api/frames`), serves each one's shell at `/m/<id>`, and proxies
operator messages to its agent (`POST /api/frame/<id>/message` →
`:8912/tasks/<id>/message` → the agent's Mesh channel).

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness + dispatcher-bearer presence. |
| `GET /api/frames` · `POST /api/frames/create` | List / create surface mindframes. |
| `GET /m/<id>` | A mindframe's surface shell (page + message rail + cognition log). |
| `GET /api/frame/<id>/page` · `/rev` | The agent's current page and its revision counter. |
| `POST /api/frame/<id>/message` · `GET /api/frame/<id>/activity` | Deliver a message; tail the agent's transcript. |
| `POST /api/dashboard-event` | Proxy an action-button event to the dispatcher (server holds the bearer). |
| `GET /api/vault` · `/entries` · `/graph` | The single knowledge-base vault. |
| `GET /api/sources` · `/api/connections` | Known-source catalog + live discovery. |
| `GET /api/events` · `/agents` | Read-only feeds for the hub's Events and Agents drawers. |
| `GET /artifacts/<id>/<path>` | Serve a mindframe's sibling files. |
| `GET /<path>` | SPA fallback — serves `public/`. |

## Run

No build step, no frontend toolchain. `public/` is served as-is.

```bash
pip install -r server/requirements.txt
python3 server/server.py     # http://127.0.0.1:5174
```

In a deployment it runs under the `daemon` capability instead, for
reboot-persistence.

## Environment

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `5174` | Backend port (also serves the UI). |
| `MINDFRAME_CORS_ORIGINS` | _(none)_ | Cross-origin allowlist. Unset by default — the UI is same-origin, so CORS is only needed for a separate-origin frontend. |
| `MINDFRAME_FRAMES_ROOT` | `~/.mindframe/frames` | Where surface mindframes live (frame dirs holding an `index.html`). |
| `MINDFRAME_DISPATCHER_URL` | `http://127.0.0.1:8911` | Dispatcher base URL for the `/api/dashboard-event` proxy. |
| `MINDFRAME_TASKPILOT_DAEMON` | `http://127.0.0.1:8912` | Agent-runtime daemon for delivering messages to a mindframe's agent. |

## Files

| File | What |
|---|---|
| `server/server.py` | FastAPI server — surface mindframes, the vault, sources, agents/events, dispatcher proxy. |
| `server/requirements.txt` | FastAPI + uvicorn + httpx + PyYAML. |
| `public/index.html` | SPA shell — the home (`/`) hub graph. |
| `public/main.js` | SPA logic. The home (`/`) is a **hub graph**: a central "New" node ringed by satellites (Mindframes, Knowledge base, Agents, Connections, Events, System); a satellite click opens a drawer, the center spawns a launchpad mindframe (KB-grounded suggestions) in a new tab. Opened via `/mindframe:open` ("open up mindframe"). |
| `public/surface.html` | Per-mindframe shell served at `/m/<id>` (iframe over the agent's page + message rail + cognition log). |
| `public/style.css` | Chrome. |
| `artifacts/<id>/` | Sibling files an agent writes next to its page. |
