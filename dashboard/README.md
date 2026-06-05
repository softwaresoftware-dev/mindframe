# Mindframe — Dashboard

A FastAPI server (no build step; `public/` is plain HTML/CSS/JS) that is the
bundle's human-facing home. It hosts **surface mindframes**, surfaces the single
knowledge base, lists connected sources, and renders a read-only system
overview. It runs as a managed daemon (the `daemon` capability) for
reboot-persistence.

## What a mindframe is

A mindframe is a **surface**: a persistent agent that owns one live HTML page it
rewrites in place, plus a message box — nothing else. The dashboard mints them
(`POST /api/frames/create` spawns the agent), lists them (`/api/frames`), serves
each one's shell at `/m/<id>`, and proxies operator messages to its agent. The
old block-stream / ephemeral-panes / share model was removed (2026-06-04).

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
| `GET /api/events` · `/agents` · `/capabilities` | Read-only system overview. |
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
| `server/server.py` | FastAPI server — surface mindframes, the vault, sources, system overview, dispatcher proxy. |
| `server/requirements.txt` | FastAPI + uvicorn + httpx + PyYAML. |
| `public/index.html` | SPA shell — home (`/`) and system (`/system`) routes. |
| `public/main.js` | SPA logic. |
| `public/surface.html` | Per-mindframe shell served at `/m/<id>` (iframe over the agent's page + message rail + cognition log). |
| `public/style.css` | Chrome. |
| `artifacts/<id>/` | Sibling files an agent writes next to its page. |
