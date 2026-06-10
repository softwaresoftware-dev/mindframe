# Mindframe — Agentic Stack

Mindframe gives an organization a knowledge base of how it works and AI agents
that act on it. It is a **packaging + onboarding layer**: it ships skills, the
customer-domain knowledge-base schema (`docs/kb-schema.md`), a `requires` list,
and one piece of business logic it owns directly, the **dashboard**. Everything
else is a provider the bundle composes.

The whole system is six runtime layers. Read them top to bottom: the human
touches the **Surface**; the Surface drives the **Agent runtime**; events arrive
through **Event ingress**; agents draw on **Knowledge**, talk over the **Mesh**,
and reach the world through **Perception**.

## The six layers

| Layer | What runs it | State |
|---|---|---|
| **Surface** | the dashboard: one FastAPI server (`dashboard/server/server.py`) + SPA that serves every mindframe at `/m/<id>` | `~/.mindframe/frames/<id>/index.html` |
| **Agent runtime** | `taskpilot` spawns a persistent tmux-backed `claude`; the starter prompt and every later message are delivered over the Mesh (not tmux keystrokes) | transcript in `~/.claude/projects/<encoded-cwd>/` |
| **Event ingress** | `dispatcher` (`:8911`): dedupe → `channels.yaml` static route → LLM fallback → spawn an ephemeral agent | `~/.dispatcher/events.db` |
| **Knowledge** | a single vault: markdown + frontmatter, the 4-layer schema in `docs/kb-schema.md` *(under redesign — see note)* | `~/.mindframe/vault` (hardcoded) |
| **Mesh** | `session-bridge` (`:8910`): agent↔agent↔human messaging. Also the Agent-runtime delivery channel | transient |
| **Perception** | `claude-browser-bridge` + adopt-on-install MCPs (github / sentry / slack / …), live-probed via the Surface's `/api/connections` | — |

Each layer is a separate plugin or MCP, bound by **capability**, except the
Surface and the Knowledge vault, which mindframe owns directly. The
`softwaresoftware` resolver picks a provider per capability at install time, so
any composed layer is swappable per customer.

Capability → provider for each layer:

| Layer | Capability | Provider |
|---|---|---|
| Surface | *(mindframe owns it)* | `dashboard/` |
| Agent runtime | `agent-spawning` | `taskpilot` (pulls in `terminal-ops` → `tmux-session`, `daemon` → `daemon-manager`) |
| Event ingress | `event-routing` | `dispatcher` |
| Knowledge | *(mindframe owns it)* | the vault — plain files at `~/.mindframe/vault` |
| Mesh | `session-mesh` | `session-bridge` |
| Perception | `browser-automation` | `claude-browser-bridge` + adopted MCPs |

`notification` is **not** a bundle capability. An agent that wants to notify a
human uses whatever notification tool is available and falls back to writing an
artifact file if none is.

## How a request flows

The push path, end to end:

```
external event ──▶ Event ingress (dispatcher :8911)
                      dedupe → channels.yaml → LLM fallback
                      └─ spawn:<recipe> → POST :8912/tasks/create_and_spawn
                                                │
                                          Agent runtime (taskpilot)
                                          tmux-backed claude, fed over the Mesh
                                                │
                          ┌─────────────────────┼─────────────────────┐
                          ▼                     ▼                      ▼
                      Knowledge            Perception              output
                      (the vault)          (browser-bridge       (artifact; notify
                                            + adopted MCPs)       if a tool exists)
```

The interactive path is the same runtime, entered from the top: the operator
opens the **Surface**, creates or messages a mindframe, and the Surface delivers
that message to the Agent runtime through the same `:8912` daemon. A mindframe is
a persistent agent that owns one HTML page it rewrites in place plus a message
box; the Surface serves the page and proxies messages. There is no second
"interactive" stack.

## Invariants

- **Manifest-first.** Bundle composition lives in `requires`. The only business
  logic mindframe owns is the Surface (`dashboard/`).
- **Every layer is a plugin or an MCP**, bound by capability. Skills reference a
  capability by intent ("spawn a long-running agent"), never by provider name,
  so any provider is swappable per customer.
- **The Mesh is the agent transport.** `taskpilot` does not type into the TUI;
  it POSTs the prompt and every message to `session-bridge :8910/sessions/<id>/message`.
  Agent runtime and Mesh are coupled by this.
- **Single vault, single Surface.** One `~/.mindframe/vault`, one dashboard
  server for every mindframe. No multi-vault catalog, no sharing, no per-frame
  server.
- **Agents recommend; humans act.** Anything irreversible or outward-facing is
  drawn on the mindframe's page as a pending action and waits for the operator
  to confirm in a message.
- **Subscription auth only.** Every `claude` process runs on the Claude Code
  subscription. No `ANTHROPIC_API_KEY` anywhere in the bundle.

## Cross-cutting concerns (not layers)

These act *on* the stack rather than being part of it:

- **Setup** — `/mindframe:setup`. A terminal bootstrap births the operator's
  first mindframe, which runs onboarding inside the Surface: it probes the
  environment, inherits the operator's identity, assembles the vault schema,
  bootstraps the vault, and surfaces the first signal (event wiring is a later
  chapter, driven from the surface). Model in
  `docs/onboarding-ux.md`; flow in `setup/install.txt` (the repo source of
  truth, deployed verbatim to https://mindframe.softwaresoftware.dev/install.txt)
  and `setup/brief.md`.
- **Doctor** — `/mindframe:doctor`. Walks the `requires` list capability by
  capability, probes each provider, heals safe issues, reports the rest with
  evidence.
- **Open** — `/mindframe:open`. The "open up mindframe" entry point: discovers
  the dashboard's port, brings the `mindframe-dashboard` daemon up if it is
  down, then opens the operator's browser to the home (the hub graph). Skill in
  `skills/open/`.
- **The work** — what a mindframe agent produces (a triage, a review, a report,
  an answer). The agent does it directly: interactively in its surface, or as an
  ephemeral agent the dispatcher spawns per event from an operator-wired recipe.
  Mindframe ships no pre-built workflow artifacts; the work lives in what the
  agent does, grounded in the vault, not in a library of packaged skills.

## In-directory artifacts

- `docs/architecture.md` — the six layers in depth: what runs each, the state it
  holds, and the runtime flow. The canonical architecture reference.
- `docs/interfaces.md` — the contracts *between* layers: the dispatcher event
  API, `channels.yaml`, the recipe contract, the agent-runtime spawn interface,
  the Mesh tools, and the Surface app API.
- `docs/product.md` — what the product is and who it is for.
- `docs/kb-schema.md` — the Knowledge layer's schema library: the meta-schema,
  the core entities, and the per-install `schema.yaml`. **Under redesign in a
  separate effort** — treat as descriptive of today's vault, not final.
- `docs/onboarding-ux.md` — the setup UX model: agent-led onboarding, the
  connections model (live discovery, not a catalog), and the surface model (one
  HTML page the agent rewrites + a message box).
- `setup/install.txt` — the canonical install + setup flow. Source of truth for
  the hosted https://mindframe.softwaresoftware.dev/install.txt (deployed
  verbatim).
- `setup/brief.md` — the setup mindframe's standing brief (a template
  `install.txt` fills in).
- `skills/setup/`, `skills/doctor/`, `skills/open/`, `skills/connect/` — the
  cross-cutting skills (onboard, diagnose, open the home, connect a tool).
- A connection is an MCP or a **connector skill** — a `SKILL.md` with a
  `connection:` fingerprint, living in `~/.claude/skills/`. `/mindframe:connect`
  researches a tool's door, authors the connector, and verifies it; the skill
  carries a worked example per door kind (cli / mcp / http-api / sql / browser /
  file). Nothing is pre-shipped — connectors are authored per operator.
- `dashboard/` — the Surface: FastAPI server (`server/server.py`) + SPA
  (`public/`). See `dashboard/README.md`.
- `dashboard/tests/` (unit — vault graph), `tests/e2e_wire/` (Tier 1, hermetic
  surface-API wire tests with a stub taskpilot daemon), `tests/e2e_fresh/`
  (Tier 3, fresh-install invariants + dashboard boot). CI
  (`.github/workflows/test.yml`) runs all three on 3 OS × py3.11/3.12.

## Knowledge layer — redesign in progress

The Knowledge layer is being reworked in a separate effort. Today: the vault is
a single local directory hardcoded at `~/.mindframe/vault` (the dashboard's
`VAULT_DIR`), markdown + frontmatter, organized by the 4-layer schema in
`docs/kb-schema.md`. There is no separate knowledge-capture subsystem; the vault
is written by setup's bootstrap and by mindframe agents as they work. The vault
is owned directly by mindframe as plain files — there is
no `knowledge-base` capability binding and no external provider (the
`knowledge-base` plugin was archived 2026-06-06). Treat `kb-schema.md` as
descriptive of today's vault, not final, until that redesign lands.

## Status, decisions, open threads

Tracked in this repo's git history and docs. (The former project-vault +
librarian tracker was archived 2026-06-06 with the `knowledge-base` plugin;
mindframe owns its knowledge layer directly now.)
