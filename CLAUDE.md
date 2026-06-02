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
- `docs/onboarding-ux.md` — **the onboarding UX model + decisions (2026-06-02 redesign).** The concept ladder (knowledge base → schema → connections → watches → signals → mindframes), the three-zone first-run surface, agent-led + human-in-the-loop principles, the intent-channel/render-state model, and the generative-UI direction. **Read this before touching setup or the dashboard** — it captures decisions not yet reflected in shipped code.
- `docs/install-flow-v2.md` — the redesigned, self-contained `install.txt` (web-app-first, dashboard-early, setup-as-a-mindframe, human-in-the-loop gate). Supersedes `install-outline.md` once promoted to the hosted `install.txt`.
- `skills/setup/` — `/mindframe:setup` wizard (delegating stub → fetches the hosted `install.txt`; the next version is generated from `docs/install-flow-v2.md`).
- `skills/doctor/` — `/mindframe:doctor`: bundle self-diagnostic. Walks the `requires` list capability by capability, probes each provider, heals safe (Tier-1) issues automatically and reports the rest. Same evidence rule as setup.
- `dashboard/` — FastAPI server (`server/server.py`) + SPA (`public/`). Exposes: KB graph (`/api/vaults`, `/api/vaults/<name>/graph`), **live connection discovery (`/api/connections` — `claude mcp list` + gh/gcloud/aws/az auth probes, minus bundle runtime; replaces the hardcoded `KNOWN_SOURCES`/`/api/sources` catalog)**, the block-stream API (`/api/frames`, `/api/frame/<id>/blocks`, `/api/frame/<id>/stream` SSE), panes, shares, `/artifacts/<sid>/<path>`. Managed daemon via the `daemon` capability. **Direction note (2026-06-02):** the append-only block-stream is being superseded *as the default mindframe modality* by an agent-generated **spatial** surface + an **intent channel** (a click carries only an element id; the frame's agent, reached by id, is resumed and resolves meaning from its own transcript). Input is linear; presentation is not. See `docs/onboarding-ux.md`. The block-stream remains the shipped frame plumbing.
- `recipes/mindframe-poc/` — example recipe ships in-tree. `make install-recipes` copies it to `~/.dispatcher/recipes/`.
- `lib/` — `frame.py` (core storage ops), `spawn.py` (CLI), tests. The block-stream contract.
- `mcp/` — `server.py` (FastMCP `write_block` + `set_title`), tests.
- `tests/e2e_wire/` — Tier 1 hermetic integration. `tests/e2e_real/` — Tier 2 live-agent smoke. `tests/e2e_fresh/` — Tier 3 native fresh-install harness.

## Next

**Direction redesigned 2026-06-02 — full model in `docs/onboarding-ux.md`.** Decisions from that session, with honest build status:

- **The web app is the primary interface**, not the terminal. The terminal does only bootstrap (PHASE 1–2); the setup conversation moves into the dashboard. *(designed; `install-flow-v2.md` drafted)*
- **A mindframe is an agent-generated SPATIAL surface, not a linear conversation.** The append-only block-stream is a failed *default* — it forces a chat feel. A mindframe is a composed surface the agent mutates: input is linear, presentation is not. *(decided; block-stream is still the shipped renderer)*
- **The agent is a durable JSON transcript reached by id**, resumed per interaction. UI elements carry only an element id (+ optional runtime context); the resumed agent resolves meaning from its own history. One intent channel, render states `idle → working → awaiting-approval → settled`. *(prototyped end-to-end — see below)*
- **Connections = live discovery** (MCPs + authed CLIs minus bundle runtime), not a hardcoded catalog. *(built: `/api/connections`)*
- **Generative-UI finding:** minimal-prompt agents reproduce/beat the hand-designed setup surface. The UI is no longer the hard part — do **not** build a component library / layout DSL.

Proven this session (prototypes under `slice/` and `dashboard/artifacts/`, local/uncommitted): a **real Claude agent** (taskpilot, subscription, reached by task id, resumed per message) interpreted element-id clicks from its own brief, ran real `gh` (pulled 40 repos), surfaced an honest error and self-corrected, and the surface reflected it live with a consequence-gated approval state.

Open / to harden, priority order:

1. **Reliable agent message-delivery transport.** taskpilot delivers via tmux keystrokes into the Claude TUI; submits drop intermittently (and `hooks/on-prompt.py` was missing — likely install drift, cf. the prefix-drift pattern). A production mindframe needs a real resume channel, not keystroke injection. **This is the keystone blocker.**
2. **Sandboxed-HOME for spawned agents** — `gh`/CLI auth isn't visible to taskpilot agents; they need `GH_CONFIG_DIR` / `$TASKPILOT_HOME` handling.
3. **The intent-channel + spatial-surface runtime** — promote the `slice/` prototypes into the dashboard: generated surface bound to live state, `element-id → resume agent` channel, render states incl. approval.
4. **Home surface** — signals → "tackle this" → spawn a mindframe (the bridge from setup to use).
5. Carryover (lower priority given the modality shift): fresh-install dry-run; live-agent stability; block-stream renderer polish.

### Shipped this slice (chronological)

- ~~`bin/mindframe-write`~~ → MCP server (write_block, set_title)
- ~~SPA block renderer + SSE stream~~ → live in browser
- ~~`mindframe.spawn()` primitive~~ → `lib/frame.py` + `lib/spawn.py` CLI, dispatcher reads `frame:` from recipe.yaml
- ~~Demo recipe~~ → ships in `recipes/mindframe-poc/`, `make install-recipes` installs to `~/.dispatcher/`
- ~~3-tier testing stack~~ → Tier 1 wire (hermetic), Tier 2 real-agent smoke (manual), Tier 3 fresh-install (native, no Docker)
- ~~CI matrix Linux/macOS/Windows~~ — caught and fixed 6 cross-platform bugs on first run
- ~~Dashboard as managed daemon~~ — `daemon` capability declared, setup skill + install.txt updated to register via daemon-manager (intent-based, not tool-hardcoded)

## Current dashboard state (as of 2026-06-02)

- `dashboard/server/server.py` — FastAPI. Endpoints: `/api/health`, `/api/panes`, block-stream (`/api/frames`, `/api/frame/<id>`, `/api/frame/<id>/blocks`, `/api/frame/<id>/stream`, `POST /api/frames`), `/api/suggestions`, `POST /api/prompt`, `POST /api/dashboard-event` (dispatcher proxy, bearer from `~/.mindframe/secrets/dispatcher-bearer.token`), vaults (`/api/vaults`, `/api/vaults/<name>/entries|graph`, `POST .../share`), sources (`/api/sources`, connect/disconnect) + **`/api/connections` (live discovery, the real replacement for the `KNOWN_SOURCES` catalog)**, shares, `/artifacts/<sid>/<path>`, SPA fallback.
- `dashboard/public/` — Space Grotesk / Source Serif 4 / JetBrains Mono, warm dark, gold accent. Routes `/` (boards index) and `/m/<id>` (typed block renderer).
- Bearer file at `~/.mindframe/secrets/dispatcher-bearer.token` (chmod 600); matches the dispatcher's `DISPATCHER_INGEST_TOKEN`.
- **2026-06-02 redesign prototypes (local, NOT shipped — `dashboard/artifacts/` is gitignored):** `kb-live` (hand-built spatial first-run surface: you-graph + schema rail + connections rail), `genui-1/2/3` (minimal-prompt agent-generated surfaces), and `slice/` (the live intent-channel experiments incl. one real taskpilot-agent run). Reference material for the spatial-mindframe direction; Wizard-of-Oz except the `slice/live` real-agent run.
