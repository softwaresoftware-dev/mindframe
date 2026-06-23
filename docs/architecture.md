# Mindframe — Architecture

The six runtime layers in depth: what runs each, the state it holds, and the
runtime flow. For the product see [`product.md`](product.md); for the contracts
between layers see [`interfaces.md`](interfaces.md).

---

## Mental model

Mindframe is **manifest-first**: the plugin ships skills, the knowledge-base
schema, and a list of capabilities it requires. The composed providers do the
work; mindframe makes them installable as one product. The exceptions are the
**Surface** (the dashboard) and the **Knowledge** vault, which mindframe owns
directly.

| Layer | What runs it | State |
|---|---|---|
| **Surface** | the dashboard: one **multi-tenant** FastAPI server (`dashboard/server/server.py`, port `5174`) + SPA — portal at `/`, workspace home at `/w/<id>/`, mindframe at `/w/<id>/m/<frame>` | `~/.mindframe/workspaces/<id>/.mindframe/frames/<frame>/index.html` |
| **Agent runtime** | one `taskpilot` (`:8912`) spawns tmux-backed `claude` for every workspace, each agent with a **per-task `$HOME`** = its workspace partition; prompt + every message delivered over the Mesh | transcript under the agent's `$HOME/.claude/projects/…`; task rows in `~/.taskpilot/taskpilot.db` (each row carries its `home`) |
| **Event ingress** | one `dispatcher` (`:8911`, ingress + poller): per event, **derive the workspace from its source** → that workspace's `channels.yaml` → spawn with the workspace's home | `~/.dispatcher/events.db` (rows tagged with `workspace`) |
| **Knowledge** | a vault **per workspace**: markdown + frontmatter, schema in [`kb-schema.md`](kb-schema.md) *(under redesign)* | `~/.mindframe/workspaces/<id>/.mindframe/vault` |
| **Mesh** | `session-bridge` (`:8910`): agent↔agent↔human messaging; also the Agent-runtime delivery channel — one shared per-host registry | transient (in-memory registry) |
| **Perception** | `claude-browser-bridge` + the MCPs/connector skills in each **workspace's** `.claude`, discovered live via `/w/<id>/api/connections` | — |

Read top to bottom: a human touches the **Surface**; the Surface drives the
**Agent runtime**; events arrive through **Event ingress** and spawn agents in
the same runtime; agents draw on **Knowledge**, talk over the **Mesh**, and
reach the world through **Perception**.

---

## The layers

### Surface — the dashboard

The piece mindframe owns. A FastAPI server with no build step (`public/` is
plain HTML/CSS/JS). One process serves every workspace: a **portal** at `/`
listing the operator's workspaces, each workspace's **home** at `/w/<id>/` — the
calm launcher: one "What should we work on?" input (typed text creates a
purposeful frame; empty opens a launchpad), the operator's attention in a few
lines (inbox with provenance, resume, recent activity), app chips, and drawers
(frames, watches, agents, knowledge, connections) — and each mindframe at
`/w/<id>/m/<frame>`. A `WorkspaceMiddleware` strips the `/w/<id>` prefix and the
server resolves frames/vault per request from
`~/.mindframe/workspaces/<id>/.mindframe/` (there is no global frames/vault).

A **mindframe** is the unit it hosts: a persistent agent that owns one HTML
page it rewrites in place, plus a message box. The Surface mints one inside the
active workspace (`POST /w/<id>/api/frames/create` → taskpilot `PUT /tasks/<id>`
+ `start`, with the task's `home` set to the workspace partition so the agent
runs there; task id == frame id), serves its shell, and proxies operator
messages to its agent. The agent
rewrites `index.html` with the Write tool; the shell polls
`/api/frame/<id>/rev` (the file's mtime) and reloads on change. The shell's
"working" indicator derives from the agent's transcript mtime.

The dashboard runs as a managed daemon (`mindframe-dashboard`, via the
`daemon` capability). It binds `127.0.0.1` only and is unauthenticated — see
the security posture in [`interfaces.md`](interfaces.md#9-security-posture).
It holds the dispatcher bearer on disk
(`~/.mindframe/secrets/dispatcher-bearer.token`) so agent-page action buttons
can POST events through `/api/dashboard-event` without the token reaching the
browser. See [`../dashboard/README.md`](../dashboard/README.md).

### Agent runtime — taskpilot

`taskpilot` spawns and supervises `claude` processes. Each agent runs in a
detached tmux session (the `terminal-ops` provider); the daemon on `:8912`
exposes an idempotent lifecycle API: `PUT /tasks/<id>` (define),
`/tasks/<id>/start` (ensure running — also the revive path, with an optional
prompt override), `/tasks/<id>/stop`, `/tasks/<id>/message` (verified
delivery), and `DELETE /tasks/<id>` (free the id). Status is reconciled
against tmux ground truth on every read. The daemon itself is
reboot-persistent through the `daemon` capability; the tasks it spawns are
not — a dead task stays down until a caller starts it again (the Surface does
this automatically on the next operator message).

**One taskpilot serves every workspace.** Each task carries a `home`; the
spawner exports it as the agent's `$HOME`, so the agent runs inside its workspace
partition (`~/.mindframe/workspaces/<id>/`) — it sees that workspace's MCPs
(`.claude.json`), connector skills (`.claude/skills`), and vault, and runs on the
operator's subscription login seeded there (no per-workspace OAuth; the spawner
also scrubs `ANTHROPIC_API_KEY`). Its durable state is its transcript under
`$HOME/.claude/projects/…`.

**The Mesh is the transport.** taskpilot does not type into the Claude TUI; it
POSTs the starter prompt and every later message to the agent's mesh channel
at `session-bridge :8910/sessions/<id>/message`.

### Event ingress — dispatcher

One `dispatcher` acquires external events for every workspace and routes them.
Ingestion is **poll-first**: its poller aggregates event-source declarations
across all workspace partitions
(`~/.mindframe/workspaces/<id>/.mindframe/dispatcher/event-sources/*.yaml`, via
`DISPATCHER_WORKSPACES_ROOT`) and **tags each polled event with the workspace it
came from** (the source's owning partition). The `POST /api/event` webhook on
`:8911` still works (the dashboard's `/api/dashboard-event` proxy uses it) but is
deprecated. All endpoints except `/api/health` are bearer-authed; audit + dedupe
state lives in `~/.dispatcher/events.db` (rows carry the `workspace`; cursors are
namespaced by workspace).

Routing, in order, against the **originating workspace's** config: dedupe →
static route from that workspace's `channels.yaml` (`session:<name>` forward or
`spawn:<recipe>`) → LLM fallback to the dispatcher's own Claude session. A
`spawn:` route reads the workspace's `recipes/<id>/`, composes the brief, and
calls taskpilot's `create_and_spawn` with the workspace's `home` — so the spawned
agent runs in the right partition.

### Knowledge — the vault

Mindframe owns this layer directly: a local vault **per workspace** at
`~/.mindframe/workspaces/<id>/.mindframe/vault` (not a resolved capability).
Markdown notes with YAML frontmatter, one note per entity, organized by the
four-layer schema in [`kb-schema.md`](kb-schema.md), plus a `CATALOG.md`
index. Read by grep, not embeddings. Written by setup's bootstrap and by that
workspace's agents as they work. **Under redesign in a separate effort** — treat
the schema as descriptive of today's vault, not final.

### Mesh — session-bridge

A localhost daemon on `:8910`. Every spawned agent registers a channel under
its task id and joins the mesh automatically. Three tools per session:
`sessions`, `message`, `reply`; inbound messages arrive as `<channel>`
notifications. The registry is in-memory — the Mesh holds no durable state. It
is both the human↔agent channel and the Agent runtime's delivery transport.

### Perception — browser-bridge + adopted tools

`claude-browser-bridge` gives an agent control of a real browser for any web
UI an API doesn't cover. Alongside it, each workspace's agents use the MCPs and
connector skills configured in **that workspace's** `.claude` — nothing is
bundled; the Surface's `/w/<id>/api/connections` discovers them live (the
workspace's `.claude.json` MCPs plus a scan of its `.claude/skills` for connector
skills carrying a `connection:` fingerprint; presence only, no auth probing).
`/mindframe:connect` authors new connector skills into a workspace.

---

## The capability graph

| Layer | Capability | Provider |
|---|---|---|
| Surface | *(mindframe owns it)* | `dashboard/` |
| Agent runtime | `agent-spawning` | `taskpilot` |
| Event ingress | `event-routing` | `dispatcher` |
| Knowledge | *(mindframe owns it)* | a vault per workspace at `~/.mindframe/workspaces/<id>/.mindframe/vault` |
| Mesh | `session-mesh` | `session-bridge` |
| Perception | `browser-automation` | `claude-browser-bridge` + adopted tools |

Plus `daemon` (→ `daemon-manager`), required directly for the dashboard and
transitively by the other daemons; `taskpilot` also pulls in `terminal-ops`
(→ `tmux-session`). Because binding is by capability, any composed layer is
swappable per customer with no change to mindframe or its skills.

---

## Runtime flow

The push path:

```
external event ──▶ Event ingress (dispatcher :8911, poll-first)
                      derive workspace → its channels.yaml → LLM fallback
                      └─ spawn:<recipe>
                             │ POST :8912/tasks/create_and_spawn (define+start, home=workspace)
                             ▼
                      Agent runtime (taskpilot; HOME = workspace partition)
                      tmux-backed claude; prompt + messages over the Mesh
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
          Knowledge      Perception     output
          (the vault)    (browser +     (artifact; notify a human
                          adopted MCPs)  if a tool is available)
```

1. **Ingress.** The event is acquired (polled or webhooked), **tagged with its
   workspace** (the owning partition), deduplicated, and written to `events.db`.
2. **Route.** The originating workspace's `channels.yaml` is consulted for a
   static match; anything semantic falls through to the LLM dispatcher.
3. **Spawn.** A `spawn:` route composes the recipe's brief and calls the Agent
   runtime daemon **with the workspace's home**, so the new agent runs in that
   partition; its prompt is delivered over the Mesh.
4. **Work.** The agent reads its workspace's vault, pulls live signals through
   Perception, and does its job.
5. **Recommend.** The agent produces an artifact. Anything irreversible waits
   for human confirmation.

The **interactive** path is the same runtime entered from the top: the operator
opens a workspace (`/w/<id>/`), creates or messages a mindframe, and the Surface
delivers the message to the Agent runtime through the same `:8912` daemon. There
is no separate interactive stack.

---

## Invariants

- **Manifest-first.** Composition lives in `requires`. The Surface is the only
  business logic mindframe owns.
- **Every composed layer is a plugin or an MCP**, bound by capability. Skills
  reference capabilities by intent, never by provider name.
- **The Mesh is the agent transport.** No keystroke injection.
- **One stack, many workspaces.** A single shared stack serves every workspace; a workspace is a *partition* under `~/.mindframe/workspaces/<id>/`, not its own daemons or ports. Agents isolate by per-task `$HOME`; the dashboard is multi-tenant (`/w/<id>/`). See [`single-stack-contract.md`](single-stack-contract.md).
- **Agents recommend; humans act.** Irreversible steps are gated on
  confirmation.
- **No credentials in mindframe.** Identity inheritance; the only secrets
  mindframe creates live under `~/.mindframe/secrets/`.
- **Subscription auth only.** No `ANTHROPIC_API_KEY` in the bundle.

## Where state lives

| State | Home | Lifetime |
|---|---|---|
| Workspace registry | `~/.mindframe/workspaces.yaml` | persistent |
| A mindframe's page | `~/.mindframe/workspaces/<id>/.mindframe/frames/<frame>/index.html` | persistent; rewritten by its agent |
| Customer knowledge | `~/.mindframe/workspaces/<id>/.mindframe/vault` (per workspace) | persistent |
| Workspace connections + auth seed | `~/.mindframe/workspaces/<id>/.claude/` (+ `.claude.json`) | persistent |
| Agent transcript | the agent's `$HOME/.claude/projects/…` (its workspace partition) | the life of the agent |
| Task rows (incl. each task's `home`) | `~/.taskpilot/taskpilot.db` | persistent |
| Event audit + dedupe | `~/.dispatcher/events.db` (rows tagged with `workspace`) | rolling; dedupe entries expire |
| Routing config + recipes + event-sources | `~/.mindframe/workspaces/<id>/.mindframe/dispatcher/` (per workspace) | persistent |
| Generated secrets | `~/.mindframe/secrets/` | persistent; file-handoff only |
| Agent ↔ agent / human messages | the session-bridge mesh | transient |
| Bundle config | `~/.claude/settings.json` (`pluginConfigs.mindframe`) | persistent |

Nothing in the bundle keeps customer state in a cloud service. The vault is a
local directory, the audit log is a local database, agents are local
processes.
