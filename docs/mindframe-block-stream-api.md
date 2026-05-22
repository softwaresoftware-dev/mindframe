# Mindframe block-stream API

Spec for the new conversational modality. **A mindframe** is a focused, agent-driven, rich-block conversation. The block-stream API is how agents author them, how the SPA renders them, and how user actions flow back.

Status: spec, not implementation. The current panes lane (shipped 2026-05-21, commit `6b9d26a`) is ~30% of this. Anything not in the panes lane is still to be built.

## Concepts

- **A mindframe** — a sequence of blocks plus metadata. Identified by an 8-10 char URL-safe id (base62). Owned by one taskpilot agent session.
- **The home** — a different surface (not specced here; closer to taskboard's domain). Surfaces external signals and offers "open a mindframe on this" affordances.
- **Block** — one rich-content message in the conversation. Append-only. The agent and the user both produce blocks (user via button clicks / form submits).
- **Channel** — out of scope; the bus that carries events. Mindframe uses session-bridge for in-frame events and the dispatcher for cross-frame / external events.

## Storage layout

Everything lives under a single root:

```
~/.mindframe/frames/
  <mindframe-id>/
    meta.json             # title, status, agent session id, spawned_by, timestamps
    blocks.jsonl          # append-only block stream — one JSON object per line
    custom/               # optional sibling files (custom-html sources, images, etc.)
      chart-001.html
      ...
```

Why JSONL: append-only is trivial (`>> blocks.jsonl`), tail-friendly for the server, replayable, debuggable with `cat`/`jq`, no parser state across lines.

`<mindframe-id>` is generated at spawn time. Format: 10 base62 chars (`mindframe.id.generate()` helper); short enough for URLs (`/m/<id>`), unique enough for the deployment.

The current panes implementation stores HTML at `<dashboard>/artifacts/<sid>/latest.html`. Migration path: blocks.jsonl lives alongside it; existing artifact files become referenceable from `custom-html` blocks.

## Block schema

Every block has the common envelope:

```json
{
  "id": "01J9V3X7K8N...",
  "ts": 1779461221126,
  "author": "agent" | "user" | "system",
  "type": "<one of the types below>"
}
```

- `id` — ULID assigned by the writer (the agent's helper script or the server when it ingests a user action). Sortable, ~26 chars.
- `ts` — epoch ms at write time. Redundant with ULID but explicit.
- `author` — `agent` (agent-authored content), `user` (button click or form submit), `system` (lifecycle events, errors).
- Type-specific fields below.

### `text`

Markdown body. The renderer applies sensible defaults (GFM, fenced code, tables, no script).

```json
{ "type": "text", "markdown": "**Found 3 OOMKilled events** in `payments-api` in the last hour." }
```

### `code`

Fenced code with language hint. Rendered with syntax highlighting if the renderer knows the language; plain monospace otherwise.

```json
{ "type": "code", "lang": "yaml", "content": "service: payments-api\nreplicas: 3" }
```

### `image`

```json
{ "type": "image", "src": "https://.../diagram.png", "alt": "system topology", "caption": "optional" }
```

`src` may be absolute or relative to the mindframe's `custom/` directory.

### `url-card`

A previewed link. The server fetches og:title / og:description / og:image at ingest time and caches them in the block (or the agent can fill them in directly).

```json
{
  "type": "url-card",
  "url": "https://sentry.io/issues/12345",
  "title": "OOMKilled — payments-api/worker-3",
  "summary": "First seen 14:02 UTC. 47 events in 1h.",
  "favicon": "https://sentry.io/favicon.ico"
}
```

### `table`

```json
{
  "type": "table",
  "headers": ["instance", "memory peak", "killed at"],
  "rows": [
    ["worker-3", "1.9 GB", "14:02"],
    ["worker-7", "2.0 GB", "14:09"],
    ["worker-1", "1.8 GB", "14:14"]
  ]
}
```

Renderer applies basic stripe + monospace numerics. No sorting/filtering in POC.

### `button-row`

The interactive primitive. One row of buttons; each fires an event.

```json
{
  "type": "button-row",
  "buttons": [
    {
      "label": "Drill into worker-3",
      "event_type": "investigate-instance",
      "data": { "instance": "worker-3" },
      "style": "primary"
    },
    {
      "label": "Open the OOM runbook",
      "event_type": "open-runbook",
      "data": { "runbook": "oomkilled.md" }
    },
    { "label": "Dismiss", "event_type": "dismiss", "style": "ghost" }
  ]
}
```

Button styles: `primary` (accent), `default`, `danger` (red), `ghost` (text-only). Default is `default`.

When clicked, the SPA records a `user-action` block in the stream and POSTs `/api/frame/<id>/event` with the button's `event_type` and `data`.

### `input`

Form field. Single-field for POC; multi-field forms come later (or use multiple input blocks side-by-side).

```json
{
  "type": "input",
  "field": "text" | "textarea" | "number" | "select",
  "name": "note",
  "label": "Add a note",
  "placeholder": "Optional context...",
  "options": ["one", "two", "three"],    // only for select
  "submit_label": "Send",
  "submit_event_type": "add-note"
}
```

Submit fires an event of type `submit_event_type` with `data: { name: "<name>", value: "<entered>" }`. A `user-action` block records the submission.

### `summary`

A status banner. Used for high-level state at the top or interspersed.

```json
{
  "type": "summary",
  "tone": "info" | "ok" | "warn" | "err",
  "title": "Investigation in progress",
  "body": "Pulling logs for worker-3 from the last 30 minutes."
}
```

### `divider`

Visual break. Free.

```json
{ "type": "divider" }
```

### `custom-html`

Escape hatch. Embeds an iframe pointing at a sibling HTML file. The renderer enforces `sandbox="allow-scripts allow-same-origin"` so the embed can call `parent.mindframe.postEvent(...)` but can't navigate the parent.

```json
{ "type": "custom-html", "src": "custom/chart-001.html", "height": 400 }
```

For complex visualizations, charts, or interactive widgets the structured block types don't cover.

### `user-action` (system-recorded)

Recorded by the server when a button is clicked or an input is submitted. Echoes the action into the stream so the conversation reads truthfully ("user clicked X").

```json
{
  "type": "user-action",
  "author": "user",
  "trigger": "button" | "input-submit",
  "label": "Drill into worker-3",
  "event_type": "investigate-instance",
  "data": { "instance": "worker-3" }
}
```

### `close` (system or agent)

Signals the agent considers the mindframe done. The renderer can collapse + label "complete." Operator may still re-open.

```json
{ "type": "close", "reason": "RCA drafted; awaiting human review.", "links": ["url-card-block-id"] }
```

## Meta.json schema

```json
{
  "id": "abc1234567",
  "title": "OOMKilled in payments-api",
  "status": "active" | "idle" | "complete" | "archived",
  "agent_session": "task-...",
  "created_at": 1779461221126,
  "last_block_at": 1779461230000,
  "spawned_by": {
    "kind": "dispatcher-event" | "home-button" | "manual",
    "source": "sentry",
    "event_type": "issue.created",
    "event_id": "..."
  },
  "tags": ["incident", "payments-api"]
}
```

`title` may be set by the agent in its first block-write cycle (the agent decides what the mindframe is "about" once it has context). Server starts with a placeholder until the agent calls `mindframe.set-title`.

## Endpoints

All endpoints are served by the mindframe dashboard server (the panes-lane server, evolved). Default port 5174.

### `GET /api/frames`

List mindframes. Returns sorted-newest-first.

```json
{
  "frames": [
    {
      "id": "abc1234567",
      "title": "OOMKilled in payments-api",
      "status": "active",
      "block_count": 12,
      "last_block_at": 1779461230000,
      "tags": ["incident"]
    }
  ]
}
```

Query params: `?status=active|idle|complete|archived` (filter), `?limit=50&offset=0`.

### `GET /api/frame/<id>`

Mindframe metadata. Returns the full `meta.json`.

### `GET /api/frame/<id>/blocks?since=<id|ts>`

Returns blocks. `since` is either a ULID (return blocks with id > since) or an epoch ms (return blocks with ts > since). Both are optional; no `since` returns all blocks.

```json
{
  "frame_id": "abc1234567",
  "blocks": [ /* ordered ascending by id */ ],
  "last_block_id": "01J9V3X7K8N..."
}
```

Clients poll this with the last seen `id` for incremental updates.

### `POST /api/frame/<id>/event`

Button click or input submit. Body:

```json
{ "event_type": "investigate-instance", "data": { "instance": "worker-3" } }
```

Server actions, in order:

1. Append a `user-action` block to `blocks.jsonl`.
2. Decide the route:
   - **Default (continue)**: send as a mesh message to the agent session whose name equals the mindframe id (via session-bridge `/messages`). The agent receives it, appends more blocks. No dispatcher involvement.
   - **Branch**: if the button's `event_type` matches a registered "branch" route (in channels.yaml or a mindframe-local routing table), forward to dispatcher `/api/event` with `source: mindframe-branch`, which may spawn a fresh mindframe. Spec the branch table below.

Returns `{ ok: true, mode: "continue"|"branch", routed_to: "<session|mindframe-id>" }`.

### `POST /api/frame/<id>/blocks`

Agent-side append. Either one block or `{blocks: [...]}`. Returns the assigned ids/timestamps. For local agents on the same machine, **direct file write is acceptable in POC** and probably preferred (no HTTP roundtrip) — the helper script does it. The HTTP endpoint exists for remote agents and as a safety valve.

### `POST /api/frame/<id>/meta`

Update title, tags, status. Body is a partial meta object — server merges.

```json
{ "title": "OOMKilled in payments-api worker-3", "status": "idle" }
```

### `POST /api/frames`

Create a new mindframe (manual spawn from the home or external API). Body:

```json
{
  "title": "Investigate payments-api restarts",
  "spawned_by": { "kind": "manual" },
  "spawn_recipe": "<optional dispatcher recipe to launch the agent>"
}
```

Returns `{ id, url, agent_session }`. If `spawn_recipe` is given, the server contacts taskpilot to spawn the agent with `task_name = <id>` so subsequent events route via session-bridge by that name.

### `GET /api/health`

Already exists. Add `frames_count` to the response.

## Agent-side ergonomics

The agent needs friction-free block authoring. A helper script ships with mindframe:

```
$ mindframe-write <id> text -- '**Found 3 OOMKilled events** in `payments-api`.'

$ mindframe-write <id> table \
    --headers '["instance","memory peak"]' \
    --rows    '[["worker-3","1.9 GB"],["worker-7","2.0 GB"]]'

$ mindframe-write <id> button-row --buttons '[
    {"label":"Drill in","event_type":"investigate","data":{"i":"worker-3"}},
    {"label":"Open runbook","event_type":"open-runbook"}
  ]'

$ mindframe-write <id> custom-html --src custom/chart.html --height 400

$ mindframe-set-title <id> "OOMKilled in payments-api worker-3"
$ mindframe-close <id> --reason "RCA drafted"
```

Implementation: a single ~80-line Python CLI that:

1. Validates the block against a schema.
2. Assigns a ULID + ts.
3. Appends one JSON line to `~/.mindframe/frames/<id>/blocks.jsonl`.

Agents discover this via the recipe template — `CLAUDE.md` in the spawn recipe lists the helper commands. The agent doesn't talk to the server; it just writes files. The server tails them.

For prose-heavy blocks, allow `-- @path/to/file.md` so the agent can write the markdown to a temp file (avoids escaping nightmares in shell):

```
$ mindframe-write <id> text -- @/tmp/finding.md
```

## Button → event flow (continue path)

```
Pane: [Drill into worker-3] click
  │
  ▼
SPA (main.js): parent.mindframe.postEvent("investigate-instance", {instance:"worker-3"})
  │  fetch POST /api/frame/<mfid>/event {event_type, data}
  ▼
Mindframe server:
  1. Append user-action block to blocks.jsonl
  2. Look up frame's agent_session in meta.json
  3. session-bridge POST /messages {to: <agent_session>, body: "<event packed as text>"}
  ▼
session-bridge daemon (port 8910)
  │  delivers as a tmux message to the running agent
  ▼
Agent (running in tmux, full Claude Code session)
  1. Receives the message
  2. Reads any context from the vault, MCPs, etc.
  3. Calls mindframe-write <mfid> <type> ... one or more times
  ▼
Server's tailer picks up new lines in blocks.jsonl
  ▼
SPA polls /api/frame/<mfid>/blocks?since=<last>, gets new blocks, renders.
```

No dispatcher involvement. The dispatcher is only for *external* events (Sentry webhook, GitHub webhook, etc.) and for explicit branching (button click that should spawn a *new* mindframe rather than continue this one).

## Button → event flow (branch path)

Branching means "this button click should spawn a new mindframe, not continue the current one." Use case: a "investigate parent service too" button on a child-service mindframe.

```
Pane: [Investigate parent service] click
  │
  ▼
SPA: parent.mindframe.postEvent("branch:investigate-service", {service: "payments-api"})
  │  fetch POST /api/frame/<mfid>/event {event_type, data}
  ▼
Mindframe server:
  1. Append user-action block to current frame
  2. event_type starts with "branch:" → branch route
  3. dispatcher POST /api/event {source: "mindframe-branch", event_type: "investigate-service", data}
  ▼
Dispatcher (port 8911)
  │  looks up channels.yaml: source=mindframe-branch + event_type=investigate-service → spawn:investigate-service-recipe
  ▼
Spawn recipe:
  1. Generates new mindframe-id
  2. Creates ~/.mindframe/frames/<new-id>/{meta.json, blocks.jsonl}
  3. Launches taskpilot agent with task_name=<new-id>, brief carrying the data
  4. Agent's first action: mindframe-write <new-id> summary --title "..."
  ▼
Optional: server appends a url-card block to the original frame linking to the new one.
```

Branch convention: `event_type` starts with `branch:` to mark intent. Easier to read than a separate field.

## Lifecycle

```
spawn ──▶ active ──▶ idle (no blocks for 15 min) ──▶ active (block written) ──▶ ...
                  │
                  └▶ complete (close block emitted) ──▶ archived (after 30/60 days)
```

Status transitions:

- **active** — has open agent session, blocks streaming
- **idle** — no new blocks for 15 min, agent session still alive (it may still be working but quietly)
- **complete** — `close` block emitted (by agent or operator); agent session may be killed (configurable)
- **archived** — moved to `~/.mindframe/frames/archive/<id>/` after 30/60 days; share URLs continue to work

Operator can close a mindframe manually from the UI ("close" affordance on the mindframe header). This emits a system-authored `close` block.

## Auth

POC: single-user, localhost only. No auth on any endpoint.

Future:

- Each mindframe URL is publicly guessable (it's a 10-char id) but no auth means no real protection. Add bearer auth at the dashboard server (single token per deployment) when mindframes get exposed via the `deploy` capability.
- Share URLs (`/s/<id>`, retained) are intentionally public — the existing pattern.
- Action buttons fire events without auth at the dashboard server but the *dispatcher* requires the bearer (which the server holds). So a malicious page that knows a mindframe-id could fire events but not bypass dispatcher routing.

## Iframe sandboxing

Pane iframes (`<iframe src="/artifacts/.../" sandbox="...">`) and `custom-html` blocks both get:

```
sandbox="allow-scripts allow-same-origin"
```

`allow-scripts` because the embedded HTML needs `parent.mindframe.postEvent` to work. `allow-same-origin` because the iframe lives at the same origin as the SPA. No `allow-popups`, `allow-top-navigation`, `allow-forms` (forms route through the input block type, not free-form HTML forms).

## Polling cadence

POC: SPA polls `/api/frame/<id>/blocks?since=<last>` every 1.5s when the mindframe is in the foreground tab, every 5s when backgrounded. `/api/frames` (the listing) polls every 5s.

Future: server-sent events on `blocks.jsonl` mtime; WebSocket if cross-frame coordination becomes a thing.

## What survives from the current panes lane

| Current | Becomes |
|---|---|
| `GET /api/panes` (lists all artifact/latest.html) | `GET /api/frames` (lists mindframes by meta.json) |
| `POST /api/dashboard-event` (proxies to dispatcher) | `POST /api/frame/<id>/event` (default routes to session-bridge, "branch:" prefix routes to dispatcher) |
| `parent.mindframe.postEvent(event_type, data)` | unchanged, with id implicit from page URL |
| `POST /api/save` snapshot | unchanged, operates per-mindframe |
| `~/.mindframe/secrets/dispatcher-bearer.token` | unchanged |
| `artifacts/<sid>/latest.html` | becomes a `custom-html` block embedded in `blocks.jsonl` (back-compat: existing artifacts auto-render as a single `custom-html` block) |
| Pane iframe rendering | block-stream rendering with `custom-html` as one of the block types |

## What's new

- `mindframe-write` / `mindframe-set-title` / `mindframe-close` helper CLIs
- `blocks.jsonl` storage + tailer
- Block renderer in the SPA (replaces / wraps the iframe-only renderer)
- `meta.json` per mindframe
- `POST /api/frames` and `POST /api/frame/<id>/meta`
- Branch routing convention (`branch:` prefix) + dispatcher template substitution for `session:{data.mindframe_id}` targets

## Open questions

1. **ULID vs UUID for block ids.** ULID is sortable, which simplifies tailers. UUID4 is in the stdlib. Lean ULID (small dep) for the helper script.
2. **Should `meta.json` be the source of truth for status, or derived from the block stream?** Both ways have appeal — derived avoids dual-write bugs, but JSONL-only means recomputing status on every read. POC: meta.json is the index, blocks.jsonl is the truth.
3. **Multi-agent mindframes.** Can two agents append to the same mindframe? (E.g. a sub-agent the main agent spawned.) Probably yes — `author` field disambiguates. But session-bridge mesh assumes one named recipient. POC: one agent per mindframe.
4. **Mindframe pinning.** The home shows recent mindframes; pinning lets the operator say "always show this one even if it goes idle." Punt to v1.
5. **Block edit / delete.** Append-only is much simpler. Edits could be modeled as new "supersedes" blocks; deletes as "redact" markers. Punt to v1.
6. **Search across mindframes.** SQLite FTS over blocks.jsonl content. Punt to v1.

## Where this gets built

Same place the panes lane already lives: `plugins/frameworks/mindframe/dashboard/`. The dashboard server and SPA grow into this spec. The helper CLI (`mindframe-write`, etc.) ships as a small Python script in `plugins/frameworks/mindframe/bin/` and gets symlinked / aliased into the operator's PATH at install time.

The home surface (curated entry point that surfaces external signals + "open a mindframe on this" affordances) is a separate spec — closest to taskboard's domain.
