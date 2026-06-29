# Mindframe — Subsystem Interfaces

The contracts *between* the bundle's layers — the seams an integrator or
contributor works against. For the layers themselves see
[`architecture.md`](architecture.md); for the product see
[`product.md`](product.md).

Contents:

1. [Capability contract](#1-capability-contract)
2. [Dispatcher event API](#2-dispatcher-event-api)
3. [Static routing — `channels.yaml`](#3-static-routing--channelsyaml)
4. [Recipe contract](#4-recipe-contract)
5. [Knowledge base](#5-knowledge-base)
6. [Agent runtime — taskpilot daemon](#6-agent-runtime--taskpilot-daemon)
7. [Session mesh](#7-session-mesh)
8. [Dashboard app API](#8-dashboard-app-api)
9. [Security posture](#9-security-posture)

---

## 1. Capability contract

The bundle is held together by **capabilities** — abstract names for things a
plugin needs but does not implement itself. The `softwaresoftware` resolver
binds each capability to a **provider** at install time.

A plugin's marketplace entry declares its side of the contract:

| Field | Meaning |
|---|---|
| `requires` | Capabilities that must be satisfied for the plugin to function. |
| `optional` | Capabilities the plugin uses if present, degrades gracefully without. |
| `provides` | Capabilities this plugin satisfies for others. |
| `built_in_capabilities` | Capabilities the plugin satisfies for *itself*, internally. |
| `environment` | Probes (`os`, `binary`, `mcp`, `port`, …) the resolver uses to auto-select among providers. |

**Rule for consumers.** A skill that needs a capability describes the *intent*
and never names a provider — the resolver guarantees a provider is loaded;
intent-based language keeps the skill swappable.

Mindframe's `requires` are exactly: `agent-spawning`, `session-mesh`,
`event-routing`, `browser-automation`, and `daemon`. There is no
`notification` capability in the bundle — an agent that wants to notify a
human uses whatever notification tool happens to be available and falls back
to writing an artifact file if none is. The Surface (the dashboard) and the
Knowledge vault are **not** resolved capabilities — mindframe owns both
directly.

Full reference: the `softwaresoftware` plugin's `docs/capability-contracts.md`.

---

## 2. Dispatcher event API

`dispatcher-ingress` is a FastAPI service on `127.0.0.1:8911`. All endpoints
except `/api/health` require a bearer token.

**Auth.** `Authorization: Bearer <token>`. The dispatcher resolves the token
from `DISPATCHER_INGEST_TOKEN` (direct value) or
`DISPATCHER_INGEST_TOKEN_FILE` (path to a file — mindframe installs drop it at
`~/.mindframe/secrets/dispatcher-bearer.token`). Missing header → 401; wrong
token → 403.

**Ingestion is poll-first, per workspace.** The dispatcher's primary ingestion
path is its poller, which aggregates event-source declarations across every
workspace partition
(`~/.mindframe/workspaces/<id>/.mindframe/dispatcher/event-sources/*.yaml`, via
`DISPATCHER_WORKSPACES_ROOT`), polls each system via an adapter, and **tags each
event with its workspace** — routing then uses that workspace's `channels.yaml`
and spawns with its home. The `POST /api/event` webhook below still works —
mindframe's `/api/dashboard-event` proxy speaks it — but is **deprecated** and
answers with a `Deprecation: true` header. See the dispatcher plugin's own CLAUDE.md.

### `POST /api/event` — ingest an event (deprecated webhook)

| Field | Type | Notes |
|---|---|---|
| `source` | string, 1–64 chars | Required. The system the event came from. |
| `event_type` | string \| null | Optional. Subtype used for routing. |
| `data` | object \| array \| scalar \| null | The event payload. |

Extra fields are rejected (422). Routing is decided in this order:

1. **Dedupe.** A `(source, event_id)` key seen within the idempotency window
   (`DISPATCHER_DEDUPE_WINDOW_MINUTES`, default 10) short-circuits. `event_id`
   is `data.event_id` / `data.id`, or a payload hash if neither exists.
2. **Static route.** If `channels.yaml` matches (§3), the event is forwarded
   to a mesh session (`session:`) or spawns an agent (`spawn:`) — no LLM.
3. **LLM fallback.** Otherwise the event is forwarded to the dispatcher's own
   Claude session, which reads the payload and decides.

Response shapes (all include `"ok": true`):

```jsonc
{ "ok": true, "mode": "static-session", "routed_to": "<session>", "bridge": {…} }
{ "ok": true, "mode": "static-spawn",   "routed_to": "spawn:<recipe>" }
{ "ok": true, "routed_to": "dispatcher", "bridge": {…} }          // LLM fallback
{ "ok": true, "deduped": true, "original_event_id": 41, "routed_to": "…" }
```

A `spawn:` route returns immediately; the spawn runs as a background task and
its outcome lands in the audit log (status `spawned` / `spawn-failed`).

### Other endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/direct/{session}` | bearer | Forward `{text, source}` straight to a named mesh session. No routing, no dedupe. |
| `GET /api/events` | bearer | Audit log, most recent first. Filters (AND-combined): `status`, `source`, `since` (ISO-8601), `limit` (default 50). Statuses: `forwarded`, `deduped`, `failed`, `spawned`, `spawn-failed`, `exception`. |
| `GET /api/events/summary` | bearer | Counts by status, optionally `since` a timestamp. |
| `GET /api/health` | none | `{ "ok": true }`. |

---

## 3. Static routing — `channels.yaml`

`channels.yaml` (per workspace, at
`~/.mindframe/workspaces/<id>/.mindframe/dispatcher/channels.yaml`) is the static
fast path consulted before the LLM dispatcher, against the **originating event's
workspace**. It is re-read on every request — edits take effect without a restart.

```yaml
routes:
  - source: test-stream          # required; exact match
    event_type: calendar-check   # optional; omit to match any event_type
    target: spawn:calendar-reader
    brief:                       # only for spawn: targets — see §4
      output_path: /tmp/calendar-agent-{event_id}.log
      window: 24h
```

| Key | Meaning |
|---|---|
| `source` | Exact-match against the event's `source`. |
| `event_type` | Exact-match, or wildcard if omitted. |
| `target` | `session:<name>` — forward to a mesh session. `spawn:<recipe>` — spawn an ephemeral agent from a recipe. |
| `brief` | For `spawn:` targets only. Literal values for the recipe brief's `{{placeholders}}` (§4). |

**First match wins** — order routes specifically-to-generally. Static routes
exist for mechanical, no-decision events; anything needing a payload-aware
decision should be left unmapped so it falls through to the LLM dispatcher.

---

## 4. Recipe contract

A **recipe** is a directory (per workspace, at
`~/.mindframe/workspaces/<id>/.mindframe/dispatcher/recipes/<id>/`) defining an
ephemeral agent the dispatcher can spawn. The spawn runs with the workspace's
`home` (taskpilot per-task `$HOME`), so the agent runs in that partition. Files:

```
recipes/<id>/
  recipe.yaml    — how to spawn the agent
  brief.json     — the operating-brief template (optional)
  CLAUDE.md      — instructions for the agent (optional)
```

### `recipe.yaml` — fields the spawner reads

The dispatcher's `spawn_helper.py` reads exactly these keys:

| Key | Meaning |
|---|---|
| `task_id_pattern` | Task-id template, e.g. `"calendar-reader-{event_id}"`. Default: `<recipe-id>-{event_id}`. Slugified into the task id. |
| `task_name` | Human-readable name (display only — also read by the dashboard's `/api/agents`). |
| `model` | Model for the spawned agent (`haiku`, `sonnet`, …). |
| `brief_schema` | `required:` and `optional:` lists of brief placeholder keys. |
| `starter_prompt` | The agent's opening prompt. Substitution tokens below. |

Any other key — `kind`, `plugins`, `mcps`, `channels`, `frame` — is **legacy
and ignored**. Every spawned agent inherits the operator's full `~/.claude`
(all installed plugins and MCPs); there is no per-task curation, and the
dispatcher never mints surface frames (the mindframe dashboard does that).

`starter_prompt` substitution tokens (single brace), filled by the spawner:

| Token | Filled with |
|---|---|
| `{event_id}` | The event id. |
| `{task_id}` | The slugified task id. |
| `{payload}` | The event's `data`, pretty-printed JSON. **The only place event data reaches the agent.** |
| `{brief}` | The composed brief, stringified JSON. |

### `brief.json` and brief composition

`brief.json` is a template containing `{{placeholder}}` tokens (double brace),
filled before the agent runs:

- **Static path (`spawn:` route).** The `channels.yaml` route's `brief:` block
  supplies the values. Override values may themselves contain `{event_id}` and
  `{task_id}` — and **only** those; event `data` fields are never substituted
  into brief values (use `{payload}` in `starter_prompt` instead).
- **LLM path.** The dispatcher session composes the brief from the payload.

Composition rules, enforced by the spawner at runtime:

- A placeholder not listed in `brief_schema.optional` is **required**; a
  required placeholder with no value fails the spawn loudly.
- An optional placeholder with no value resolves to an empty string.

> Mindframe ships no recipes — operators author them after setup. This section
> documents the dispatcher seam; the contract lives in the `dispatcher`
> provider, not in this plugin.

---

## 5. Knowledge base

The vault is a local directory of Markdown notes with YAML frontmatter — one
note per entity, organized by the four layers (Thing, Event, Knowledge,
Process) — plus a `CATALOG.md` index. There is one vault **per workspace** at
`~/.mindframe/workspaces/<id>/.mindframe/vault`, populated at setup and by that
workspace's agents as they work, and **read by grep**, not by embeddings.

The schema is **per-install**, two-layered:

- **Fixed** — the meta-schema in [`kb-schema.md`](kb-schema.md) (the rules
  every entity obeys). Contributors build against this.
- **Per-vault** — the deployment's `schema.yaml` manifest, assembled at setup.
  Skills read *this* to know what entity types exist; they never assume a
  hardcoded list. Writers validate against it at write time.

---

## 6. Agent runtime — taskpilot daemon

`taskpilot` spawns and supervises agents through its daemon on
`127.0.0.1:8912`. The dispatcher's spawn helper and the Surface both reach it
the same way, over HTTP.

| Endpoint | Purpose |
|---|---|
| `GET /health` | `{ok, version, running, total}`. |
| `GET /tasks[?status=]`, `GET /tasks/{id}` | List/detail. Status is reconciled against tmux ground truth on every read (`running` + dead tmux → `crashed`), plus live `tmux_alive`/`channel_healthy`. |
| `PUT /tasks/{id}` | Upsert the task definition `{description, name?, cwd?, home?, model?, brief?}`. `home` sets the agent's `$HOME` (its workspace partition) — the key to one taskpilot serving every workspace. Idempotent; the id is a caller-chosen slug. |
| `POST /tasks/{id}/start` | Ensure running (idempotent): no-op if alive, (re)spawn if crashed/stopped. Optional `{prompt}` overrides the starter prompt — the Surface passes a revival brief here when respawning a frame's dead agent. Blocks ~16s on an actual spawn. |
| `POST /tasks/{id}/stop` | Ensure stopped (idempotent). The row survives for a later start. |
| `POST /tasks/{id}/message` | Deliver `{text, from_session?}` to a running agent with **verified delivery**. Errors carry `detail.code`: 409 `agent_not_running` (start + retry), 503 `channel_not_ready` (retry shortly), 502 `delivery_failed`. |
| `DELETE /tasks/{id}` | Stop + delete the task entirely, freeing the id for reuse. |
| `POST /tasks/create_and_spawn` | Composite (PUT + start in one call) for event-driven callers — the dispatcher passes the workspace's `home`; idempotent. |

Each spawned agent runs `claude` in a detached tmux session with `$HOME` set to
its task's `home` — the workspace partition — so it sees that workspace's MCPs,
connector skills, and vault and runs on the subscription login seeded there (the
spawner scrubs `ANTHROPIC_API_KEY`; opt out with `TASKPILOT_KEEP_API_KEY`). Its
durable state is the transcript under `$HOME/.claude/projects/…`. **Messages
reach agents over the Mesh** (`session-bridge :8910/sessions/<id>/message`),
never by typing into the tmux pane; the starter prompt is delivered the same way
at spawn time.

Tasks do not auto-respawn and have no reboot persistence — anything that must
survive reboots unattended runs through the `daemon` capability instead. But
revival is one idempotent call (`POST /tasks/{id}/start`), and the Surface
uses it to respawn a frame's agent on the next operator message after a
reboot or crash.

---

## 7. Session mesh

`session-bridge` is the message bus connecting agents and humans — a localhost
daemon on `:8910` (`GET /health`, `GET /sessions`, `POST /register`,
`POST /sessions/{name_or_id}/message`). Every spawned agent registers a
channel under its task id and joins the mesh automatically.

Tools exposed to a session:

| Tool | Purpose |
|---|---|
| `sessions` | List mesh members. |
| `message` | Start a conversation with another session. |
| `reply` | Reply within a conversation, by `chat_id`. |

Inbound messages arrive as `<channel>` notifications carrying `from_id`,
`from_name`, and a `chat_id` to reply against. The dispatcher's `session:`
routes, `POST /api/direct/{session}`, and taskpilot's message delivery all go
through this mesh.

---

## 8. Dashboard app API

The dashboard (`dashboard/server/server.py`) is the Surface layer — one
**multi-tenant** FastAPI server on `127.0.0.1:5174` (configurable via `PORT`)
serving the portal, every workspace, and every mindframe. A `WorkspaceMiddleware`
strips the `/w/<id>/` prefix and resolves frames/vault per request from
`MINDFRAME_HOME/workspaces/<id>/.mindframe/`. Endpoints below shown as
`/w/<id>/…` are **workspace-scoped** (the SPA sends the prefix; the middleware
strips it); `/`, `/api/health`, and `/api/workspaces` are top-level. Full env-var
table in [`../dashboard/README.md`](../dashboard/README.md).

| Endpoint | Purpose |
|---|---|
| `GET /` | The workspace **portal** — lists workspaces with frame counts + auth status. |
| `GET /api/workspaces` | `{workspaces:[{id,label,frames,auth}]}` — the portal's data (top-level). |
| `GET /api/health` | `{ok, port, dispatcher_url, dispatcher_bearer_present, workspaces[], auth}`. Top-level; `auth` reports the workspace's subscription-login status when called as `/w/<id>/api/health` (`ready` / `expired` / `no-login` / `api-key-conflict`, each with a message + fix). |
| `GET /w/<id>/api/frames` | List the workspace's mindframes (frame dirs under `~/.mindframe/workspaces/<id>/.mindframe/frames` holding an `index.html`), newest-activity first. |
| `POST /w/<id>/api/frames/create` | `{prompt, title?}` — mint a frame dir in the workspace, drop a placeholder, then define + start its agent via taskpilot with `home` = the workspace partition (task_id == frame id). **Pre-spawn auth gate:** 503 with a clear reason if the workspace isn't signed in (instead of a frame whose agent hangs at a login screen). Returns `{id, url: "/w/<id>/m/<frame>", spawn}`. |
| `GET /w/<id>/api/frames/activity` | Per-frame `working` (transcript written recently) + `awake` (agent tmux alive) flags for the dock. |
| `GET /w/<id>/m/<frame>` | The per-mindframe surface shell (iframe over the agent's page + message rail + cognition log). |
| `GET /w/<id>/api/frame/<frame>/page` | The agent's current `index.html` (or a composing placeholder). |
| `GET /w/<id>/api/frame/<frame>/rev` | Revision = the page file's `mtime_ns`; the shell polls it and reloads on change. |
| `POST /w/<id>/api/frame/<frame>/message` | `{text}` — deliver a message to the frame's agent via taskpilot. If the agent died (reboot/crash) it is revived first (started with a revival brief, then delivered); response carries `revived: true`. |
| `GET /w/<id>/api/frame/<frame>/activity` | Tail the agent's transcript for cognition events (`?offset=`, `?file=`); also reports `mtime`, `model`, `context`. |
| `DELETE /w/<id>/api/frame/<frame>` | Tear a mindframe down: delete its task (stops the agent, frees the id; best-effort), then remove the frame dir. |
| `POST /w/<id>/api/dashboard-event` | `{event_type, data?}` — proxy to the dispatcher's `/api/event` with `source: dashboard-button`. The server reads the bearer from `~/.mindframe/secrets/dispatcher-bearer.token`; the browser never sees it. |
| `GET /w/<id>/api/vault[/entries\|/graph]` | The workspace's vault at `~/.mindframe/workspaces/<id>/.mindframe/vault`: counts + last-modified; recent entries; and a node-link graph (edges from `[[wikilinks]]` + frontmatter FKs, capped at `?limit=`). |
| `GET /w/<id>/api/connections` | The workspace's connections — presence only: its `.claude.json` MCPs plus a scan of its `.claude/skills` for connector skills (`SKILL.md` with a `connection:` fingerprint), minus the bundle's own runtime plugins. No auth probing. |
| `GET /api/agents` (+ `POST /api/agents/<id>/{pause,resume,open}`) | The standing agents: recipes joined with routes, recent runs, deliveries; pause/resume/open manage them. |
| `GET /api/events`, `/api/runs`, `/api/activity` | Read-only system views (dispatcher routes, taskpilot runs, recent activity). `/api/agents` + `/api/events` read the active workspace's dispatcher partition (`ws_home()/.mindframe/dispatcher`, via `dispatcher_home()`); `/api/runs`/`/api/activity` read the shared taskpilot state filtered by the workspace's frames. See [`single-stack-contract.md`](single-stack-contract.md). |
| `GET /w/<id>/artifacts/<frame>/<path>` | Sibling files an agent writes next to its `index.html` (traversal-checked). |
| `GET /<path>` | Static / SPA fallback — serves `public/`. |

The dispatcher and taskpilot daemons are optional dependencies: the dashboard
runs without them, and only the endpoints that talk to each fail when its
daemon is unreachable.

Spawned mindframe agents run as `claude` processes authenticated by the Claude
Code subscription — no `ANTHROPIC_API_KEY` anywhere in the bundle. The agent
writes its page with the plain Write tool; there is no page-writing MCP.

---

## 9. Security posture

- **The dashboard binds `127.0.0.1` only and has no authentication, by
  design.** Any local process — including any agent-authored page it serves —
  has full API authority: create, message, and delete mindframes, and post
  dispatcher events through the held bearer. It must never be exposed beyond
  localhost (no reverse proxy, no tunnel) without adding an auth layer first.
- **Mindframe stores no third-party credentials.** Agents act through the
  operator's existing CLIs and MCPs (identity inheritance); tokens stay in
  each provider's own credential store. The only secrets mindframe itself
  creates live under `~/.mindframe/secrets/` (file-handoff: the dispatcher
  bearer token, connector-skill access files), `chmod 700` dir / `600` files,
  never printed to chat.
- **Subscription auth only.** Every `claude` process runs on the Claude Code
  subscription; no `ANTHROPIC_API_KEY` exists anywhere in the bundle.
