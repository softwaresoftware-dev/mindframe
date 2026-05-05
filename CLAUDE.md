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
agent-       channel    knowledge-   event-     status-      browser-      error-triage
spawning                base         routing    dashboard    automation    (optional)
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
- **Every box is a plugin or an MCP.** No loose `tools/` directories in the bundle.
- **Capabilities are the only contract.** Any provider is swappable per customer (notification → Slack today, email tomorrow).
- **Push and pull paths stay separate.** Dispatcher (ears) and taskboard (eyes) don't talk directly.

## Bootstrap order

KB schema (paper) ✓ → `/mindframe:sentry-triage` against fake KB → `/mindframe:setup` wizard → trigger plumbing.

## Status, decisions, open threads

Lives in the vault at `Projects/mindframe-rollout.md`. Ask the librarian — don't re-record state here. The librarian owns the v2 schema (11 entity types + FK rules) and will keep cross-references correct.

## In-directory artifacts

- `docs/kb-schema.md` — customer-domain KB contract. Read before building setup or wedge skills.
- `skills/setup/` — `/mindframe:setup` wizard.
- `skills/sentry-triage/` — `/mindframe:sentry-triage` wedge skill.
