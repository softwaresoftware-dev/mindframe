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

**Working directory convention.** Spawned agents have `cwd = ~/.mindframe/frames/<id>/`. Helper scripts (`mindframe-write`, `mindframe-set-title`, `mindframe-close`) infer the target id from cwd when neither `--id` nor `$MINDFRAME_ID` is set. This is the "you just write" mode the agent operates in by default — see Agent-side ergonomics.

**Directory permissions.** `~/.mindframe/` is `chmod 700`. `~/.mindframe/frames/` and every frame directory inside it inherit `chmod 700`. Mindframes can contain sensitive content (logs, names, customer references); other local users should not be able to read them. This matches `~/.mindframe/secrets/` (already 700). Set on first create; verified by `mindframe-doctor`.

The current panes implementation stores HTML at `<dashboard>/artifacts/<sid>/latest.html`. Migration path: blocks.jsonl lives alongside it; existing artifact files become referenceable from `custom-html` blocks.

## Block schema

Every block has the common envelope:

```json
{
  "id": "01927934-9c8e-7000-89ab-cdef01234567",
  "ts": 1779461221126,
  "author": "agent" | "user" | "system",
  "type": "<one of the types below>"
}
```

- `id` — **UUIDv7** assigned by the writer (the agent's helper script or the server when it ingests a user action). 36-char hex with dashes per RFC 9562; the first 48 bits encode the millisecond Unix timestamp, so block ids sort chronologically as plain strings. Generated via `uuid.uuid7()` from the Python stdlib (added in Python 3.14). Why UUIDv7: `since=<id>` queries reduce to string comparison; the JSONL file is implicitly time-sorted; ids interoperate with any UUID column type, UUID-aware tool, or UUID-aware logger; no third-party dependency, no custom code.
- `ts` — epoch ms at write time. Redundant with the UUIDv7's timestamp prefix but kept explicit because consumers shouldn't have to parse UUIDs to read time.

**Python version requirement**: mindframe requires **Python 3.14+** on the host (released October 2024; ~19 months old at time of writing). The installer probes for it and refuses to proceed otherwise. Older Pythons would need a uuid7 polyfill (`uuid_utils` or similar) — the spec stays stdlib-only for simplicity.
- `author` — `agent` (agent-authored content), `user` (button click or form submit), `system` (lifecycle events, errors).
- Type-specific fields below.

The per-type examples below show **only the type-specific fields** for clarity. Every example block is implicitly wrapped in the common envelope. The helper script and server fill in `id`, `ts`, and `author` automatically.

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

`src` is an absolute URL (`https://...`) or a path relative to the mindframe directory — same rule as `custom-html`. Absolute URLs are loaded by the browser; relative paths resolve to `~/.mindframe/frames/<id>/<src>` and are served by the dashboard.

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

When clicked, the SPA POSTs `/api/frame/<id>/event` with the button's `event_type` and `data`. The server records a `user-action` block in the stream *only after* successfully delivering the event (mesh for continue, dispatcher for branch). A failed click does not pollute the conversation.

**Two entry points for `postEvent`.** Button clicks in native block renderings (button-row, input submit) call `window.mindframe.postEvent(event_type, data)` directly — the SPA owns the click handler. Buttons embedded inside a `custom-html` block live in a sandboxed iframe; they call `parent.mindframe.postEvent(event_type, data)` to cross the iframe boundary. The SPA exposes `mindframe.postEvent` on `window`; the iframe (same-origin) accesses it via `parent`. Both paths converge on the same POST.

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

Submit fires an event of type `submit_event_type` with `data: { name: "<name>", value: "<entered>" }`. Same routing rules as button-row: `submit_event_type` starting with `branch:` triggers the branch path. The server records a `user-action` block on successful delivery (per the button-row rule above).

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

`src` is a path relative to the mindframe's directory — typically `custom/<name>.html`. The agent authors the file directly (its cwd is the mindframe directory, so `cat > custom/chart-001.html <<'EOF' ... EOF` or `Write` tool against that relative path) before appending the `custom-html` block. The server serves anything under `custom/` via `GET /artifacts/<id>/<path>` (existing endpoint, repurposed).

For complex visualizations, charts, or interactive widgets the structured block types don't cover.

### `user-action` (system-recorded)

Recorded by the server when a button is clicked or an input is submitted, *after* the event has been successfully delivered (mesh message accepted for continue, dispatcher event accepted for branch). Echoes the action into the stream so the conversation reads truthfully ("user clicked X").

```json
{
  "type": "user-action",
  "author": "user",
  "trigger": "button" | "input-submit",
  "label": "Drill into worker-3",
  "event_type": "investigate-instance",
  "data": { "instance": "worker-3" },
  "mode": "continue" | "branch"
}
```

`event_type` is recorded **as the operator's click sent it** — including the `branch:` prefix when present. The `mode` field is the server's classification ("continue" / "branch"). This keeps the user-facing record honest while still letting downstream readers reason about routing.

### `supersedes` (agent)

Edits an earlier block by appending a new one that replaces it visually. Append-only stays sacred: the original bytes are never removed; the renderer just shows the new content where the old one was.

```json
{
  "type": "supersedes",
  "supersedes_id": "01927934-9c8e-7000-89ab-cdef01234567",
  "block": { "type": "text", "markdown": "Corrected — the count is 4, not 3." }
}
```

`supersedes_id` is the id of the prior block being replaced. `block` is the new content (any valid block type, including another `supersedes` to walk the chain). Renderer rules:

- Default view: shows the latest in the chain at the prior block's position; original (and intermediate revisions) hidden behind an "edited" indicator with a toggle.
- A `supersedes` of a `supersedes` chains forward — the latest revision wins; the renderer dereferences transitively.
- Only `agent`-authored `supersedes` blocks are honored (the SPA can't issue them; user content is never edited by the system).

### `redact` (agent or system)

Marks an earlier block as hidden. Original bytes stay on disk for forensics; the renderer shows a redaction placeholder with the reason.

```json
{
  "type": "redact",
  "redact_id": "01927934-9c8e-7000-89ab-cdef01234567",
  "reason": "contained PII"
}
```

Renderer rule: a redacted block renders as a struck-through "[redacted: <reason>]" placeholder in place of the original. The original content is not shown by default and is not returned in `GET /api/frame/<id>/blocks` either (the server applies redaction at read time). Operators can inspect raw on-disk content if needed.

Use cases: agent realizes a block leaked a token / customer name / etc.; system applies retention rules ("redact PII older than 90 days" — future feature).

### `close` (system or agent)

Signals the agent considers the mindframe done. The renderer can collapse + label "complete." Operator may still re-open.

```json
{
  "type": "close",
  "reason": "RCA drafted; awaiting human review.",
  "links": ["01927934-9c8e-7000-89ab-cdef01234567"]
}
```

`links` is an array of `url-card` block ids (within this same mindframe) the operator should follow up on — typically a draft PR, an RCA document, a calendar invite. The renderer surfaces these prominently in the "complete" state (e.g. as the only visible content above a "show full history" toggle). Empty array is fine.

A `close` block is a **visual section break, not a hard cutoff**. After reactivation, the renderer keeps showing prior content (the close block stays where it is in the stream) and renders new post-reactivation blocks below it — typically with a "Reactivated at &lt;time&gt;" separator. Multiple close/reactivation cycles produce multiple section breaks in the same stream.

## Meta.json schema

```json
{
  "id": "abc1234567",
  "title": "OOMKilled in payments-api",
  "status": "active" | "idle" | "complete" | "archived",
  "agent_session": "abc1234567",
  "created_at": 1779461221126,
  "last_block_at": 1779461230000,
  "spawned_by": {
    "kind": "dispatcher-event" | "home-button" | "manual" | "branch",
    "source": "sentry",
    "event_type": "issue.created",
    "event_id": "...",
    "parent_mindframe_id": "..."
  },
  "tags": ["incident", "payments-api"],
  "pinned": false
}
```

**`pinned`** — boolean. When true, the home/boards index surfaces this mindframe prominently even if it's idle or complete; idle-archive cleanup skips it. Operator toggles via `POST /api/frame/<id>/meta {pinned: true}` or a UI star icon. The block-stream API stays unaware — pinning is metadata.

`agent_session` is, by convention, identical to the mindframe `id` — `mindframe.spawn()` always calls taskpilot with `--name <id>`. The field is kept distinct in case a future model decouples them (e.g. multi-agent mindframes).

`title` is set by `mindframe.spawn()` at create time from the recipe's `seed_block` templating (see "Seed block is non-negotiable" below). The agent may update it later via `mindframe-set-title`; the operator may rename via `POST /api/frame/<id>/meta`.

**meta.json is mostly a cache.** `status`, `last_block_at`, `block_count` (the `/api/frames` field) are reconstructable from `blocks.jsonl` — the tailer keeps them current and the reconciliation pass repairs drift.

**The exceptions are meta.json-native**: `id`, `created_at`, `spawned_by`, `agent_session`, `tags`, and `title`. These have no authoritative representation in the block stream and are never derived. They live in meta.json. Writers update them under flock per Concurrent write safety. If meta.json is lost, these fields are gone with it — block stream alone won't reconstruct them.

Practical implication: backups should snapshot both `blocks.jsonl` and `meta.json` together. Reconciliation can repair derived state but not native state.

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

Query params: `?status=active|idle|complete|archived` (filter; comma-separated to OR), `?limit=50&offset=0`.

`block_count` is the total line count of `blocks.jsonl` — includes agent-authored, user-action, and system blocks. Renderers wanting "how many agent messages" should call `/api/frame/<id>/blocks` and filter by `author`.

### `GET /api/frame/<id>`

Mindframe metadata. Returns the full `meta.json`.

### `GET /api/frame/<id>/blocks?since=<id|ts>`

Returns blocks. `since` is either a UUIDv7 (return blocks with id > since by string comparison — works because UUIDv7 sorts chronologically) or an epoch ms (return blocks with ts > since). Both are optional; no `since` returns all blocks.

```json
{
  "frame_id": "abc1234567",
  "blocks": [ /* ordered ascending by id */ ],
  "last_block_id": "01927934-9c8e-7000-89ab-cdef01234567"
}
```

Clients poll this with the last seen `id` for incremental updates.

### `POST /api/frame/<id>/event`

Button click or input submit. Body:

```json
{ "event_type": "investigate-instance", "data": { "instance": "worker-3" } }
```

Server actions, in order:

1. Determine the route from the `event_type` prefix:
   - Starts with `branch:` → **branch** path.
   - Anything else → **continue** path.
2. Attempt delivery:
   - **Continue**: POST to session-bridge `/messages` `{to: <agent_session>, body: <see Mesh message format>}`. On failure, return 502 to the SPA *without* appending the user-action block — the operator sees an error and can retry. (Recording a click the agent never received would falsify the conversation.)
   - **Branch**: POST to dispatcher `/api/event` `{source: "mindframe-branch", event_type: "<stripped of branch: prefix>", data: {...data, parent_mindframe_id: <id>}}`. On failure, same — return 502 without recording.
3. On delivery success, append a `user-action` block to `blocks.jsonl`. This is the durable record of "the operator clicked, and the system handled it."
4. (Branch path only) append a `url-card` block to the *parent* mindframe linking to the new child mindframe.

Returns:

```json
// continue
{ "ok": true, "mode": "continue", "routed_to": "<agent-session-id>" }

// branch — includes the new mindframe so the SPA can offer to open it
{ "ok": true, "mode": "branch", "routed_to": "<new-mindframe-id>",
  "new_mindframe": { "id": "...", "url": "/m/...", "title": "..." } }
```

In branch mode, the SPA shows a toast with "opened a new mindframe — [open it]" rather than auto-navigating (auto-nav would surprise an operator who clicked a button mid-investigation).

### `POST /api/frame/<id>/blocks`

Agent-side append. Body is either a single block or `{blocks: [...]}`. The server assigns `id` (UUIDv7), `ts`, and `author` (always `agent` for this endpoint — `user` and `system` author blocks are written by other paths).

Response:

```json
{
  "ok": true,
  "appended": [
    { "id": "01927934-9c8e-7000-89ab-cdef01234567", "ts": 1779461230000 },
    { "id": "01927934-9c8f-7001-89ab-cdef89abcdef", "ts": 1779461230001 }
  ]
}
```

For local agents on the same machine, **direct file write is acceptable in POC** and probably preferred (no HTTP roundtrip) — the helper script does it. The HTTP endpoint exists for remote agents and as a safety valve.

### `POST /api/frame/<id>/meta`

Update title, tags, or status. Body is a partial meta object — server merges.

```json
{ "title": "OOMKilled in payments-api worker-3", "status": "idle" }
```

**Allowed status transitions** (server validates and returns 409 on disallowed):

| From → To | Allowed? | Notes |
|---|---|---|
| `active` → `idle` | yes | operator/tailer can pause |
| `active` → `complete` | yes | operator-driven close (also via close block) |
| `idle` → `active` | yes | reactivation by event |
| `idle` → `complete` | yes | operator-driven close |
| `complete` → `active` | yes | reactivation; should also trigger respawn via spawn(id=…) |
| `complete` → `archived` | yes | automatic after retention window |
| `archived` → anything | no | terminal state |
| any → same | no-op | accepted silently |

`title` and `tags` accept arbitrary values (title truncated to 200 chars).

### `POST /api/frames`

Create a new mindframe (manual spawn from the home or external API). Body:

```json
{
  "title": "Investigate payments-api restarts",
  "spawned_by": { "kind": "manual" },
  "spawn_recipe": "<optional dispatcher recipe to launch the agent>"
}
```

Returns `{ id, url, agent_session }`. Internally calls `mindframe.spawn(recipe=spawn_recipe, ...)` (see Spawning a mindframe). The spawned agent's `task_name` equals the mindframe id so subsequent events route via session-bridge by that name.

### `GET /api/search`

Full-text search across all mindframes' block content.

Query params: `?q=<terms>` (required), `?limit=20`, `?status=active,idle,complete` (default: exclude archived).

Response:

```json
{
  "matches": [
    {
      "mindframe_id": "abc1234567",
      "mindframe_title": "OOMKilled in payments-api",
      "block_id": "01927934-9c8e-7000-89ab-cdef01234567",
      "ts": 1779461230000,
      "author": "agent",
      "snippet": "...the worker exceeded the <mark>OOM</mark> threshold during /export..."
    }
  ]
}
```

Backed by SQLite FTS5 at `~/.mindframe/index.db`. The tailer feeds the index — on each agent/system block append, it extracts searchable text (text.markdown, code.content, table cell contents, summary.title+body, url-card.title+summary) and inserts a row keyed by `(mindframe_id, block_id)`. Redacted and superseded blocks are excluded from the index (the tailer also handles deletion on redact/supersede). Index is opt-in (`MINDFRAME_INDEX=1`); disabled by default in POC since the corpus starts small.

### `GET /api/health`

Already exists. Add `frames_count` and `index_enabled` to the response.

## Page routes (SPA)

The dashboard server's SPA fallback serves the same JS shell for every page route; the SPA reads the URL and renders accordingly.

| URL | Renders |
|---|---|
| `/` | The home surface (curated entry point; specced separately). Until the home lands, `/` renders a **boards index**: fed by `GET /api/frames?limit=50&status=active,idle,complete` (archived excluded by default), lists mindframes newest-first with `{title, status badge, last-activity relative-time, tags}`. Clicking opens `/m/<id>`. Filter chips for `active / idle / complete / archived` (selecting `archived` re-queries with `status=archived`). Status counts in the header. A "+ new mindframe" button posts `/api/frames`. |
| `/m/<id>` | One mindframe — polls `/api/frame/<id>` for meta + `/api/frame/<id>/blocks` for blocks, renders blocks in order. |
| `/s/<id>` | Existing share URL (immutable snapshot). |

The id in `/m/<id>` is the source of truth for which mindframe the SPA acts on — `window.mindframe.postEvent`, snapshots, and polls all scope to that id implicitly.

## Spawning a mindframe

Three paths produce a new mindframe. All converge on one primitive that creates the directory, writes `meta.json`, writes a seed `summary` block, spawns the agent via taskpilot, and returns the URL.

### Path 1 — External event (webhook to dispatcher)

Dispatcher gains a new target type, `spawn-mindframe:<recipe>`:

```yaml
# channels.yaml
- source: sentry
  event_type: issue.created
  target: spawn-mindframe:sentry-incident-recipe
```

When dispatcher matches this route, it calls `mindframe.spawn(...)` (below), which:

1. Mints a new 10-char base62 mindframe id.
2. Creates `~/.mindframe/frames/<id>/` with `meta.json` (status=`active`, `spawned_by` from the event, `agent_session=<id>`) and an empty `blocks.jsonl`.
3. Writes a seed block synchronously so the operator never opens an empty mindframe. The block's fields come from the recipe's `seed_block` config with `event.*` substitution (see Seed-block templating below for the full convention). If the recipe didn't declare one, the default is `{type: "summary", tone: "info", title: "<title from spawn call>", body: "Investigating — context loading."}`.
4. Spawns the recipe via taskpilot with `--name <id>` (so the session id equals the mindframe id) and a brief carrying `{mindframe_id, original_event_data}`. `cwd` is set to `~/.mindframe/frames/<id>/` so helper scripts auto-detect.
5. Returns to the webhook caller: `{ok: true, mindframe_id: <id>, mindframe_url: "<MINDFRAME_PUBLIC_URL>/m/<id>"}`.

The `MINDFRAME_PUBLIC_URL` env var is set at install time — typically `http://127.0.0.1:5174` for local-only deployments, or the operator's deploy-capability hostname (e.g. `https://mindframe.example.com`) when the dashboard is exposed externally. Defaults to `http://127.0.0.1:<PORT>` when unset. Stored in `~/.claude/settings.json` → `pluginConfigs.mindframe.options.public_url`.

The recipe's CLAUDE.md instructs the agent to call the notification provider as one of its first actions; the notification points at `mindframe_url`.

### Path 2 — Manual creation (home button or external API caller)

`POST /api/frames` on the dashboard server. Internally calls the same `mindframe.spawn(...)` primitive. Body carries a recipe name and any seed data.

### Path 3 — Branch (button in an existing mindframe spawns a new one)

Server proxies the event to dispatcher with `source: mindframe-branch`, `event_type: <agent's branch label>` (the `branch:` prefix is stripped — the dispatcher sees the bare label), `data: {...original, parent_mindframe_id: <current id>}`. Dispatcher routes via channels.yaml using the same `spawn-mindframe:` target:

```yaml
# channels.yaml — branch routing convention
- source: mindframe-branch
  event_type: investigate-service       # corresponds to button event_type "branch:investigate-service"
  target: spawn-mindframe:investigate-service-recipe

- source: mindframe-branch
  event_type: triage-incident
  target: spawn-mindframe:incident-triage-recipe
```

Recipe authors are expected to add a branch route to channels.yaml when they introduce a new branch button — `mindframe-write button-row` with `event_type: branch:X` requires a matching `source: mindframe-branch, event_type: X` route. The server warns if a branch event has no route (event lands in dispatcher's unrouted-LLM-fallback, which usually misroutes).

The new mindframe's `meta.json.spawned_by` records `parent_mindframe_id`. The server also appends a `url-card` block to the parent frame linking to the child so the operator can navigate between them.

### The `mindframe.spawn()` primitive

Lives in `plugins/frameworks/mindframe/lib/spawn.py`:

```python
def spawn(
    recipe: str,
    title: str = "",
    spawned_by: dict | None = None,
    brief_extra: dict | None = None,
    event: dict | None = None,     # available to seed_block templating
    id: str | None = None,         # reactivation: reuse an existing id
) -> dict:  # {id, url, agent_session}
```

Both dispatcher's `spawn-mindframe:` target handler and the dashboard server's `POST /api/frames` import this. One code path, three callers.

**Reactivation path.** If `id` is passed, `spawn()` checks for an existing `~/.mindframe/frames/<id>/`:

- **Active session present** — `agent_session` is in taskpilot's live set: `spawn()` is a no-op, returns the existing `{id, url, agent_session}`. No new block written. Caller idempotency: a double-click on a "reactivate" button doesn't spawn two agents.
- **Complete / idle, no live session** — reuses the directory and meta.json, appends a system `summary` block with `tone: "info"` and title "Reactivating — operator returned", launches a fresh taskpilot agent with `task_name=<id>`. Brief carries `{mindframe_id, reactivation: true, last_block_id}` so the agent can read the prior context. meta.json status flips to `active`.

  **During the boot window** — between `spawn()` returning and the new agent's first action — any incoming `/api/frame/<id>/event` POSTs cannot be mesh-delivered (the session isn't registered with session-bridge yet). The dashboard server detects this (session-bridge POST returns 404) and **buffers the events in `~/.mindframe/frames/<id>/pending-events.jsonl`** instead of failing. The fresh agent's CLAUDE.md instructs it to drain this file as one of its first actions: read every line, treat each as a mesh message, then delete the file. Buffer is capped at 50 events; overflow returns 503 to the SPA. Typical boot window: 10-30 seconds — usually empty.
- **Directory missing** — race or bad id; `spawn()` returns 404 to the caller. Does not create.

If `id` is omitted (the normal path), `spawn()` mints a new 10-char base62 id and creates everything fresh.

**Seed block is non-negotiable.** `spawn()` writes the seed `summary` block *before* it returns. The agent's boot latency (taskpilot spawn + Claude Code session warmup + first tool calls) can be 10-30 seconds. Without the seed, the notification fires, the operator clicks through, and they open an empty mindframe — bad first impression that says "broken." With the seed block landed synchronously, opening the URL always shows *something*: a title, a status banner, and a "the agent is preparing the investigation" message. The agent's first real write supersedes this with substantive content.

### Seed-block templating

The seed body is recipe-controlled. Each recipe declares `seed_block` in its config — a block dict where string fields may contain Jinja-style placeholders that `spawn()` evaluates against the `event` dict before writing:

```yaml
# sentry-incident-recipe/recipe.yaml
seed_block:
  type: summary
  tone: warn
  title: "{{ event.data.title }}"
  body: |
    Issue {{ event.data.issue_id }} reported by Sentry.
    Investigating context, recent commits, and runbooks.
```

Allowed substitution surface (POC): `event.data.*`, `event.event_type`, `event.source`, `mindframe.id`, `mindframe.url`. Missing keys render as empty strings (don't fail spawn). No conditionals, no loops, no filters. Anything richer than substitution belongs in the agent's first block-write cycle, not the seed.

Default if `seed_block` is not declared: `{type: "summary", tone: "info", title: "<title from spawn call>", body: "Investigating — context loading."}`.

The seed block's `author` is set to `system` so the conversation reads truthfully ("system seeded this, agent took over from there").

## Multi-agent within a mindframe

A mindframe has one **primary** agent — `meta.json.agent_session` is its task name. The primary is the only session button events mesh-message to. But it can spawn **helpers** via taskpilot directly (background research, code review, log triage) and have them append blocks to the same mindframe.

How helpers contribute:

- Primary calls `taskpilot:spawn-task` with a recipe of its choice. The helper's brief includes `mindframe_id` (the same one) and `helper: true`.
- Helper's CLAUDE.md tells it to write blocks via `mindframe-write --id <mindframe_id>` (it can't infer cwd since it's working elsewhere).
- Helper's `author` field on its blocks is the helper's session id, not `agent`. The renderer attributes blocks with `author: "<session-id>"` distinctly (e.g. a small avatar/label per helper, so the conversation reads "primary said X, log-triage helper said Y").

How helpers communicate with the primary:

- Standard session-bridge mesh — helper finishes, mesh-messages the primary "research complete, see blocks 01J… and 01K…". The primary consumes that the next time it takes a turn.
- Helpers don't receive operator button events; if a button needs to address a specific helper, the primary becomes the router (it receives the event, decides which helper handles it, mesh-messages that helper).

Why this model:

- Reuses primitives — taskpilot spawns helpers, session-bridge routes between them, mindframe-write appends blocks.
- One conversation, multiple voices — closer to how complex investigations actually go (lead investigator + specialists).
- One addressable agent from the operator's perspective — buttons go to the primary, which is consistent with the "one mindframe = one conversation thread" mental model even when the work is parallel underneath.

Lifecycle: when the primary closes (close block + grace expiry), helpers are also killed. Reactivation re-spawns the primary only; helpers are re-spawnable from there if needed.

## Agent-side ergonomics

### How the agent knows its mindframe id

Three layers, checked in order by the helper script:

1. **Explicit flag** — `mindframe-write --id abc1234567 text -- "..."`. Always wins. The positional argument is the block type, never the id.
2. **`MINDFRAME_ID` env var** — set by `mindframe.spawn()` when launching the agent (taskpilot exports it to the spawned process).
3. **Cwd inference** — if the current working directory matches `~/.mindframe/frames/<id>/`, the helper uses that id.

The spawn primitive sets all three (it passes the id in the brief, exports the env var, and `cd`s into the directory). The agent typically doesn't have to think about it — it just runs `mindframe-write text -- "..."` and the helper figures out the id.

The brief.json also carries `mindframe_id` as an explicit key, so the agent can read it directly (e.g. for embedding in URLs or notification bodies).

### Helper script

A helper script ships with mindframe:

```
# Spawned agents have MINDFRAME_ID set and cwd in the frame dir — id can be omitted:

$ mindframe-write text -- '**Found 3 OOMKilled events** in `payments-api`.'

$ mindframe-write table \
    --headers '["instance","memory peak"]' \
    --rows    '[["worker-3","1.9 GB"],["worker-7","2.0 GB"]]'

$ mindframe-write button-row --buttons '[
    {"label":"Drill in","event_type":"investigate","data":{"i":"worker-3"}},
    {"label":"Open runbook","event_type":"open-runbook"}
  ]'

$ mindframe-write custom-html --src custom/chart.html --height 400

$ mindframe-set-title "OOMKilled in payments-api worker-3"
$ mindframe-close --reason "RCA drafted"

# Explicit id form (any agent, any cwd):

$ mindframe-write --id abc1234567 text -- "remote append from a different agent"
```

Implementation: a single ~80-line Python CLI that:

1. Resolves the target id (explicit `--id` arg, then `$MINDFRAME_ID` env, then cwd inference).
2. Validates the block against a schema.
3. Assigns a UUIDv7 (via `uuid.uuid7()`) + ts.
4. Appends one JSON line to `~/.mindframe/frames/<id>/blocks.jsonl` under `flock` (see Concurrent write safety).

Agents discover this via the recipe template — `CLAUDE.md` in the spawn recipe lists the helper commands. The agent doesn't talk to the server; it just writes files. The server tails them.

For prose-heavy blocks, allow `-- @path/to/file.md` so the agent can write the markdown to a temp file (avoids escaping nightmares in shell):

```
$ mindframe-write text -- @/tmp/finding.md
```

## Notifications — pointers, not payloads

Mindframe agents notify the operator via the `notification` capability (provided by `notify-slack`, `notify-email`, `notify-linux`, `notify-termux`, depending on what the resolver wired in). **The notification is always a pointer to the mindframe URL, never the content itself.** The content lives in the mindframe.

Convention for the agent's notification body:

```
[<title from meta.json>] <one-line status>
<mindframe_url>
```

The `one-line status` is the `title` field of the most recent `summary` block the agent has written. If no summary block has been written yet, use the title from the seed summary block. The body is intentionally short — the operator should never need to read it to understand what to do next; they need to know *that* something happened and where to look.

Example: `[OOMKilled in payments-api] First-pass triage ready — 3 instances affected. http://localhost:5174/m/abc1234567`

This is part of the recipe's CLAUDE.md instructions — typically the agent fires the notification as one of its first actions (right after writing the opening blocks), and may fire follow-up notifications if state changes significantly (status crosses warn/err thresholds, agent finishes and closes the mindframe). One mindframe → 1-3 notifications over its lifetime is typical.

Slack/email/desktop become breadcrumbs that point at mindframes; mindframes are where work happens.

## Button → event flow (continue path)

```
Native button click [Drill into worker-3] in the SPA's block renderer
  │  (an iframe-embedded button would call parent.mindframe.postEvent instead)
  ▼
SPA (main.js): window.mindframe.postEvent("investigate-instance", {instance:"worker-3"})
  │  fetch POST /api/frame/<mfid>/event {event_type, data}
  ▼
Mindframe server:
  1. event_type doesn't start with "branch:" → continue path
  2. Look up frame's agent_session in meta.json (== mindframe id, by convention)
  3. session-bridge POST /messages {to: <agent_session>, body: <see Mesh message format>}
  4. On success, append user-action block to blocks.jsonl. (On failure, return 502 — no block appended; operator retries.)
  ▼
session-bridge daemon (port 8910)
  │  delivers as a tmux message to the running agent
  ▼
Agent (running in tmux, full Claude Code session)
  1. Receives the message; parses the fenced event block for structured payload
  2. Reads any context from the vault, MCPs, etc.
  3. Calls `mindframe-write <type> ...` (MINDFRAME_ID is set in its env; --id flag not needed)
  ▼
Server's tailer picks up new lines in blocks.jsonl
  ▼
SPA polls /api/frame/<mfid>/blocks?since=<last>, gets new blocks, renders.
```

No dispatcher involvement. The dispatcher is only for *external* events (Sentry webhook, GitHub webhook, etc.) and for explicit branching (button click that should spawn a *new* mindframe rather than continue this one).

### Mesh message format

When the server mesh-messages the agent, the body is human-readable text the agent can react to naturally, followed by a fenced JSON block the agent's helper (or just `jq`) can parse if structured access is useful. Format:

```
The user clicked "Drill into worker-3" in this mindframe.

```event
{
  "event_type": "investigate-instance",
  "data": { "instance": "worker-3" },
  "block_id": "01927934-9c8e-7000-89ab-cdef01234567"
}
```

Continue the investigation in this mindframe. Append blocks via `mindframe-write`.
```

The first line names the affordance the operator actually saw (the button's `label`), which keeps the conversation legible. The fenced `event` block carries machine-readable payload — `event_type`, `data`, and the `block_id` of the `button-row` block that triggered it (so the agent can refer back).

The recipe's CLAUDE.md teaches the agent: "When you receive a message that contains an `event` fenced block, parse the JSON inside and respond by writing new blocks. The user-action has already been recorded; don't echo it. Continue from where you left off."

Mesh messages are **queued, not interruptive**. If the agent is in the middle of a turn (writing blocks, calling MCPs, reading the vault) when a new event arrives, session-bridge buffers the message and delivers it on the agent's next turn boundary. The operator sees the user-action block immediately (server records on delivery, not on agent-consume) but the agent's response lands once it's free. POC accepts this latency; future versions could surface a "queued — agent busy" hint on recent user-action blocks.

## Button → event flow (branch path)

Branching means "this button click should spawn a new mindframe, not continue the current one." Use case: a "investigate parent service too" button on a child-service mindframe.

```
Pane: [Investigate parent service] click
  │
  ▼
SPA: window.mindframe.postEvent("branch:investigate-service", {service: "payments-api"})
  │  fetch POST /api/frame/<mfid>/event {event_type, data}
  ▼
Mindframe server:
  1. event_type starts with "branch:" → branch path
  2. dispatcher POST /api/event {source: "mindframe-branch", event_type: "investigate-service" (prefix stripped), data: {...data, parent_mindframe_id: <mfid>}}
  3. On success, append user-action block to current frame (and url-card linking to the new child after spawn returns)
  ▼
Dispatcher (port 8911)
  │  channels.yaml: source=mindframe-branch + event_type=investigate-service → spawn-mindframe:investigate-service-recipe
  │  target handler calls mindframe.spawn(recipe, spawned_by={kind:"branch", parent_mindframe_id:<mfid>})
  ▼
mindframe.spawn():
  1. Mints new id, creates ~/.mindframe/frames/<new-id>/{meta.json, blocks.jsonl}
  2. Writes seed summary block ("Investigating...") synchronously
  3. Launches taskpilot agent with task_name=<new-id>, cwd=<new dir>, brief carrying parent id + data
  ▼
Server appends a url-card block to the original frame linking to the new one.
```

Branch convention: `event_type` starts with `branch:` to mark intent. Easier to read than a separate field.

### When to branch vs continue

Default to **continue** (in-frame). Branching is for genuine task separation — when the new work has its own scope, will likely produce its own RCA/PR/handoff, and shouldn't crowd the original mindframe's stream.

| Action | Right shape |
|---|---|
| "Drill into this instance" | Continue — agent appends more blocks to current frame |
| "Show me the runbook" | Continue — agent appends a `text` block or url-card |
| "Filter the table to errors only" | Continue — agent appends a new filtered `table` block |
| "Open the related parent service in a new mindframe" | Branch — new scope, separate work |
| "Triage all incidents in this Sentry project" | Branch — multiple investigations, each its own mindframe |
| "Walk me through PR #789 in a separate context" | Branch — distinct artifact |

Heuristic: would a human open a new tab for this, or stay in the current one? If they'd stay, continue. If they'd open a new tab, branch.

Agents author button labels and `event_type`s; the `branch:` prefix is the agent's deliberate choice. A recipe template can guide common patterns ("for service-investigation, use `branch:investigate-service` so the parent stays focused on its incident").

## Lifecycle

```
spawn ──▶ active ──▶ idle (no agent block for 15 min) ──▶ active (agent block) ──▶ ...
                  │
                  └▶ complete (close block emitted) ──▶ archived (after 30/60 days)
```

Status transitions:

- **active** — has open agent session, agent appending blocks
- **idle** — no *agent-authored* blocks for 15 min. Agent session still alive; may still be working but quietly. User-action blocks alone do **not** reset the idle timer — if the operator clicks a button and the agent hasn't responded yet, the mindframe is still idle (in fact, it's "waiting on the agent" — a sub-state worth surfacing in the UI later but not in the meta.json status enum).
- **complete** — `close` block emitted (by agent or operator); agent session enters its grace window
- **archived** — moved to `~/.mindframe/frames/archive/<id>/` after 30/60 days; share URLs continue to work

Operator can close a mindframe manually from the UI ("close" affordance on the mindframe header). The SPA POSTs `/api/frame/<id>/blocks` with a `close` block authored by `system` (the dashboard server overrides `author` to "system" for any close block coming from a same-origin SPA request without an agent session). Status transition to `complete` then happens via the tailer noticing the close block — one path through the spec, not two.

### Agent session on complete

Default behavior when a mindframe enters `complete`:

- The agent session stays alive for a **1-hour grace window**. During the grace window, the session can be reactivated by re-sending an event to it (operator reopens the mindframe and clicks a button, or types a follow-up). No respawn cost.
- After the grace window, taskpilot terminates the session. The mindframe remains viewable; its blocks.jsonl is immutable from this point unless reactivated.
- **Reactivation after grace** — operator interaction triggers `mindframe.spawn(id=<existing>)`. The three branches of that primitive (active / complete-or-idle / missing) and the boot-window event buffer are documented in detail under "Reactivation path" in the spawn primitive section above.

Configurable per recipe — a heavyweight investigation recipe might want a longer grace (4 hours, say) before tearing down. POC default: 1 hour.

### Stale-state reconciliation

If the taskpilot daemon restarts, the dashboard server restarts, or an agent crashes, a mindframe's meta.json may say `active` while its underlying agent session no longer exists in taskpilot. Stale.

The dashboard server runs a **reconciliation pass** every 60 seconds:

1. Read every `meta.json` with status `active` or `idle`.
2. Query taskpilot `/api/tasks` (or `taskpilot list`) for the set of live session ids.
3. For each mindframe whose `agent_session` is not in the live set: append a `system`-authored `summary` block with `tone: "warn"` and title "Agent session lost — reactivate to continue", and mark `meta.json.status = "idle"`. Don't auto-respawn; reactivation is operator-driven (see Agent session on complete).

Same pass also catches mindframes whose `meta.json.last_block_at` is stale: rebuild that field from `blocks.jsonl` if its line count exceeds the cached `block_count`.

Reconciliation is best-effort — the dashboard server is the only writer of meta.json `status`, so concurrent updates aren't a concern.

## Auth

POC: single-user, localhost only. No auth on any endpoint.

Future:

- Each mindframe URL is publicly guessable (it's a 10-char id) and no auth means no real protection. Add bearer auth at the dashboard server (single token per deployment) when mindframes get exposed via the `deploy` capability.
- Share URLs (`/s/<id>`, retained) are intentionally public — the existing pattern.
- **Threat note for the POC.** Continue-path button clicks do *not* go through the dispatcher — they mesh-message the agent directly. So the dispatcher bearer doesn't protect them. A malicious page that knows a mindframe id and runs in a context with localhost access could fire arbitrary continue-events at the agent. For POC + localhost this is acceptable; for any non-local deployment, dashboard-server bearer auth becomes mandatory.

## Concurrent write safety

`blocks.jsonl` is written by at least two parties: the agent (via `mindframe-write`) and the server (when it records `user-action` blocks on incoming events). On POSIX, an `open(O_APPEND) + write()` of bytes < `PIPE_BUF` (typically 4096 on Linux, 512 on macOS) is atomic — concurrent appenders never interleave bytes. Above that, the kernel splits writes and lines can interleave.

Realistic blocks exceed `PIPE_BUF`: a markdown `text` block with a code fence, a `custom-html` block with embedded HTML, or a `table` with many rows can easily be 8-50 KB.

**Convention: all writers take an advisory exclusive lock on `blocks.jsonl` before appending, release after one full write.** Cross-platform implementations:

- **Linux / macOS** (Python): `fcntl.flock(f, fcntl.LOCK_EX)` around the write. Shell escape: `flock blocks.jsonl -c 'echo "…" >> blocks.jsonl'`.
- **Windows** (Python): `msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, length)` over the byte range being written. No shell `flock`; on Windows operators use the Python helper exclusively.

The mindframe shared library (`lib/locking.py`) exposes one wrapper — `with exclusive_lock(path):` — that selects the right primitive per platform. Server and helper script both use it.

Reads are unlocked — the tailer can read while writers hold the lock; it'll just see lines after the writer releases. The only constraint is that no line is partially written when the lock is released, which the lock + a single buffered `write()` guarantee.

The same convention applies to `meta.json` — read-modify-write under exclusive lock taken on `meta.json` itself: lock, read, mutate the dict in memory, truncate, write, release. Reads outside the lock may see slightly stale state (acceptable per "meta.json is a cache").

## The tailer

The dashboard server runs a tailer per active mindframe (lazy: started on first poll, idle-times-out after no readers for 5 min and re-starts on next poll). The tailer's job:

- Watch `~/.mindframe/frames/<id>/blocks.jsonl` for new lines (Linux `inotify` via `watchdog`, macOS `kqueue`, Windows `ReadDirectoryChangesW`; fallback to polling mtime every 1s).
- For each new line: parse as JSON.
  - **Malformed line** — log a warning, skip. Do not crash the tailer; do not propagate the bad block to clients. The line stays on disk for forensics; the tailer remembers the byte offset and continues past.
  - **Valid block** — update `meta.json.last_block_at` and `meta.json.block_count` (under lock, per Concurrent write safety).
  - **`close` block** — also flip `meta.json.status` to `complete` and start the agent-session grace-window timer (see Agent session on complete).
  - **agent-authored block while status was `idle`** — flip `meta.json.status` back to `active`.
  - **`supersedes` / `redact` block** — update the FTS index: remove the targeted block's text, insert the new content (supersedes) or nothing (redact).
  - **searchable content blocks** (text, code, table, summary, url-card) when `MINDFRAME_INDEX=1` — extract searchable text and insert into the FTS index at `~/.mindframe/index.db` (see `GET /api/search`).

The tailer does not enforce block schema beyond "valid JSON" — schema is the writer's responsibility. The schema lives at `plugins/frameworks/mindframe/lib/block_schema.json`; the helper CLI and the HTTP `/blocks` endpoint validate against it. The tailer is defensive against malformed lines but does not re-validate. A block missing required fields renders blank in the SPA (renderer guards against missing keys); the operator sees an empty card rather than a crash.

**Writers of meta.json's derived fields**: tailer (block events), reconciliation pass (60s sweep), and `mindframe.spawn()` (status flips on spawn/reactivation). All three use the `exclusive_lock(meta_path)` wrapper from Concurrent write safety — last-writer-wins under the lock, no torn writes. The single-writer mental model isn't quite right; the single-locking-discipline model is.

### Idle detection

`active → idle` is **not** the tailer's job (the tailer fires on writes, not on absence). The reconciliation pass (which already runs every 60s) handles it: for each mindframe in `active`, check `now - meta.json.last_block_at`; if > 15 min and the most recent block isn't agent-authored, flip status to `idle`. `idle → active` *is* the tailer's job (it sees the next agent block land).

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
- `blocks.jsonl` storage + tailer + flock convention
- Block renderer in the SPA (replaces / wraps the iframe-only renderer)
- `meta.json` per mindframe (as cache; blocks.jsonl is the truth)
- `POST /api/frames` and `POST /api/frame/<id>/meta`
- `mindframe.spawn()` shared primitive in `lib/spawn.py`
- New dispatcher target type `spawn-mindframe:<recipe>` and seed-block templating
- Branch routing via dispatcher (`source: mindframe-branch`, target `spawn-mindframe:`)
- Continue routing via direct session-bridge mesh from the dashboard server (no dispatcher hop)
- Reactivation path via `mindframe.spawn(id=...)` after grace-window expiry
- Reconciliation pass to detect stale `active` mindframes whose agent session is gone
- SPA page routes `/m/<id>` (mindframe) and `/` (boards index until home lands)
- UUIDv7 block ids (Python 3.14+ stdlib `uuid.uuid7()`, no custom impl)
- `supersedes` and `redact` block types preserving append-only on disk
- `meta.json.pinned` boolean + UI affordance
- Multi-agent within a mindframe — primary + taskpilot-spawned helpers, distinct `author` per session
- `GET /api/search` backed by SQLite FTS5, fed by the tailer (opt-in via `MINDFRAME_INDEX=1`)

## Deferred polish

Implementation details surfaced during spec review but not on the critical path. Address opportunistically as the implementation lands.

- **Server startup**: walk `~/.mindframe/frames/`, register tailers for active/idle, run one reconciliation pass at boot.
- **Per-frame in-memory `id → byte offset` index** so `GET /api/frame/<id>/blocks?since=<id>` is O(1) seek rather than O(N) scan. Important for mindframes that grow past a few hundred blocks.
- **Canonical recipe CLAUDE.md template**, ~50 lines, documenting the agent's standard flow: read brief, write opening blocks, fire notification, loop on mesh messages, eventually emit close. Lives at `plugins/frameworks/mindframe/templates/recipe.CLAUDE.md`.
- **"+ new mindframe" without a recipe** — a blank canvas that lets the operator type the first prompt as an `input` block; the home decides which recipe to spawn (or routes to a generic "scratch" recipe) based on the first message.
- **SPA polling backpressure**: if a poll is in-flight, skip the next tick. Standard pattern; not currently stated.
- **Helper-script error handling contract**: stderr message + exit code 1 on schema validation failure, exit code 2 on missing id (no flag, no env, no cwd match), exit code 3 on lock acquisition timeout.

## Resolved design questions

All six open questions resolved during spec review. Recorded here for posterity; each has a concrete home elsewhere in the spec.

1. ~~ULID vs UUID for block ids.~~ **UUIDv7** (RFC 9562) — available in Python stdlib since 3.14 via `uuid.uuid7()`. Sorts chronologically by string compare, like ULID, but is a standards-track UUID — works with any UUID-aware tooling. Trade: requires Python 3.14+ on the host. See Block schema envelope.
2. ~~`meta.json` source-of-truth vs derived.~~ **Cache; blocks.jsonl is truth**, with named meta.json-native exceptions (id, created_at, spawned_by, agent_session, tags, title, pinned). See Meta.json schema.
3. ~~Multi-agent mindframes.~~ **Primary + helpers** — primary is the only mesh-addressable session; helpers spawn via taskpilot and append blocks attributed to their own session id. See Multi-agent within a mindframe.
4. ~~Mindframe pinning.~~ **Resolved.** `meta.json.pinned` boolean, exposed in `/api/frames`, toggled via `POST /api/frame/<id>/meta`. UI is the home's domain.
5. ~~Block edit / delete.~~ **Append-only stays sacred.** New block types: `supersedes` (replaces a prior block visually) and `redact` (hides a prior block with reason). Original bytes always remain on disk. See Block schema.
6. ~~Search across mindframes.~~ **SQLite FTS5** at `~/.mindframe/index.db`, fed by the tailer, exposed at `GET /api/search`. Opt-in for POC. See Endpoints.

## Where this gets built

Same place the panes lane already lives: `plugins/frameworks/mindframe/dashboard/`. The dashboard server and SPA grow into this spec. Shared Python code (spawn primitive, locking wrapper, block schema) lives at `plugins/frameworks/mindframe/lib/`. UUIDv7 generation uses `uuid.uuid7()` from the stdlib — no shared helper needed.

The helper CLI (`mindframe-write`, `mindframe-set-title`, `mindframe-close`) ships as a small Python script in `plugins/frameworks/mindframe/bin/`. Install symlinks `${CLAUDE_PLUGIN_ROOT}/bin/mindframe-*` into `~/.local/bin/` (Linux/macOS) or adds to PATH via the shim mechanism (Windows). Helpers all import from `lib/` so logic stays one place.

`mindframe-set-title` and `mindframe-close` are thin wrappers: set-title is a meta.json read-modify-write under `exclusive_lock`; close appends a `close` block via the same path as `mindframe-write`.

The home surface (curated entry point that surfaces external signals + "open a mindframe on this" affordances) is a separate spec — closest to taskboard's domain.
