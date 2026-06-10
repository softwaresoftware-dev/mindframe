# Mindframe — Architecture

How the mindframe stack is composed, layer by layer, and what happens end to end
when an agent runs. For what the product *is*, see [`product.md`](product.md);
for the contracts between layers, see [`interfaces.md`](interfaces.md).

---

## Mental model

Mindframe is **manifest-first**. The plugin ships skills, the customer-domain
knowledge-base schema, and a list of capabilities it requires. The layers do the
work; mindframe is what makes them installable as one product. The one exception
is the **Surface** (the dashboard), which mindframe owns directly.

The system is **six runtime layers**:

| Layer | What runs it | State |
|---|---|---|
| **Surface** | the dashboard: one FastAPI server (`dashboard/server/server.py`) + SPA that serves every mindframe at `/m/<id>` | `~/.mindframe/frames/<id>/index.html` |
| **Agent runtime** | `taskpilot` spawns a persistent tmux-backed `claude`; the starter prompt and every later message arrive over the Mesh | transcript in `~/.claude/projects/<encoded-cwd>/` |
| **Event ingress** | `dispatcher` (`:8911`): dedupe → `channels.yaml` static route → LLM fallback → spawn an ephemeral agent | `~/.dispatcher/events.db` |
| **Knowledge** | a single vault: markdown + frontmatter, the 4-layer schema in [`kb-schema.md`](kb-schema.md) *(under redesign)* | `~/.mindframe/vault` (hardcoded) |
| **Mesh** | `session-bridge` (`:8910`): agent↔agent↔human messaging; also the Agent-runtime delivery channel | transient |
| **Perception** | `claude-browser-bridge` + adopt-on-install MCPs (github / sentry / slack / …), live-probed via `/api/connections` | — |

Read the stack top to bottom. A human touches the **Surface**. The Surface
drives the **Agent runtime**. Events arrive through **Event ingress** and also
spawn agents in that runtime. Every agent draws on **Knowledge**, talks over the
**Mesh**, and reaches the world through **Perception**.

---

## The layers

### Surface — the dashboard

The one piece mindframe owns. A FastAPI server (`dashboard/server/server.py`,
no build step; `public/` is plain HTML/CSS/JS) that is the bundle's human-facing
home. It serves the SPA (`/` — the hub-graph home), the vault / sources /
system feeds, and **every mindframe** at `/m/<id>`.

A **mindframe** is the unit the Surface hosts: a persistent agent that owns one
live HTML page it rewrites in place, plus a message box. The Surface mints a
mindframe, lists them, serves each one's shell, and proxies operator messages to
its agent. State is on disk at `~/.mindframe/frames/<id>/index.html`; the agent
rewrites the file, the shell polls a revision counter and reloads.

The Surface runs as a managed daemon (the `daemon` capability) for
reboot-persistence. It holds the dispatcher bearer on disk so a mindframe's
action buttons can POST events back through Event ingress without exposing the
token to the browser. Default port `5174`. See [`../dashboard/README.md`](../dashboard/README.md).

### Agent runtime — taskpilot

`taskpilot` spawns and supervises `claude` processes. Each agent runs in a
detached tmux session (via the `terminal-ops` provider, `tmux-session`) and is
reboot-persistent via the `daemon` capability. The taskpilot daemon listens on
`:8912`; callers spawn through `POST /tasks/create_and_spawn` and message through
`POST /tasks/<id>/message`.

The agent's durable state is its **transcript** at
`~/.claude/projects/<encoded-cwd>/`. taskpilot inherits the operator's real
`~/.claude` environment, so a spawned agent has the operator's plugins, MCPs, and
identity.

**The Mesh is the transport.** taskpilot does not type into the Claude TUI. It
POSTs the starter prompt and every later message to the agent's Mesh channel at
`session-bridge :8910/sessions/<id>/message`. This is why Agent runtime and Mesh
are coupled: the Mesh is how anything reaches a running agent.

### Event ingress — dispatcher

`dispatcher` is the push path's front door: a FastAPI service bound to localhost
(default `127.0.0.1:8911`), exposed publicly through a tunnel, bearer-authed on
every endpoint except `/api/health`. Audit + dedupe state lives in
`~/.dispatcher/events.db`.

Routing on `POST /api/event`, in order:

1. **Dedupe.** A `(source, event_id)` seen inside the idempotency window
   short-circuits.
2. **Static route.** If `channels.yaml` matches, the event is forwarded to a
   Mesh session (`session:`) or spawns an ephemeral agent (`spawn:`) with no LLM
   in the loop.
3. **LLM fallback.** Otherwise it goes to the dispatcher's own Claude session,
   which reads the payload and decides.

A `spawn:` route reaches the Agent runtime through the dispatcher's
`spawn_helper.py`, which reads `~/.dispatcher/recipes/<id>/` and calls
taskpilot's `POST :8912/tasks/create_and_spawn`. A draft poller runtime (pulling
events instead of receiving webhooks) is sketched in
[`../../../providers/dispatcher/docs/event-sources.md`](../../../providers/dispatcher/docs/event-sources.md)
but is **not built** — webhook ingest is the only live path.

### Knowledge — the vault

Mindframe owns this layer directly: a single local directory at
`~/.mindframe/vault` (hardcoded as `VAULT_DIR` in the Surface; not configurable,
not a resolved provider). Markdown notes with YAML frontmatter, one note per
entity, organized by the 4-layer schema (Thing / Event / Knowledge / Process) in
[`kb-schema.md`](kb-schema.md), plus a `CATALOG.md` index. Read by grep, not
embeddings.

**Under redesign in a separate effort.** There is no separate knowledge-capture
subsystem; the vault is written by setup's bootstrap and by mindframe agents as
they work. Treat the schema as descriptive of today's vault, not final.

### Mesh — session-bridge

`session-bridge` is the message bus connecting agents and humans, a localhost
daemon on `:8910`. Every spawned agent registers a channel under its task id and
joins the mesh automatically. It exposes three tools to a session: `sessions`
(list members), `message` (start a conversation), `reply` (answer within one).
Inbound messages arrive as `<channel>` notifications carrying `from_id`,
`from_name`, and a `chat_id` to reply against.

The Mesh is transient: the registry is in-memory, driven by `/register`. It is
both the human↔agent channel and, as noted above, the Agent runtime's delivery
transport.

### Perception — browser-bridge + adopted MCPs

`claude-browser-bridge` gives an agent control of a real browser (navigate, fill,
click, screenshot, run JS, observe the a11y tree) for any web UI an API doesn't
cover. It is default-install, general-purpose perception.

Alongside it, the bundle **adopts on install** whatever data-source MCPs the
operator already has (github / sentry / slack / gcp-logging / grafana / gmail /
…). These are not bundled; they are discovered live. The Surface's
`/api/connections` enumerates them (`claude mcp list` plus `gh`/`gcloud`/`aws`/`az`
auth probes, minus the bundle's own runtime), which is how setup and the
dashboard know what an agent can reach.

---

## The capability graph

Mindframe declares abstract **capabilities** it `requires`. The
`softwaresoftware` resolver binds each to a concrete **provider** that fits the
host, and installs in dependency order. Nothing in the bundle names a provider
directly.

| Layer | Capability | Provider |
|---|---|---|
| Surface | *(mindframe owns it)* | `dashboard/` |
| Agent runtime | `agent-spawning` | `taskpilot` |
| Event ingress | `event-routing` | `dispatcher` |
| Knowledge | *(mindframe owns it)* | the vault — plain files at `~/.mindframe/vault` *(under redesign)* |
| Mesh | `session-mesh` | `session-bridge` |
| Perception | `browser-automation` | `claude-browser-bridge` + adopted MCPs |

Transitively, `taskpilot` pulls in `terminal-ops` (→ `tmux-session`) and
`daemon` (→ `daemon-manager`); the Surface uses `daemon` too. `notification` is
a capability agents use to notify a human (resolves to `notify-slack` /
`notify-email` / `notify-linux` / …); it is not a layer. The Knowledge vault is
**not** a resolved capability — the manifest carries no `knowledge-base`
requirement; mindframe owns the vault outright (see below).

Because the binding is by capability, any layer is swappable per customer with no
change to mindframe or its skills.

---

## Runtime flow — a push-path run

```
external event ──webhook──▶ Event ingress (dispatcher :8911)
                              dedupe → channels.yaml → LLM fallback
                              └─ spawn:<recipe>
                                     │ POST :8912/tasks/create_and_spawn
                                     ▼
                              Agent runtime (taskpilot)
                              tmux-backed claude; prompt + messages over the Mesh
                                     │
                  ┌──────────────────┼──────────────────┐
                  ▼                  ▼                   ▼
              Knowledge          Perception          output channel
              (the vault)        (browser-bridge     (notification
                                  + adopted MCPs)     capability, optional)
```

1. **Ingress.** The event hits `POST /api/event` with a bearer token, is
   deduplicated, and written to `events.db`.
2. **Route.** `channels.yaml` is consulted for a static `(source, event_type)`
   match. Mechanical events route statically; anything semantic falls through to
   the LLM dispatcher.
3. **Spawn.** A `spawn:` route composes the recipe's brief and calls the Agent
   runtime daemon. The new agent's prompt is delivered over the Mesh.
4. **Work.** The agent reads the **Knowledge** vault to identify entities, pulls
   live signals through **Perception** (browser-bridge + adopted MCPs), and does
   its job.
5. **Recommend.** The agent produces an artifact and, if a `notification`
   provider is present, notifies a human. Anything irreversible waits for human
   confirmation.

The **interactive** path is the same runtime entered from the top: the operator
opens the Surface, creates or messages a mindframe, and the Surface delivers the
message to the Agent runtime over the same daemon. There is no separate
interactive stack.

---

## Invariants

- **Manifest-first.** Composition lives in `requires`. The Surface is the only
  business logic mindframe owns.
- **Every layer is a plugin or an MCP**, bound by capability. Skills reference
  capabilities by intent, never by provider name.
- **The Mesh is the agent transport.** No keystroke injection; messages reach
  agents over `session-bridge`.
- **Single vault, single Surface.** One `~/.mindframe/vault`; one dashboard for
  every mindframe.
- **Agents recommend; humans act.** Irreversible steps are gated on confirmation.
- **Subscription auth only.** No `ANTHROPIC_API_KEY` in the bundle.

## Where state lives

| State | Home | Lifetime |
|---|---|---|
| A mindframe's page | `~/.mindframe/frames/<id>/index.html` | persistent; rewritten by its agent |
| Customer knowledge | `~/.mindframe/vault` | persistent |
| Agent transcript | `~/.claude/projects/<encoded-cwd>/` | the life of the agent |
| Event audit + dedupe | `~/.dispatcher/events.db` | rolling; dedupe entries expire |
| Agent ↔ agent / human messages | the session-bridge mesh | transient |
| Bundle + deployment config | `~/.claude/settings.json` (`pluginConfigs`) | persistent |

Nothing in the bundle keeps customer state in a cloud service. The vault is a
local directory, the audit log is a local database, agents are local processes.
