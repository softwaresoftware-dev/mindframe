# Mindframe — Generative-UI Dashboard

A single-textbox web app. You type an instruction; the **Mindframe agent**
composes a complete HTML dashboard for it; the page loads in an iframe. Each
further instruction refines or replaces it. Built for the Mindframe demo
(Act 3 — the agent authoring an internal tool live).

## Architecture

```
Browser (vanilla JS)           this server (Python/FastAPI)       taskpilot
─────────────────────          ──────────────────────────        ─────────────
type instruction ─SSE /api/run─▶ POST :8912/tasks/<agent>/message ─▶ daemon ─▶
                                                                    session-bridge ─▶
                                                                    Mindframe agent
                                                                    (tmux, Claude Code)
                                                                         │ writes
   iframe loads  ◀── done {url} ◀── watches artifacts/<sid>/latest.html ◀─┘
```

The dashboard is driven by **one persistent taskpilot task** — the Mindframe
agent. It is a full Claude Code session supervised by the taskpilot daemon.
Every user instruction is delivered to it as a session-bridge mesh message.
The agent reads the customer vault, composes one complete HTML document, and
writes it to `artifacts/<sid>/latest.html`. The server watches that file and,
once it lands and stabilizes, tells the browser to load it.

**No `claude --print`. No Anthropic API key.** The agent authenticates with the
user's Claude subscription the same way any taskpilot task does.

## Prerequisites

The taskpilot and session-bridge daemons must be running:

```bash
# taskpilot daemon (:8912) — supervises the agent
python3 <plugins>/providers/taskpilot/daemon.py --install

# session-bridge daemon (:8910) — message routing
# (installed with the session-bridge plugin)
```

If either is down, every instruction hard-fails with a clear error. There is
no `claude --print` fallback by design.

## Run

There is **no build step and no frontend toolchain**. The UI in `public/` is
plain HTML/CSS/JS, served as-is by the backend.

```bash
pip install -r server/requirements.txt
python3 server/server.py     # http://127.0.0.1:5174  (serves the UI + API; warms the agent on boot)
```

Open <http://127.0.0.1:5174>. The first instruction pays a one-time ~16s agent
spawn if the server didn't already warm it. After that, instructions are
delivered instantly and the agent keeps conversation context across them.

## How a run works

1. Browser opens an SSE connection to `api/run?sid=…&msg=…`.
2. Server preflights the daemons, then `ensure_agent()` — reuses the running
   agent, respawns a dead one, or creates a fresh one via taskpilot's
   `spawner_cli.py`.
3. Server POSTs the instruction to the agent as a mesh message. The message
   carries the absolute `VAULT` path, the absolute `ARTIFACT` path, and a
   `RUN-ID` nonce.
4. Server watches the artifact file and emits coarse `progress` SSE events
   (connecting, picked up, heartbeats). It confirms the agent has picked up
   *this* instruction — by finding the `RUN-ID` in the agent's `last_prompt`
   state — before arming any fail-fast.
5. When the file is written and stable for 3s, the server emits `done {url}`
   and the browser swaps the iframe to it.

## Files

| File | What |
|---|---|
| `server/server.py` | FastAPI server. Drives the taskpilot agent, watches artifacts, serves shares, serves `public/`. |
| `server/requirements.txt` | Backend Python dependencies (FastAPI, uvicorn, httpx). |
| `public/index.html` | The shell — topbar, stage, composer. |
| `public/main.js` | Shell UI — instruction box, spinner + activity log, iframe, share button. Plain JS, no build. |
| `public/style.css` | Shell chrome only. Artifacts bring their own styles. |
| `agent/CLAUDE.md` | The Mindframe agent's persona + grounding rules. Loaded as the agent's project context. |
| `agent/brief.json` | taskpilot operating brief for the agent task. |

## Agent lifecycle

- The agent's task id is cached in `.agent-id` (gitignored).
- On server boot, the server warms the agent so the first instruction is fast.
- If the agent dies, the next instruction respawns it.
- To force a fresh agent: `rm .agent-id` and restart the server, or kill the
  task via `/taskpilot:manage`.

## Sharing

- The **share** button snapshots the current artifact to `shares/<id>/` and
  returns a `/s/<id>` URL (10-char base62 id).
- Shares have a 60-day retention (`MINDFRAME_SHARE_RETENTION_DAYS`). A sweep
  runs on server startup and hourly; `/s/<id>` also returns `410` for an
  unswept-but-expired share.

## Environment

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `5174` | Backend port (also serves the UI) |
| `MINDFRAME_MODEL` | `sonnet` | Model for the agent task |
| `MINDFRAME_TASKPILOT_DAEMON` | `http://127.0.0.1:8912` | taskpilot daemon |
| `MINDFRAME_SESSION_BRIDGE` | `http://127.0.0.1:8910` | session-bridge daemon |
| `MINDFRAME_TASKPILOT_DIR` | `../../../providers/taskpilot` | taskpilot plugin dir (for `spawner_cli.py`) |
| `MINDFRAME_SHARE_RETENTION_DAYS` | `60` | Share retention window |
| `MINDFRAME_CORS_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | Cross-origin allowlist — only relevant if the UI is opened from a different origin; the bundled UI is same-origin. |

## Notes

- The UI is served by the backend, so the browser talks to it same-origin —
  in dev (`:5174` directly) and in prod (behind nginx at `/demo/`). All API
  and asset URLs in `main.js` are relative to the page, so the same files work
  under `/` or `/demo/` with no configuration.
- On load the frontend probes `artifacts/<sid>/latest.html` and restores it
  into the iframe if present — a page refresh doesn't lose the current tool.
- The agent shares conversation context across all browser sessions — this is
  a single-user demo app, not multi-tenant.
- Agent latency: composing a full ~20 KB HTML document takes the agent
  1–4 minutes. The 6-minute run timeout accommodates this.
