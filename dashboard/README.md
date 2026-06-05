# Mindframe — Dashboard (static shell)

A FastAPI server that serves the dashboard SPA (`public/`), exposes generated
artifacts under `artifacts/<sid>/`, and snapshots them to sharable `/s/<id>` URLs.

## Status

**The persistent dashboard agent was removed on 2026-05-21.** The previous
build drove a long-running taskpilot task that composed full HTML dashboards
in response to free-text instructions. That model is being replaced by a
merger with the [taskboard](../../apps/taskboard/) plugin:

- **Static frame** — taskboard supplies the systems/services/agents topology.
- **Ephemeral panes** — dispatcher events spawn per-task agents that write
  pane artifacts into `artifacts/<sid>/`. This server keeps serving them.
- **Buttons fire dispatcher events** — no instruction-box composer.
- **Share + retention** — unchanged; works on whatever artifacts land.

Until that merge lands, the SPA is inert (the composer in `public/` will
error on submit — there's no `/api/run` endpoint anymore). The artifact and
share endpoints still work for any HTML written into `artifacts/<sid>/`.

## What this server still does

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness probe. |
| `GET /artifacts/<sid>/<path>` | Serve an artifact file written by an external producer. |
| `POST /api/save` | Snapshot `artifacts/<sid>/latest.html` to a sharable `/s/<id>` URL. |
| `GET /s/<share_id>` | Serve a saved share (HTML). |
| `GET /api/share/<share_id>` | Share metadata JSON. |
| `GET /<path>` | SPA fallback — serves `public/`. |

## Run

No build step, no frontend toolchain. `public/` is plain HTML/CSS/JS, served
as-is.

```bash
pip install -r server/requirements.txt
python3 server/server.py     # http://127.0.0.1:5174
```

## Environment

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `5174` | Backend port (also serves the UI). |
| `MINDFRAME_CORS_ORIGINS` | _(none)_ | Cross-origin allowlist. Unset by default — the UI is same-origin, so CORS is only needed for a separate-origin frontend. |

## Files

| File | What |
|---|---|
| `server/server.py` | FastAPI server — serves artifacts, shares, SPA. No agent integration. |
| `server/requirements.txt` | FastAPI + uvicorn. |
| `public/index.html` | SPA shell (currently inert pending merge). |
| `public/main.js` | Shell JS (currently inert pending merge). |
| `public/style.css` | Shell chrome. |
| `artifacts/<sid>/` | HTML written by external producers (dispatcher-spawned task agents in the merged model). |
| `shares/<id>/` | Saved snapshots. 60-day retention. |
