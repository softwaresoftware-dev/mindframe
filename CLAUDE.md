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

- `docs/kb-schema.md` — the KB schema library: the fixed meta-schema, the core entities, and the per-install `schema.yaml` manifest format (domain entities are synthesized as `custom` at setup, not pre-shipped). Read before building setup or deliverable skills.
- `docs/onboarding-ux.md` — **the onboarding UX model + decisions (2026-06-02).** The concept ladder (knowledge base → schema → connections → watches → signals → mindframes), the three-zone first-run surface, agent-led + human-in-the-loop principles, the **v0 interaction model** (agent rewrites a full HTML page + message box; the intent primitive is cut), and the generative-UI direction. **Read this before touching setup or the dashboard.**
- `surface/` — **the v0 mindframe substrate** (shipped). `server.py` owns the shell + message rail and serves the agent's `index.html`; `shell.html` is the chrome. Env-driven (`MF_FRAME_DIR`/`MF_TASK_ID`/`MF_PORT`/`MF_DAEMON`). This is what a mindframe IS now.
- `setup/brief.md` — **the setup mindframe's standing brief** (a template `install.txt` fills in). The onboarding arc on the v0 substrate: this-is-you → interview/schema → discover connections via shell → connect+synthesize → first signal.
- `skills/setup/` — `/mindframe:setup`: delegating stub → fetches the hosted `install.txt` (now the UI-based bootstrap → birth setup mindframe → hand off).
- `skills/doctor/` — `/mindframe:doctor`: bundle self-diagnostic. Walks the `requires` list capability by capability, probes each provider, heals safe (Tier-1) issues automatically and reports the rest. Same evidence rule as setup.
- `dashboard/` — FastAPI server (`server/server.py`) + SPA (`public/`). Exposes: the single-vault KB (`/api/vault`, `/api/vault/entries`, `/api/vault/graph`), **live connection discovery (`/api/connections` — `claude mcp list` + gh/gcloud/aws/az auth probes, minus bundle runtime)**, the System overview feeds (`/api/events`, `/api/agents`, `/api/capabilities`), surface mindframe listing (`/api/frames` — frame dirs holding an `index.html`), panes, `/artifacts/<sid>/<path>`. Managed daemon via the `daemon` capability. **Block-stream removed (2026-06-04):** a mindframe is the **v0 surface substrate** (`surface/`) — the agent owns one HTML page it rewrites + a message box. Per-mindframe viewing and prompt→surface creation in the dashboard are the next migration step. See `docs/onboarding-ux.md`.
- **Single-vault (2026-06-05):** the vault is **static at `~/.mindframe/vault`** — not configurable, and there is no override. The dashboard hardcodes that path (`VAULT_DIR`); nothing reads it from config. No `vaults.yaml` catalog, no `vault_path` userConfig/settings key, no `lib/vault.py` resolver, no `vault_sharing/` agent (share + accept) — all removed. One deployment, one vault, no inter-org sharing. (`lib/` now holds only `__init__.py`/`tests/`; block-stream `frame.py`/`spawn.py` and the `mcp/` server were removed in the 2026-06-04 surface migration — the bundle ships no MCP, surface agents write `index.html` with the plain Write tool.)
- **Vault agents removed (2026-06-05):** `vault_keeper/` (write side) and `vault_query/` (read side) — the automated knowledge-capture loop — were deleted pending a redesign. The bundle ships no vault agents right now; the vault is populated by setup's bootstrap and operator-authored deliverables. install.txt PHASE 9.5 is a deferred placeholder.
- `tests/e2e_wire/` — Tier 1 hermetic integration. `tests/e2e_real/` — Tier 2 live-agent smoke. `tests/e2e_fresh/` — Tier 3 native fresh-install harness.

## Next

**Direction redesigned 2026-06-02 — full model in `docs/onboarding-ux.md`.** Status:

- **The web app is the primary interface**, not the terminal. The terminal does only bootstrap; the setup conversation moves into the surface. *(shipped: `install.txt` migrated to bootstrap → birth setup mindframe → hand off)*
- **A mindframe is the v0 model**: the agent owns one HTML page it rewrites + a message box. The block-stream AND the interim intent-primitive are both cut. *(shipped: substrate at `surface/`; proven live)*
- **Capabilities are skills / MCPs / CLIs, not KB records** — self-injecting at startup; a CLI capability is a skill whose body is the recipe. The KB holds what the org IS, never the capability registry; no `Connection` entity. *(decided)*
- **Connections = live discovery** (MCPs + authed CLIs minus bundle runtime), not a hardcoded catalog. *(built: `/api/connections`; setup discovers via shell)*
- **Generative-UI finding:** minimal-prompt agents reproduce/beat a hand-designed surface. Don't build a component library / layout DSL.

Open / to harden, priority order:

1. **Reliable agent message-delivery transport.** taskpilot delivers via tmux keystrokes; submits drop intermittently. A production mindframe needs a real resume channel — e.g. `claude --continue` in the per-task cwd (verified to reload plugins; would also kill the `state.json` frailty by making the transcript the durable state). **Keystone blocker.**
2. **Run the migrated `install.txt` end-to-end** in an isolated `HOME` (faithful, zero blast radius), then deploy the staticsite so the hosted URL serves the new flow (it currently still serves the old one).
3. **URL delivery polish** — dynamic port (not the fixed `5180`), a persistent/re-findable URL, and decide whether the setup URL becomes the durable "home" or hands off again.
4. **Home surface** — signals → "tackle this" → spawn a mindframe (the bridge from setup to use).
5. Deferred / lower priority: taskpilot "inherit all user-scope plugins/MCPs" (off critical path); block-stream + intent-primitive runtime are legacy.

### Shipped this slice (chronological)

- ~~`bin/mindframe-write`~~ → MCP server (write_block, set_title)
- ~~SPA block renderer + SSE stream~~ → live in browser
- ~~`mindframe.spawn()` primitive~~ → `lib/frame.py` + `lib/spawn.py` CLI, dispatcher reads `frame:` from recipe.yaml
- ~~Demo recipe~~ → removed; mindframe ships no recipes (the `recipes/` dir + `install-recipes` target were removed 2026-06-05). Operators author their own during `/mindframe:setup`.
- ~~3-tier testing stack~~ → Tier 1 wire (hermetic), Tier 2 real-agent smoke (manual), Tier 3 fresh-install (native, no Docker)
- ~~CI matrix Linux/macOS/Windows~~ — caught and fixed 6 cross-platform bugs on first run
- ~~Dashboard as managed daemon~~ — `daemon` capability declared, setup skill + install.txt updated to register via daemon-manager (intent-based, not tool-hardcoded)

## Current dashboard state (as of 2026-06-05)

- `dashboard/server/server.py` — FastAPI. Endpoints: `/api/health`, `/api/panes`, surface mindframes (`/api/frames` lists frame dirs holding an `index.html`; **`POST /api/frames/create`** mints a frame dir + spawns a persistent agent via taskpilot's `spawner_cli.py`, discovered from `installed_plugins.json`), **per-mindframe surface (`/m/<id>` shell; `/api/frame/<id>/page|rev`, `POST /api/frame/<id>/message` → taskpilot daemon `:8912`, `/api/frame/<id>/activity` tails the agent transcript)**, System overview feeds (`/api/events`, `/api/agents`, `/api/capabilities`), `POST /api/dashboard-event` (dispatcher proxy), the **single vault (`/api/vault`, `/api/vault/entries`, `/api/vault/graph`)**, sources (`/api/sources` + **`/api/connections`** live discovery), `/artifacts/<sid>/<path>`, SPA fallback. **Single-vault 2026-06-05:** the multi-vault list/share/accept endpoints (`/api/vaults`, `/api/vaults/<name>/share`, `/api/shares/*`, `/api/github/owners`) were removed. **Block-stream endpoints removed 2026-06-04.**
- `dashboard/public/` — Space Grotesk / Source Serif 4 / JetBrains Mono, warm dark, gold accent. SPA routes `/` (home: create-a-mindframe box + your mindframes + the single knowledge base + sources) and `/system` (bundle overview). `surface.html` is the per-mindframe shell served at `/m/<id>` — an iframe over the agent's page + message rail + cognition log (multi-tenant fold-in of `surface/server.py`; the agent's transcript lives at `~/.claude/projects/<encoded-cwd>/` for a real-HOME spawn or `~/.taskpilot/<id>/.claude/projects/` for an isolated one).
- Bearer file at `~/.mindframe/secrets/dispatcher-bearer.token` (chmod 600); matches the dispatcher's `DISPATCHER_INGEST_TOKEN`.
- **2026-06-02 redesign prototypes (local, NOT shipped — `dashboard/artifacts/` is gitignored):** `kb-live` (hand-built spatial first-run surface: you-graph + schema rail + connections rail), `genui-1/2/3` (minimal-prompt agent-generated surfaces), and `slice/` (the live intent-channel experiments incl. one real taskpilot-agent run). Reference material for the spatial-mindframe direction; Wizard-of-Oz except the `slice/live` real-agent run.
