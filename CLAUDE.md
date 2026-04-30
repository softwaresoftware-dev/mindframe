# Mindframe — Agentic Framework Bundle

Customer-installable bundle for shipping AI-agent infrastructure to enterprises. Mindframe is a packaging + onboarding layer, not a framework or dashboard in its own right. The components do the work; mindframe is what makes them installable as one product.

This plugin is **manifest-first** — ships skills, customer templates, and a `requires` list. Actual work happens in the bundled providers.

## Bundle composition (7 buckets)

Mental model: **runs agents → gives them memory → wakes them up → sets it up → is what they do → shows the human → connects to the world.**

1. **Agent runtime** — `taskpilot` + `session-bridge`. Spawns and manages claude processes (tmux-backed, reboot-persistent). Mesh for inter-agent + agent-to-human messaging.
2. **Knowledge base** — customer vault + librarian agent. Markdown + frontmatter, schema in `docs/kb-schema.md`. Persistent memory: services, repos, runbooks, owners, on-call, past incidents.
3. **Event router** — `dispatcher`. Push-path: public webhook ingress + LLM/direct router + audit. Spawns ephemeral agents on demand.
4. **Setup wizard** — `/mindframe:setup`. Claude-driven onboarding. Walks user through credentials per data system, validates connections live, bootstraps the vault, configures triggers, runs end-to-end smoke test.
5. **Wedge skill** — `/mindframe:sentry-triage`. RCA → draft fix → notify. The thing the customer pays for.
6. **Dashboard** — `taskboard` (sibling plugin). Pull-path: probes services, agents, daemons, sites, sessions, telemetry and renders status. Pairs with dispatcher — taskboard is the eyes (pull), dispatcher is the ears (push).
7. **Perception + adopt-first MCPs** — `claude-browser-bridge` + sentry / gcp-logging / github / grafana / slack MCPs. Browser-bridge is general-purpose perception for any web UI; default-install, not opt-in.

## Architecture

Two views.

### Plugin/capability graph

```
                           mindframe (this plugin)
                           - /mindframe:setup
                           - /mindframe:sentry-triage
                           - customer-domain KB schema (docs/kb-schema.md)
                                       │
                                       │ requires
   ┌──────────────┬────────────┬───────┼────────────┬────────────┬──────────────┐
   ▼              ▼            ▼       ▼            ▼            ▼              ▼
agent-       channel    knowledge-   event-     infra-       browser-      error-triage
spawning                base         routing    status-page  automation    (optional)
   │              │            │       │            │            │              │
 taskpilot   session-bridge librarian  dispatcher  taskboard   browser-bridge   gh-mcp,
                                                                                sentry-mcp,
                                                                                gcp-logging,
                                                                                slack
```

### Runtime flow (Sentry triage)

```
Sentry → dispatcher (webhook) → spawn ephemeral claude → /mindframe:sentry-triage
                                                          │
                              ┌───────────────────────────┼──────────────────────┐
                              ▼                           ▼                      ▼
                          knowledge-base          browser-bridge MCP     output channel
                          (vault + librarian)     (Sentry, Grafana, GH)  (Slack, PR, email)
                              │
                          taskboard observes everything ← always-on
```

## Invariants

- **Mindframe is manifest-first.** Bundle composition lives in `requires`. No business logic in this plugin.
- **Every box is a plugin or an MCP.** No loose `tools/` directories in the bundle. The only existing exception is `home-taskboard` (Thatcher's :42069 deployment) — that migrates to a `taskboard`-plugin deployment in parallel work.
- **Capabilities are the only contract.** Any provider is swappable per customer (notification → Slack today, email tomorrow).
- **Push and pull paths stay separate.** Dispatcher (ears) and taskboard (eyes) don't talk directly.

## Decisions (canonical 2026-04-27, see vault)

- **Job-per-event over long-running.** Each event spawns fresh claude that dies after the job. Reliable, avoids context-window collapse.
- **Claude Code subscription pricing.** Agents run as `claude` in tmux, billed against the user's subscription, not API tokens.
- **Customer credentials = setup user's credentials (POC compromise).** Must change before second customer.
- **Adopt-first for MCPs.** Only build if no acceptable community option exists.
- **Markdown vault for KB.** Greppable, human-editable, dogfoods existing librarian.
- **Browser-bridge default-install.** General-purpose perception, not optional.

## Bootstrap order

KB schema (paper) ✓ → `/mindframe:sentry-triage` against fake KB → `/mindframe:setup` wizard → trigger plumbing.

## Reference

- `docs/kb-schema.md` — customer-domain KB contract (11 entity types, FK rules, CATALOG, validator, 3-pass bootstrap)
- `../taskboard/` — sibling plugin, dashboard framework, also bundled
- `vault-v1/Projects/mindframe/mindframe.md` — open threads, decisions log

## Open threads

Tracked in the vault note. Highlights:

1. Generalize vault for customer domain (services/repos/runbooks/incidents/owners) + per-customer packaging
2. Dispatcher: add spawn-on-demand mode + idempotency + customer-domain ingress
3. MCP fitness assessment per system
5. Get 30-50 anonymized real Sentry issues from customer
6. Notification mechanism per integration (Slack thread vs PR comment vs email)
7. RCA destination (GitHub wiki vs PR description vs vault entry)
