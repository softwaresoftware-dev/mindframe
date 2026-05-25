# Mindframe — Agentic Framework Bundle

Customer-installable bundle that gives an organization a knowledge base of how it works — and AI agents that act on it. Mindframe is a packaging + onboarding layer, not a framework or dashboard in its own right. The components do the work; mindframe is what makes them installable as one product. Incident triage is the first deliverable skill, not the mission — the bundle is general over business, engineering, and software-ops domains.

This plugin is **manifest-first** — ships skills, customer templates, and a `requires` list. Actual work happens in the bundled providers. The one exception is `dashboard/` (see below): a self-contained generative-UI web app that ships *inside* this plugin, distributed by mindframe, run locally under Claude Code, and opened through browser-bridge.

## Bundle composition (7 buckets)

Mental model: **runs agents → gives them memory → wakes them up → sets it up → is what they do → shows the human → connects to the world.**

1. **Agent runtime** — `taskpilot` + `session-bridge`. Spawns and manages claude processes (tmux-backed, reboot-persistent). Mesh for inter-agent + agent-to-human messaging.
2. **Knowledge base** — customer vault + librarian agent. Markdown + frontmatter, schema in `docs/kb-schema.md`. Persistent memory: services, repos, runbooks, owners, on-call, past incidents.
3. **Event router** — `dispatcher`. Push-path: public webhook ingress + LLM/direct router + audit. Spawns ephemeral agents on demand.
4. **Setup wizard** — `/mindframe:setup`. Claude-driven onboarding. Walks user through credentials per data system, validates connections live, bootstraps the vault, configures triggers, runs end-to-end smoke test.
5. **Deliverable skills** — a library of skills that turn the knowledge base into work: incident triage (RCA → draft fix → notify), reviews and reports, answers about how the org runs. What the customer asks the agents for. **No deliverable skills currently ship in the bundle** (the prior `sentry-triage` and `k8s-triage` were deleted 2026-05-19 pending redesign). New deliverables are added to the library; incident triage is the first slated entry.
6. **Dashboard** — mindframe ships its own dashboard at `dashboard/`. Runs as a managed daemon via `daemon-manager` (the `daemon` capability provider). Reboot-persistent via systemd/launchd/Task Scheduler. Sibling-plugin merger with `taskboard` is future work.
7. **Perception + adopt-first MCPs** — `claude-browser-bridge` + sentry / gcp-logging / github / grafana / slack MCPs. Browser-bridge is general-purpose perception for any web UI; default-install, not opt-in.

## Architecture

### Plugin/capability graph

```
                           mindframe (this plugin)
                           - /mindframe:setup
                           - deliverable skills (none ship currently)
                           - customer-domain KB schema (docs/kb-schema.md)
                                       │
                                       │ requires
   ┌──────────────┬────────────┬───────┼────────────┬────────────┬──────────┬───────────┐
   ▼              ▼            ▼       ▼            ▼            ▼          ▼           ▼
agent-       channel    knowledge-   event-     status-      browser-     daemon    notification
spawning                base         routing    dashboard    automation
   │              │            │       │            │            │          │
 taskpilot   session-bridge librarian  dispatcher  taskboard   browser-bridge  daemon-manager
                                                                              (runs the dashboard
                                                                              as a managed service)
```

### Runtime flow (wire shape for any deliverable skill)

The bundle currently ships no deliverable skill. The wire shape below is what any future deliverable will plug into — preserved here so the dispatcher → taskpilot → mesh → vault → notify path is documented:

```
external event → dispatcher (webhook) → spawn ephemeral claude → /mindframe:<deliverable>
                                                                  │
                                      ┌───────────────────────────┼──────────────────────┐
                                      ▼                           ▼                      ▼
                                  knowledge-base          browser-bridge MCP     output channel
                                  (vault + librarian)     (provider MCPs)        (notification provider)
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
- `skills/doctor/` — `/mindframe:doctor`: bundle self-diagnostic. Walks the `requires` list capability by capability, probes each provider, heals safe (Tier-1) issues automatically and reports the rest. Same evidence rule as setup.
- `dashboard/` — FastAPI server that serves the SPA shell, exposes the block-stream API (`/api/frames`, `/api/frame/<id>`, `/api/frame/<id>/blocks`, `/api/frame/<id>/stream` SSE), and the manual-spawn `POST /api/frames`. Runs as a managed daemon via the `daemon` capability (`daemon-manager`) — reboot-persistent via systemd/launchd/Task Scheduler. SPA: boards-index at `/`, per-mindframe detail at `/m/<id>` with the typed block renderer. See `dashboard/README.md` and `docs/mindframe-block-stream-api.md`.
- `recipes/mindframe-poc/` — example recipe ships in-tree. `make install-recipes` copies it to `~/.dispatcher/recipes/`.
- `lib/` — `frame.py` (core storage ops), `spawn.py` (CLI), tests. The block-stream contract.
- `mcp/` — `server.py` (FastMCP `write_block` + `set_title`), tests.
- `tests/e2e_wire/` — Tier 1 hermetic integration. `tests/e2e_real/` — Tier 2 live-agent smoke. `tests/e2e_fresh/` — Tier 3 native fresh-install harness.

## Next

The bundle is now in **shipping shape**: cross-platform CI matrix green, demo recipe works end-to-end, install path documented. Remaining items in priority order:

1. **Diagnose the live-agent crash at block ~10** (gap #6). The mindframe-poc spawn died early in the live demo. State dir was empty — taskpilot's lifecycle hooks may not have fired. Needs another live spawn with hook instrumentation.
2. **Real end-to-end fresh-install dry-run** against a clean Linux box (Tier 3 covers the deterministic surface; the Claude-driven phases of install.txt — PHASE 3–8 — still need a human-in-the-loop pass).
3. **Home surface** — signals + "tackle this" buttons that spawn mindframes. Closer to taskboard's domain.
4. **Block-stream renderer polish** — supersedes/redact visual paths untested, inline markdown is minimal (no GFM tables/strikethrough), large historical replay isn't paginated.

### Shipped this slice (chronological)

- ~~`bin/mindframe-write`~~ → MCP server (write_block, set_title)
- ~~SPA block renderer + SSE stream~~ → live in browser
- ~~`mindframe.spawn()` primitive~~ → `lib/frame.py` + `lib/spawn.py` CLI, dispatcher reads `frame:` from recipe.yaml
- ~~Demo recipe~~ → ships in `recipes/mindframe-poc/`, `make install-recipes` installs to `~/.dispatcher/`
- ~~3-tier testing stack~~ → Tier 1 wire (hermetic), Tier 2 real-agent smoke (manual), Tier 3 fresh-install (native, no Docker)
- ~~CI matrix Linux/macOS/Windows~~ — caught and fixed 6 cross-platform bugs on first run
- ~~Dashboard as managed daemon~~ — `daemon` capability declared, setup skill + install.txt updated to register via daemon-manager (intent-based, not tool-hardcoded)

## Current dashboard state (as of 2026-05-23)

- `dashboard/server/server.py` — FastAPI: `/api/health`, `/api/panes`, `/api/dashboard-event` (dispatcher proxy reading `~/.mindframe/secrets/dispatcher-bearer.token`), `/api/save` + `/s/<id>` shares, `/artifacts/<sid>/<path>` artifact serving, SPA fallback for everything else. No taskpilot or session-bridge dependency.
- `dashboard/public/` — Inter-free design-system stack (Space Grotesk / Source Serif 4 / JetBrains Mono), warm dark palette, gold accent. Two routes: `/` (boards index) and `/m/<id>` (mindframe detail).
- Bearer file at `~/.mindframe/secrets/dispatcher-bearer.token` (chmod 600); matches the dispatcher daemon's `DISPATCHER_INGEST_TOKEN` env so button events succeed end-to-end.
- 21 artifacts under `dashboard/artifacts/`: 19 historical (legacy dashboard-agent leftovers, ignore for demos) + `demo-incident-001` (OOMKilled in payments-api) + `demo-customer-review` (Acme QBR prep). Latter two are hand-authored HTML used for visual iteration.
