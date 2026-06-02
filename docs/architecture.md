# Mindframe — Architecture

This document describes how the mindframe bundle is composed, the two paths it
runs on, and what happens end-to-end when an agent runs a deliverable. For what
the product *is*, see [`product.md`](product.md); for the contracts between
subsystems, see [`interfaces.md`](interfaces.md).

---

## Mental model

Mindframe is **manifest-first**. The plugin itself ships skills, customer
templates, and a list of capabilities it requires — almost no business logic.
The components do the work; mindframe is what makes them installable as one
product. (The one exception is the dashboard, which mindframe owns directly —
see below.)

Read the bundle as a sentence:

> It **runs agents** → gives them **memory** → **wakes them up** → **sets it up**
> → has them **do the work** → **shows the human** → and **connects to the world.**

Each clause is one of the seven buckets.

## The seven buckets

| # | Bucket | Components | Role |
|---|--------|-----------|------|
| 1 | Agent runtime | `taskpilot` + `session-bridge` | Spawns and supervises `claude` processes (tmux-backed, reboot-persistent). A mesh carries inter-agent and agent-to-human messages. |
| 2 | Knowledge base | customer vault + `librarian` agent | Persistent memory of how the org works: services, projects, decisions, people, past incidents. Markdown + frontmatter; schema in [`kb-schema.md`](kb-schema.md). |
| 3 | Event router | `dispatcher` | The push path: public webhook ingress, a router, an audit log. Turns events into agent spawns. |
| 4 | Setup wizard | `/mindframe:setup` | Claude-driven onboarding: discovers the environment, collects credentials, bootstraps the vault, wires triggers, runs a smoke test. **Redesigned 2026-06-02** into a UI-based, setup-as-a-mindframe flow (terminal bootstrap births the setup mindframe, which onboards in a web surface) — see [`onboarding-ux.md`](onboarding-ux.md), the hosted `install.txt`, and `setup/brief.md`. |
| 5 | Deliverable skills | *(none ship in the current bundle — prior triage skills were deleted 2026-05-19 pending redesign)* | The work: a library of skills that ground a request in the knowledge base and produce something a human can use. Incident triage is the first slated entry; the library grows. |
| 6 | Dashboard | `taskboard` + the mindframe dashboard app | The pull path: probes everything and renders status. |
| 7 | Perception + connectors | `claude-browser-bridge` + Sentry / GCP-logging / GitHub / Grafana / Slack MCPs | General-purpose web perception plus adopt-on-install data connectors. |

## The plugin / capability graph

Mindframe declares abstract **capabilities** it `requires`. The
`softwaresoftware` resolver binds each to a concrete **provider** that fits the
host environment, and installs in dependency order. Nothing in the bundle names
a provider directly — capabilities are the only contract.

```
                         mindframe (this plugin)
                         - /mindframe:setup
                         - deliverable skills (none ship currently)
                         - customer-domain KB schema (docs/kb-schema.md)
                         - the dashboard app
                                     │
                                     │ requires
   ┌──────────────┬────────────┬─────┼──────────┬──────────────┬──────────────┐
   ▼              ▼            ▼     ▼          ▼              ▼              ▼
agent-       session-     knowledge-  event-   status-       browser-      notification
spawning     mesh         base        routing  dashboard     automation    (optional)
   │              │            │       │          │              │              │
taskpilot   session-bridge  knowledge- dispatcher taskboard   claude-        notify-slack
                            base /                            browser-      / -email / …
                            hive-mind                         bridge
```

Transitively, `taskpilot` pulls in `terminal-ops` (→ `tmux-session`) and
`daemon` (→ `daemon-manager`). The bundle's full capability closure is
verified by `tests/e2e/test_install_contract.py`.

Because the binding is by capability, any provider is swappable per customer:
notifications resolve to Slack on one install and email on the next, with no
change to mindframe or to its deliverable skills.

## Two paths: push and pull

Mindframe observes a stack two ways, and the two paths are kept strictly
separate — they never call each other.

```
         PUSH  (the ears — dispatcher)            PULL  (the eyes — taskboard)

   Sentry / PagerDuty / GitHub webhook            probes on a timer
              │                                          │
              ▼                                          ▼
       dispatcher-ingress                          taskboard
              │                                    services, daemons, agents,
       route → spawn ephemeral agent               sessions, telemetry
              │                                          │
       deliverable skill runs, produces             renders current status
              │   its output
       notify the human
```

- **Push** is event-driven and ephemeral. An event arrives, an agent spawns,
  does one job, and exits. Latency matters; the agent is short-lived.
- **Pull** is continuous and stateless-per-tick. The dashboard probes on its
  own schedule and reflects what is true now. It notices the things no event
  announced — a daemon that died quietly, a disk filling up.

Keeping them separate means a flood of events can't blind the dashboard, and a
slow probe can't delay an agent spawn.

## Runtime flow — a deliverable run (wire shape)

The canonical push-path run. **No deliverable skill currently ships** — this
section documents the wire shape any future deliverable will reuse.

```
external event ──webhook──▶ dispatcher-ingress ──▶ route ──▶ spawn ephemeral claude
                                                                 │
                                                       /mindframe:<deliverable>
                                                                 │
                          ┌─────────────────────┬────────────────┼────────────────┐
                          ▼                     ▼                ▼                ▼
                    knowledge base        perception MCPs    recent commits   output channel
                    (vault + librarian)   (provider MCPs)    (provider MCPs)  (notification provider)
                          │
                  taskboard observes everything  ◀── always-on, pull path
```

Step by step:

1. **Ingress.** The event hits `dispatcher-ingress` at `POST /api/event` with a
   bearer token. The request is deduplicated against a short idempotency
   window and written to an audit log.
2. **Route.** The dispatcher consults `channels.yaml` for a static
   `(source, event_type)` route. Static routes are the fast path for
   mechanical, no-decision events. Anything semantic falls through to the LLM
   dispatcher, which reads the payload and composes a brief.
3. **Spawn.** The router invokes the agent runtime to spawn an ephemeral
   `claude` process from a **recipe** — a directory defining the agent's
   starter prompt, the plugins it needs, and a brief template.
4. **Investigate.** The deliverable skill reads the customer vault to identify
   the affected entities (service, owners, prior incidents — depending on the
   deliverable); pulls signals from the loaded perception MCPs; reads recent
   commits if relevant; and falls back to browser automation for any UI the
   APIs don't cover.
5. **Decide.** The skill produces its output artifact — a fix PR, a draft RCA,
   a report, an answer — depending on the deliverable shape.
6. **Notify.** The recommendation goes to whichever channel the customer's
   configuration declares — Slack thread, PR comment, email. The skill uses
   intent-based language; the resolver bound the actual notification provider
   at install time.

## The dashboard

`taskboard` is the generic status-dashboard capability. The mindframe bundle
also ships its own dashboard app — a generative-UI surface where the operator
describes what they want to see and the Mindframe agent authors a complete
HTML view per instruction. It runs locally under Claude Code as a persistent
agent and is opened through browser-bridge. This is the deliberate carve-out
from "manifest-first": the dashboard is business logic mindframe owns rather
than delegates, because it is the human-facing surface of the whole bundle.

## Invariants

These hold across the bundle and should not be broken without a deliberate
decision:

- **Mindframe is manifest-first.** Bundle composition lives in the `requires`
  list. No business logic in the plugin — except the dashboard app.
- **Every box is a plugin or an MCP.** No loose tool directories in the bundle.
- **Capabilities are the only contract.** Skills reference what they need by
  intent ("send a notification"), never by provider name. Any provider is
  swappable per customer.
- **Push and pull stay separate.** The dispatcher (ears) and taskboard (eyes)
  do not talk to each other.
- **Agents recommend; humans act.** Deliverable skills stop at producing and
  notifying. Rollbacks, merges, sends, and pages are gated on human approval.
- **Subscription auth only.** Agents run as `claude` processes authenticated by
  the Claude Code subscription. No Anthropic API key anywhere in the bundle.

## Where state lives

| State | Home | Lifetime |
|---|---|---|
| Customer domain knowledge | the vault (git repo) | persistent; curated by the librarian |
| Event audit + dedupe | dispatcher-ingress SQLite DB | rolling; dedupe entries expire |
| A deliverable run's working data | the spawned agent's task directory + recipe cache | the life of the run; cache enables idempotent replay |
| Agent ↔ agent / human messages | the session-bridge mesh | transient |
| Bundle + per-deployment config | `~/.claude/settings.json` (`pluginConfigs`) | persistent |

Nothing in the bundle keeps customer state in a cloud service. The vault is a
local git repository; the audit log is a local database; agents are local
processes.
