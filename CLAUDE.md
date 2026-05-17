# Mindframe — Agentic Framework Bundle

Customer-installable bundle that gives an organization a knowledge base of how it works — and AI agents that act on it. Mindframe is a packaging + onboarding layer, not a framework or dashboard in its own right. The components do the work; mindframe is what makes them installable as one product. Incident triage is the first deliverable skill, not the mission — the bundle is general over business, engineering, and software-ops domains.

This plugin is **manifest-first** — ships skills, customer templates, and a `requires` list. Actual work happens in the bundled providers. The one exception is `dashboard/` (see below): a self-contained generative-UI web app that ships *inside* this plugin, distributed by mindframe, run locally under Claude Code, and opened through browser-bridge.

## Bundle composition (7 buckets)

Mental model: **runs agents → gives them memory → wakes them up → sets it up → is what they do → shows the human → connects to the world.**

1. **Agent runtime** — `taskpilot` + `session-bridge`. Spawns and manages claude processes (tmux-backed, reboot-persistent). Mesh for inter-agent + agent-to-human messaging.
2. **Knowledge base** — customer vault + librarian agent. Markdown + frontmatter, schema in `docs/kb-schema.md`. Persistent memory: services, repos, runbooks, owners, on-call, past incidents.
3. **Event router** — `dispatcher`. Push-path: public webhook ingress + LLM/direct router + audit. Spawns ephemeral agents on demand.
4. **Setup wizard** — `/mindframe:setup`. Claude-driven onboarding. Walks user through credentials per data system, validates connections live, bootstraps the vault, configures triggers, runs end-to-end smoke test.
5. **Deliverable skills** — a library of skills that turn the knowledge base into work: incident triage (`/mindframe:sentry-triage`, `/mindframe:k8s-triage` — RCA → draft fix → notify), reviews and reports, answers about how the org runs. What the customer asks the agents for. New deliverables are added to the library; incident triage is the first.
6. **Dashboard** — `taskboard` (sibling plugin). Pull-path: probes services, agents, daemons, sites, sessions, telemetry and renders status. Pairs with dispatcher — taskboard is the eyes (pull), dispatcher is the ears (push).
7. **Perception + adopt-first MCPs** — `claude-browser-bridge` + sentry / gcp-logging / github / grafana / slack MCPs. Browser-bridge is general-purpose perception for any web UI; default-install, not opt-in.

## Architecture

### Plugin/capability graph

```
                           mindframe (this plugin)
                           - /mindframe:setup
                           - deliverable skills (sentry-triage, k8s-triage, …)
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

### Runtime flow (example: a Sentry-triage deliverable)

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

- **Mindframe is manifest-first.** Bundle composition lives in `requires`. No business logic in this plugin — *except* `dashboard/`, the generative-UI app mindframe ships and distributes directly.
- **Every box is a plugin or an MCP.** No loose `tools/` directories in the bundle. `dashboard/` is the deliberate carve-out: it is the Act-3 hero surface, owned by mindframe rather than delegated to a provider.
- **Capabilities are the only contract.** Any provider is swappable per customer (notification → Slack today, email tomorrow).
- **Push and pull paths stay separate.** Dispatcher (ears) and taskboard (eyes) don't talk directly.

## Status, decisions, open threads

Lives in the vault at `Projects/mindframe-rollout.md`. Ask the librarian — don't re-record state here. The librarian owns the customer-domain KB schema and will keep cross-references correct.

## In-directory artifacts

- `docs/kb-schema.md` — the KB schema library: the fixed meta-schema, core entities, domain packs, and the per-install `schema.yaml` manifest format. Read before building setup or deliverable skills.
- `skills/setup/` — `/mindframe:setup` wizard.
- `skills/sentry-triage/`, `skills/k8s-triage/` — deliverable skills (incident triage). The first entries in the skills library.
- `dashboard/` — generative-UI web app (vanilla JS frontend in `public/` — no build step; Python/FastAPI backend). The Mindframe agent authors a complete HTML dashboard per instruction; runs locally under Claude Code via a persistent taskpilot agent, opened through browser-bridge. See `dashboard/README.md`.
