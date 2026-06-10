# Mindframe — Subsystem Interfaces

This document defines the contracts *between* the bundle's subsystems — the
seams an integrator or a contributor works against. For the components
themselves see [`architecture.md`](architecture.md); for the product see
[`product.md`](product.md).

Each subsystem is reachable only through the interface described here.
Internals behind these seams may change; the contracts should not, without a
version bump.

Contents:

1. [Capability contract](#1-capability-contract)
2. [Dispatcher event API](#2-dispatcher-event-api)
3. [Static routing — `channels.yaml`](#3-static-routing--channelsyaml)
4. [Recipe contract](#4-recipe-contract)
5. [Knowledge base](#5-knowledge-base)
6. [Agent runtime — spawn interface](#6-agent-runtime--spawn-interface)
7. [Session mesh](#7-session-mesh)
8. [Notification capability](#8-notification-capability)
9. [Dashboard app API](#9-dashboard-app-api)

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
| `built_in_capabilities` | Capabilities the plugin satisfies for *itself*, internally — the resolver skips resolution for these. |
| `environment` | Probes (`os`, `binary`, `mcp`, `port`, …) the resolver uses to auto-select among providers. |

**Rule for consumers.** A skill that needs a capability describes the *intent*
and never names a provider:

```markdown
Send the incident summary to the on-call channel.
Use an available skill or tool.
```

The resolver guarantees a provider is loaded; intent-based language keeps the
skill swappable. mindframe's own `requires` are `agent-spawning`,
`session-mesh`, `event-routing`, `browser-automation`, `notification`, and
`daemon`. The Surface and the Knowledge vault are **not** resolved capabilities —
mindframe owns both directly (there is no `knowledge-base` requirement in the
manifest).

Full reference: the `softwaresoftware` plugin's `docs/capability-contracts.md`.

---

## 2. Dispatcher event API

`dispatcher-ingress` is the push path's entry point — a FastAPI service,
bound to localhost (default `127.0.0.1:8911`) and exposed publicly through a
tunnel. All endpoints except `/api/health` require a bearer token.

**Auth.** `Authorization: Bearer <token>`, where the token is the dispatcher's
configured `DISPATCHER_INGEST_TOKEN`. Missing → 401; wrong → 403.

### `POST /api/event` — ingest an event

The main entry point. Request body:

| Field | Type | Notes |
|---|---|---|
| `source` | string, 1–64 chars | Required. The system the event came from (`sentry`, `github`, …). |
| `event_type` | string \| null | Optional. Subtype used for routing. |
| `data` | object \| array \| scalar \| null | The event payload. |

Routing is decided in this order:

1. **Dedupe.** A `(source, event_id)` key seen within the idempotency window
   (`DISPATCHER_DEDUPE_WINDOW_MINUTES`, default 10) short-circuits. `event_id`
   is `data.event_id` / `data.id`, or a payload hash if neither exists.
2. **Static route.** If `channels.yaml` has a matching route (§3), the event is
   forwarded (`session:`) or spawns an agent (`spawn:`) with no LLM in the loop.
3. **LLM fallback.** Otherwise the event is forwarded to the dispatcher Claude
   session, which reads the payload and decides.

Response shapes (all include `"ok": true`):

```jsonc
{ "ok": true, "mode": "static-session", "routed_to": "<session>", "bridge": {…} }
{ "ok": true, "mode": "static-spawn",   "routed_to": "spawn:<recipe>" }
{ "ok": true, "routed_to": "dispatcher", "bridge": {…} }          // LLM fallback
{ "ok": true, "deduped": true, "original_event_id": 41, "routed_to": "…" }
```

A `spawn:` route returns immediately; the spawn runs as a background task and
its outcome lands in the audit log (§ `static-spawn-result`).

### `POST /api/direct/{session}` — explicit forward

Forward text straight to a named mesh session. No routing, no LLM, no dedupe.
Body: `{ "text": string, "source": string }`.

### `GET /api/events` — audit log

Bearer-authed. Most recent first. Query filters, AND-combined: `status`,
`source`, `since` (ISO-8601), `limit` (default 50). Every ingest writes a row;
statuses include `forwarded`, `deduped`, `failed`, `spawned`, `spawn-failed`,
`exception`.

### `GET /api/events/summary` — counts by status

Bearer-authed. Returns `{ "<status>": <count>, … }`, optionally `since` a
timestamp. For dashboards that want "failed today: 4" without scanning rows.

### `GET /api/health`

Unauthenticated. Returns `{ "ok": true }`.

---

## 3. Static routing — `channels.yaml`

`channels.yaml` (default `~/.dispatcher/channels.yaml`) is the static fast path
consulted before the LLM dispatcher. It is re-read on every request — edits
take effect without a restart.

```yaml
routes:
  - source: test-stream          # required; exact match
    event_type: calendar-check   # optional; omit to match any event_type
    target: spawn:calendar-reader
    brief:                       # only for spawn: targets — see §4
      output_path: /tmp/calendar-agent-{event_id}.log
      window: 24h
      vault_context: none
      success_criteria: "summary file written and confirmation sent"
```

| Key | Meaning |
|---|---|
| `source` | Exact-match against the event's `source`. |
| `event_type` | Exact-match, or wildcard if omitted. |
| `target` | `session:<name>` — forward to a mesh session. `spawn:<recipe>` — spawn an ephemeral agent from a recipe. |
| `brief` | For `spawn:` targets only. Literal values for the recipe's brief `{{placeholders}}` (§4). |

**First match wins** — order routes specifically-to-generally.

Static routes exist for *mechanical, no-decision* events: heartbeats, smoke
tests, deterministic fan-out. They skip the LLM dispatcher — so a `spawn:`
route has nobody to compose its brief, which is why the route carries a
`brief:` block. Anything that needs a payload-aware decision should be left
unmapped so it falls through to the LLM dispatcher.

---

## 4. Recipe contract

A **recipe** is a directory (default `~/.dispatcher/recipes/<id>/`) defining an
ephemeral agent. Three files:

```
recipes/<id>/
  recipe.yaml    — how to spawn the agent
  brief.json     — the operating-brief template
  CLAUDE.md      — instructions loaded into the agent's context
```

### `recipe.yaml`

| Key | Meaning |
|---|---|
| `task_id_pattern` | Task-id template, e.g. `"calendar-reader-{event_id}"`. |
| `task_name` | Human-readable task name. |
| `kind` | `task` (ephemeral — one run, then exit) or `service` (long-lived). |
| `model` | Model for the spawned agent (`haiku`, `sonnet`, …). |
| `when_to_use` | Hints for the LLM dispatcher when it picks a recipe. |
| `brief_schema` | `required:` and `optional:` lists of brief keys. |
| `plugins` | Installed-plugin marketplace keys to enable. `{base: [...], optional_pool: [...]}` or a flat list. The `base` set is always loaded; the LLM dispatcher may add from `optional_pool`. Static spawns get `base` only. |
| `mcps` | MCP server names to enable (from `~/.claude.json`). Same `{base, optional_pool}`-or-flat-list shape as `plugins`. |
| `channels` | Extra mesh channels beyond session-bridge (attached automatically). |
| `starter_prompt` | The agent's opening prompt. Substitution tokens below. |

`starter_prompt` substitution tokens (single brace), filled by the spawner:

| Token | Filled with |
|---|---|
| `{event_id}` | The event id. |
| `{task_id}` | The slugified task id. |
| `{payload}` | The event payload, pretty-printed JSON. |
| `{brief}` | The composed brief, stringified JSON. |

### `brief.json` and brief composition

`brief.json` is the agent's operating brief — objectives, workflows,
boundaries, and a `context` block. It is a *template*: it contains
`{{placeholder}}` tokens (double brace) that must be filled before the agent
runs.

Composition depends on the path:

- **Semantic path (LLM dispatcher).** The dispatcher reads the event and fills
  the `{{placeholders}}` from the payload and the vault.
- **Static path (`spawn:` route).** No LLM runs, so the `channels.yaml` route's
  `brief:` block (§3) supplies the values.

Composition rules:

- Every `{{placeholder}}` in `brief.json` must be declared in `brief_schema`
  (`required` or `optional`).
- A **required** placeholder with no value is an error — the spawn fails
  loudly rather than handing the agent a literal `{{output_path}}`.
- An **optional** placeholder with no value resolves to an empty string.
- Brief values may themselves contain `{event_id}` / `{task_id}`.

This contract is enforced at runtime by the dispatcher-ingress spawner: a
`spawn:` with an unfilled **required** placeholder fails loudly rather than
handing the agent a literal `{{output_path}}`.

> **Note.** Mindframe ships no recipes of its own — operators author them
> during setup. This section documents the dispatcher seam any future
> event-driven agent plugs into; the recipe/`channels.yaml` contract lives in the
> `dispatcher` provider, not in this plugin.

---

## 5. Knowledge base

The customer vault is a local directory of Markdown notes with YAML
frontmatter — one note per entity, organized by the four layers (Thing,
Event, Knowledge, Process) — plus a `CATALOG.md` index. It is populated at
setup and by mindframe agents as they work, and is **read by grep**, not by embeddings.

The schema is **per-install**. [`kb-schema.md`](kb-schema.md) is the
*library*: the fixed meta-schema (the rules every entity obeys), the core
entities, and the rule for custom entities. The contract
for a *specific* deployment is that vault's own **`schema.yaml` manifest** —
the assembled entity set, generated by `/mindframe:setup`. It records, per
entity type, its layer, identity mode, directory, fields, and foreign keys,
and a `source` (`core` | `custom`).

The interface, then, is two-layer:

- **Fixed** — the meta-schema in `kb-schema.md`. Never changes without a
  version bump. Contributors build against this.
- **Per-vault** — the deployment's `schema.yaml`. Skills read *this* to know
  what entity types exist; they never assume a hardcoded list. A software vault
  has `service`/`repository`; a paper-mill vault has `machine`/`mill` and
  neither of those. Whatever writes the vault (setup's bootstrap, mindframe
  agents) validates against `schema.yaml` at write time — see `kb-schema.md`.

Mindframe agents depend on the grep contract documented per-vault in its
`README.md` — the entity directories and frontmatter keys they can rely on
finding.

---

## 6. Agent runtime — spawn interface

`taskpilot` spawns and supervises agents through its **daemon** on `:8912`.
Both the Event ingress spawn helper and the Surface reach it the same way, over
HTTP.

### `POST /tasks/create_and_spawn`

Define and spawn a task atomically. Idempotent by the task-id slug. Body
(JSON): the agent's `description` (the recipe's substituted `starter_prompt`),
the `name` (task id), an optional `model`, and an optional `cwd`. The daemon
launches a detached tmux session running `claude`, registers the agent's Mesh
channel under the task id, and returns
`{ "ok": true, "task_id": "…", … }` on success or `{ "ok": false, "error": "…" }`
on failure.

### `POST /tasks/<id>/message`

Deliver a message to a running agent. The daemon forwards it to the agent's Mesh
channel (`session-bridge :8910/sessions/<id>/message`) — **messages reach agents
over the Mesh, never by typing into the tmux pane.** The starter prompt is
delivered the same way at spawn time.

Each spawned agent gets a task directory (default `~/.taskpilot/<task_id>/`); its
durable state is the transcript at `~/.claude/projects/<encoded-cwd>/`. taskpilot
inherits the operator's real `~/.claude` environment, so the agent has the
operator's plugins, MCPs, and identity (there is no sandboxed `$HOME` and no
per-spawn `--enabled-plugins`/`--enabled-mcps`).

Every task is **one-shot**: it runs and exits, and `kill` is the only teardown.
The long-lived `kind: service` mode and auto-respawn were removed; a service that
must survive reboots is managed through the `daemon` capability instead.

---

## 7. Session mesh

`session-bridge` is the message bus connecting agents and humans. Every spawned
agent joins the mesh automatically.

Tools exposed to a session:

| Tool | Purpose |
|---|---|
| `sessions` | List mesh members. |
| `message` | Start a conversation with another session. |
| `reply` | Reply within a conversation, by `chat_id`. |

Inbound messages arrive as `<channel>` notifications carrying `from_id`,
`from_name`, and a `chat_id` to reply against. The dispatcher's `session:`
routes and `POST /api/direct/{session}` both deliver through this mesh, and so
does the Agent runtime — `POST :8910/sessions/<id>/message` is how taskpilot
delivers a spawned agent's prompt and every later message.

---

## 8. Notification capability

Mindframe agents typically end by notifying a human. They do this through the
`notification` capability — never a named provider:

```markdown
Post the incident summary to the on-call channel.
Use an available skill or tool.
If no notification tool is available, write the summary to a fallback file.
```

The resolver binds `notification` to whatever fits the environment —
`notify-slack`, `notify-email`, `notify-linux`, and others. The channel target
(which Slack channel, which address) comes from the customer's vault — the
Service or Team note's `slack:` / `notify:` frontmatter — not from the skill.

`notification` is treated as best-effort by agents: they include a fallback-file
path so a run still produces an artifact if no notification provider is
reachable.

---

## 9. Dashboard app API

The mindframe dashboard is a local web app (a Python/FastAPI backend serving an
SPA). It is the one piece of business logic mindframe owns directly. A
**mindframe** is the unit it hosts: a persistent agent that owns one HTML page
it rewrites, plus a message rail and a cognition log. The dashboard mints them,
lists them, and proxies messages to them.

| Endpoint | Purpose |
|---|---|
| `GET /api/frames` | List surface mindframes — the frame dirs holding an `index.html`. |
| `POST /api/frames/create` | Mint a frame dir and spawn its persistent agent through the Agent runtime daemon (`POST :8912/tasks/create_and_spawn`). |
| `GET /m/<id>` | The per-mindframe shell: an iframe over the agent's page + message rail + cognition log. |
| `GET /api/frame/<id>/page`, `/rev` | The agent's current `index.html` and its revision counter (for live reload). |
| `POST /api/frame/<id>/message` | Deliver a message to the mindframe's agent (forwarded to the taskpilot daemon). |
| `GET /api/frame/<id>/activity` | Tail the agent's transcript for the cognition log. |
| `GET /api/vault`, `/api/vault/entries`, `/api/vault/graph` | The single knowledge base at `~/.mindframe/vault`. |
| `GET /api/sources`, `/api/connections` | Configured sources, and connection discovery: MCPs Claude is connected to (minus bundle runtime, except browser-bridge) plus skills carrying a `connection:` fingerprint. Presence only — no live auth probing yet. |
| `GET /api/events`, `/api/agents` | Read-only feeds for the hub's Events and Agents drawers. |
| `POST /api/dashboard-event` | Dispatcher proxy (bearer-authed against `~/.mindframe/secrets/dispatcher-bearer.token`). |
| `GET /api/health` | `{ ok, port, dispatcher_url, dispatcher_bearer_present }`. |
| `/artifacts/<sid>/<path>` | Static artifacts written by an agent. |
| `/` | The SPA home — a hub graph: a central "New" ringed by Mindframes, Knowledge base, Agents, Connections, and Events nodes (each opens a drawer). |

Spawned mindframe agents run as `claude` processes authenticated by the Claude
Code subscription — no `ANTHROPIC_API_KEY`, consistent with the rest of the
bundle. The agent writes its `index.html` with the plain Write tool; the bundle
ships no MCP for this. The dashboard runs as a managed daemon via the `daemon`
capability.
