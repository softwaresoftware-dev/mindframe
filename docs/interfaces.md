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
`session-mesh`, `knowledge-base`, `event-routing`, `status-dashboard`,
`browser-automation`; `notification` is optional.

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

This contract is enforced in CI by `tests/e2e/test_recipe_contract.py` and at
runtime by the dispatcher-ingress spawner. The shared checker
(`tests/e2e/recipe_contract.py`) is also a CLI:

```
python3 recipe_contract.py <channels.yaml> <recipes_dir>
```

---

## 5. Knowledge base

The customer vault is a git repository of Markdown notes with YAML
frontmatter — one note per entity, organized by the four layers (Thing,
Event, Knowledge, Process) — plus a `CATALOG.md` index and a `librarian`
agent that maintains it. The vault is **read by grep**, not by embeddings.

The schema is **per-install**. [`kb-schema.md`](kb-schema.md) is the
*library*: the fixed meta-schema (the rules every entity obeys), the core
entities, the domain packs, and the rule for custom entities. The contract
for a *specific* deployment is that vault's own **`schema.yaml` manifest** —
the assembled entity set, generated by `/mindframe:setup`. It records, per
entity type, its layer, identity mode, directory, fields, and foreign keys,
and a `source` (`core` | `pack:<name>` | `custom`).

The interface, then, is two-layer:

- **Fixed** — the meta-schema in `kb-schema.md`. Never changes without a
  version bump. Contributors build against this.
- **Per-vault** — the deployment's `schema.yaml`. The librarian, the
  validator, and skills read *this* to know what entity types exist; they
  never assume a hardcoded list. A software vault has `service`/`repository`;
  a paper-mill vault has `machine`/`mill` and neither of those.

Deliverable skills depend on the grep contract documented per-vault in its
`README.md`; the demo vault's contract is pinned by
`tests/e2e/test_vault_fixture.py`.

---

## 6. Agent runtime — spawn interface

`taskpilot` spawns and supervises agents. The event router reaches it through
the **spawner CLI**, which the dispatcher-ingress spawn helper invokes:

```
python spawner_cli.py <description> --name <task_id> \
    [--enabled-plugins a,b] [--enabled-mcps a,b] \
    [--channels a,b] [--model <model>] [--brief <path>]
```

| Argument | Meaning |
|---|---|
| `<description>` | The agent's starter prompt (recipe `starter_prompt`, substituted). |
| `--name` | The task id — slugified, used for the tmux session and task directory. |
| `--enabled-plugins` | Comma-joined installed-plugin keys (the recipe's `plugins` base set). |
| `--enabled-mcps` | Comma-joined MCP server names (the recipe's `mcps` base set). |
| `--channels` | Comma-joined extra mesh channels. |
| `--model` | Model override. |
| `--brief` | Path to the composed brief JSON. |

The CLI blocks until tmux + `claude` are launched (~16 s) and prints a JSON
result: `{ "ok": true, "task_id": "…", … }` on success, `{ "ok": false,
"error": "…" }` on failure. Each spawned agent gets a task directory
(default `~/.taskpilot/<task_id>/`) holding its `brief.json`, prompt, and pane
log.

`kind: task` agents run once and exit; `kind: service` agents are long-lived
and reboot-persistent via the `daemon` capability (systemd on Linux, launchd
on macOS).

---

## 7. Session mesh

`session-bridge` is the message bus connecting agents and humans. Every spawned
agent joins the mesh automatically.

Tools exposed to a session:

| Tool | Purpose |
|---|---|
| `sessions` | List mesh members; filter by namespace or label selector. |
| `message` | Start a conversation with another session. |
| `reply` | Reply within a conversation, by `chat_id`. |
| `broadcast` | Message every session matching a selector (e.g. `kind:service`). |
| `label` | Tag a session with `key:value` labels. |

Inbound messages arrive as `<channel>` notifications carrying `from_id`,
`from_name`, and a `chat_id` to reply against. The dispatcher's `session:`
routes and `POST /api/direct/{session}` both deliver through this mesh.

---

## 8. Notification capability

Deliverable skills typically end by notifying a human. They do this through the
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

`notification` is an **optional** capability for the bundle: the deliverable
skills include a fallback-file path so a run still produces an artifact if no
notification provider is installed.

---

## 9. Dashboard app API

The mindframe dashboard is a local web app (a Python/FastAPI backend serving a
built SPA). It is the one piece of business logic mindframe owns directly.

| Endpoint | Purpose |
|---|---|
| `GET /api/run?sid=&msg=` | Server-Sent Events stream. Runs a `claude` subprocess that authors a complete HTML artifact for the instruction; streams progress events; the artifact is written under `artifacts/<sid>/`. |
| `POST /api/save` | Persist the current artifact as a shareable snapshot. |
| `GET /s/<id>` | Serve a saved share by id. |
| `GET /api/share/<id>` | Share metadata. |
| `GET /api/health` | `{ ok, port, agentId, daemons }`. |
| `/` and `/assets/*` | The built SPA (served behind a configurable base path). |

The backend spawns `claude` with `ANTHROPIC_API_KEY` stripped, forcing
subscription auth — consistent with the rest of the bundle. SSE streams are
long-lived; any reverse proxy in front of the app must disable response
buffering.
