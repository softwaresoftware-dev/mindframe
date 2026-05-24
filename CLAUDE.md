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
6. **Dashboard** — `taskboard` (sibling plugin). Pull-path: probes services, agents, daemons, sites, sessions, telemetry and renders status. Pairs with dispatcher — taskboard is the eyes (pull), dispatcher is the ears (push).
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
- `dashboard/` — FastAPI server that serves the SPA shell, exposes artifact HTML under `artifacts/<sid>/`, and snapshots to `/s/<id>` shares. The persistent dashboard agent was removed 2026-05-21. SPA is now a boards-index + per-mindframe detail view, design-system aesthetic. Buttons inside agent-authored HTML fire dispatcher events via `/api/dashboard-event` (server holds the bearer). Still uses iframes for pane content — block-stream renderer is the next slice. See `dashboard/README.md` and `docs/mindframe-block-stream-api.md`.

## Next

Build the **block-stream renderer**. Spec is at `docs/mindframe-block-stream-api.md` (938 lines, converged across 6 rounds of gap-patching). What to ship in order:

1. ~~`bin/mindframe-write`~~ — **shipped as an MCP** (`mcp/server.py`). `write_block` + `set_title`, UUIDv7 inline polyfill, auto-resolves mindframe_id from cwd / `$MINDFRAME_ID`. 33 hermetic tests. Auto-loads via plugin.json `mcpServers`.
2. ~~SPA block renderer + SSE stream~~ — **done** (2026-05-24). Server: `GET /api/frames`, `/api/frame/<id>`, `/api/frame/<id>/blocks?since=`, and `/api/frame/<id>/stream` (SSE, polling tail @ 250ms, replay-from-Last-Event-ID for free via UUIDv7). SPA: typed renderer per block type (text/markdown, code, table, button-row, input, summary, divider, custom-html, image, url-card, supersedes, redact, close, user-action). Verified end-to-end in the browser — appending a block via MCP appears in the live page without reload. Known rough edges: inline markdown is minimal (no GFM), no test coverage on the SSE endpoint, supersedes/redact visual paths untested, large historical replay isn't paginated.
3. ~~Spawn primitive + dispatcher integration~~ — **done** (2026-05-24). Mindframe is *not* a runtime; it's a storage/UI convention plus a thin "prep before spawn" function. `lib/frame.py` is the single source of truth for frame ops (create, append, set_title, mint id, uuid7) — used by the MCP, the dashboard's `POST /api/frames`, and the spawn CLI. `lib/spawn.py` is a thin CLI wrapper. Dispatcher reads a `frame:` block from recipe.yaml; if present, it shells out to `lib/spawn.py` to mint the frame, then passes `--name=<id> --cwd=<frame_dir>` to taskpilot's spawner_cli. **Zero taskpilot changes** — `--cwd` already existed. 49 dispatcher tests + 67 mindframe tests passing.
4. Recipe convention: a recipe becomes a mindframe recipe by adding `frame: {title, seed_block, tags}` to its recipe.yaml. Title/seed_block fields go through the same `{{placeholder}}` composer as brief.json (so `frame.title: "OOM in {{service}}"` works alongside `brief.context.service: "{{service}}"`).
5. End-to-end smoke test from an *actual* webhook → dispatcher → frame → live SPA. (Dispatcher's frame-spawn wire is integration-tested against the real CLI; the agent-writes-real-blocks-from-vault-context loop is the next thing to prove.)
6. Home surface — signals + "tackle this" buttons that fire spawn-mindframe events. Closer to taskboard's domain.

The home / static-taskboard surface is the slice *after* this — surfaces signals (calendar, sentry, on-call) with "tackle this" buttons that fire spawn-mindframe events. That part is closer to taskboard's domain.

## Current dashboard state (as of 2026-05-23)

- `dashboard/server/server.py` — FastAPI: `/api/health`, `/api/panes`, `/api/dashboard-event` (dispatcher proxy reading `~/.mindframe/secrets/dispatcher-bearer.token`), `/api/save` + `/s/<id>` shares, `/artifacts/<sid>/<path>` artifact serving, SPA fallback for everything else. No taskpilot or session-bridge dependency.
- `dashboard/public/` — Inter-free design-system stack (Space Grotesk / Source Serif 4 / JetBrains Mono), warm dark palette, gold accent. Two routes: `/` (boards index) and `/m/<id>` (mindframe detail).
- Bearer file at `~/.mindframe/secrets/dispatcher-bearer.token` (chmod 600); matches the dispatcher daemon's `DISPATCHER_INGEST_TOKEN` env so button events succeed end-to-end.
- 21 artifacts under `dashboard/artifacts/`: 19 historical (legacy dashboard-agent leftovers, ignore for demos) + `demo-incident-001` (OOMKilled in payments-api) + `demo-customer-review` (Acme QBR prep). Latter two are hand-authored HTML used for visual iteration.
