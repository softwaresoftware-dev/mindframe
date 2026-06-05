# install.txt — outline

*The canonical, copy-paste-once entry point. Customer visits mindframe.softwaresoftware.dev, copies this, pastes into Claude Code (or any agent terminal). End state: deployment running, vault built, events wired, dashboard up.*

This is the outline / argument layer — what each phase does, what it asks, what it produces. Prose comes after the structure is settled.

---

## PHASE 0 — Preamble (rules the agent reads first)

Tells the agent reading this what kind of document it is and how to behave.

- **Identity.** "You are installing Mindframe for a real human. They launched Claude Code, pasted this doc, and expect end-to-end install + setup with as little manual intervention as possible. You drive the entire flow — including the shell commands at the start, via Bash."
- **Conversation, not script.** Stop and ask. Surface evidence. Never fabricate. (Same evidence rule the current setup SKILL.md already uses — pull it forward.)
- **Mindframe inherits the operator's identity. It has no credentials of its own.** Mindframe runs on the operator's machine as the operator. Anything the operator can do via `gh`, `az`, `gcloud`, `aws`, `sentry-cli`, or any already-loaded MCP, mindframe can do *by invoking those same tools*. Mindframe never asks for a token that an existing tool already holds. If a system has no working CLI/MCP path, the operator runs the provider's own login flow (`gh auth login`, `az login`, `gcloud auth login`) — typically a browser OAuth handoff the operator completes on their machine. The token lands in the provider's own credential store, not in mindframe.
- **Generated secrets are file-handoff, never chat.** Tokens *mindframe creates* (e.g. the dispatcher bearer in PHASE 7.6) are written to a file under `~/.mindframe/secrets/` (`chmod 700` dir, `chmod 600` files). The agent prints the path and a `pbcopy`/`xclip` command, never the value. Operator copies via clipboard and pastes into the source system's webhook config. Rationale: the conversation transcript, Anthropic's prompt cache, and subagent spawns all become attack surfaces the moment a secret enters chat — even one mindframe generated itself.
- **Idempotency.** Every phase detects "already done" and skips. Re-pasting the URL is safe.
- **Resumability.** Each phase records its completion in `~/.claude/settings.json` under `pluginConfigs.mindframe.install_state` (sibling to `options`). Derive done-ness from the world first — vault exists, daemon green, config key set, file on disk — and use `install_state` only for things that aren't observable from the world (operator confirmed scope, operator picked events to wire). The next paste reads `install_state` to skip what's done.
- **No Anthropic API key.** Mindframe runs on the Claude Code subscription. Never ask for one.
- **User-scope by default.** Every MCP mindframe installs or registers goes in `~/.claude/settings.local.json` (user-scope local). Never project-scope. The deployment is org-wide, not tied to a single repo — the operator should be able to run `claude` anywhere on their machine and have mindframe available. Same rule for plugin enablement and credentials: user-scope.
- **Stop conditions.** If a probe fails, ask the user. Don't retry blindly. Don't proceed past a failed credential probe.

Produces: nothing on disk. Sets the agent's frame for everything below.

---

## PHASE 0.W — Windows operators only (skip if Linux or macOS)

Mindframe runs on Linux + macOS natively. **Windows operators run it inside WSL2.** Native Windows is not supported because taskpilot's agent spawner currently depends on tmux. Everything else in the bundle is cross-platform; the WSL hop only affects the install agent at runtime.

Before going to PHASE 1, verify the operator is in the right environment:

- **0.W.1 — In WSL already?** Probe `$WSL_DISTRO_NAME` and `uname -r` (contains `microsoft`). If yes, proceed to PHASE 1 like Linux.
- **0.W.2 — Native Windows preflight.** Tell the operator they need WSL2: `wsl --install Ubuntu` from PowerShell, then `sudo apt install -y curl tmux git python3 python3-pip nodejs`, then `npm install -g @anthropic-ai/claude-code`, then re-launch Claude Code inside the WSL session and re-paste install.txt.
- **0.W.3 — Keep WSL alive for daemons.** WSL2 shuts down ~8s after the last shell exits, which kills daemons. Tell the operator (note for later, do not enforce now): leave one terminal open, OR enable systemd in `/etc/wsl.conf` + `wsl --shutdown-timeout` in `%USERPROFILE%/.wslconfig`, OR add `wsl ~` to Windows startup.
- **0.W.4 — Notifications.** `notify-send` doesn't work in WSL without WSLg + a display server. When PHASE 5 resolves the notification capability, prefer `notify-slack` or `notify-email` over `notify-linux` for WSL deployments.

Produces: a Linux-shaped runtime environment (either native Linux/macOS, or WSL2 on Windows) ready for PHASE 1.

---

## PHASE 1 — Bootstrap the marketplace + resolver

The operator launched `claude` and pasted this doc. Agent drives via Bash.

```
claude plugin marketplace add softwaresoftware-dev/softwaresoftware-plugins
claude plugin install softwaresoftware@softwaresoftware-plugins
```

Then ask the operator to type `/reload-plugins` — the built-in reload has no Bash equivalent, and the running session needs it to pick up the newly installed `softwaresoftware` skill. This is the one unavoidable manual step in the bootstrap.

Idempotent: marketplace add is a no-op if already added; plugin install reports already-installed cleanly; reload is always safe.

Produces: `softwaresoftware` resolver loaded and callable as a skill. Ready for PHASE 2.

---

## PHASE 2 — Install mindframe + dependencies

Inside Claude Code, with `softwaresoftware` now loaded.

**Step 2.1 — run the resolver.** Agent invokes the `softwaresoftware:install` skill (callable directly, since it's a plugin skill not a built-in slash command) with target `mindframe`. The resolver probes the host, picks providers for `agent-spawning`, `session-mesh`, `knowledge-base`, `event-routing`, `status-dashboard`, `browser-automation`, `notification`, installs in dependency order, starts daemons.

**Step 2.2 — reload plugins.** Agent asks the operator to type `/reload-plugins` so mindframe's own skills (`/mindframe:setup`, `/mindframe:doctor`, plus whatever deliverable skills ship) are available. Second unavoidable manual step.

Asks: `/reload-plugins` (operator-typed). Otherwise nothing — the resolver is deterministic given the host.

Produces: every required provider installed, daemons up (dispatcher-ingress :8911, taskpilot :8912, session-bridge :8910). `~/.claude/settings.json` lists enabled plugins. mindframe's own skills loaded.

---

## PHASE 3 — Collect deployment config

One value mindframe needs.

- `deployment_name` — labels this deployment everywhere (dashboard breadcrumb, grounding prompt).
- The KB vault is **not asked for** — it is always `~/.mindframe/vault`, created in a later phase.

Asks: one line for the deployment name. Writes it to `~/.claude/settings.json` → `pluginConfigs.mindframe.options`. The vault path is fixed (`~/.mindframe/vault`) so there is nothing to reconcile.

Checkpoint: `deployment_name` non-empty in settings.json.

---

## PHASE 4 — Environment discovery

Probe the environment to learn what systems the org runs and what domain nouns it has. The output is a picture — discovered systems + candidate domain entities — that PHASE 6 turns into `custom` entity types in the vault schema. Mindframe ships no pre-baked domain bundles; the agent synthesizes the types this org actually needs.

### Step 4.1 — Probe the environment

Deterministic probe pass against the local machine. Evidence-or-it-didn't-happen rule from the current setup skill applies.

A. **Installed MCPs.** Read `~/.claude/settings.local.json` (user-scope, where MCPs live) and check `claude mcp list` for the runtime view. Scan nearby project-scope `.local.json` files for *discovery only* — mindframe writes user-scope.
B. **Binaries on PATH + their auth state.** `command -v` probes for common dev/cloud CLIs (`gh`, `kubectl`, `gcloud`, `aws`, `az`, `sentry-cli`, `pwsh`, `dotnet`, etc.). For any CLI found, run its native auth-status command (`gh auth status`, `az account show`, `gcloud auth list`, `aws sts get-caller-identity`, etc.) and capture exit code only — stdout is fine to surface (these print account/org info, not tokens). PHASE 5 reuses this directly: an authed CLI here means mindframe already has that system's access.
C. **Tool config files.** Non-secret fields from `~/.gitconfig`, `~/.aws/config`, `~/.azure/config`, `~/.kube/config`, `~/.config/gh/hosts.yml`, etc. **Never read credential values.**
D. **Code-root signals.** Shallow scan of `~/projects`, `~/code`, `~/src`, `~/work`, `~/dev` for `.git/config` remotes, container manifests, language manifests, `.github/workflows/`, `azure-pipelines.yml`, `bicep/`, `.csproj`, etc.
E. **Recent transcripts.** Keyword-grep ~20 most-recently-modified transcript files in `~/.claude/projects/<encoded-cwd>/`. Cite files; never copy content.
F. **Direct elicitation.** Ask the operator: *"What software does your org use day-to-day?"* Free text, comma-separated, follow-up question if vague. Capture verbatim — it surfaces domain nouns no local probe catches: SaaS-only signals (Power BI / Ignition / SAP / Enertia / Teams / SharePoint) that imply entity types worth minting.

### Step 4.2 — Draft candidate domain entities

From the probe results + the free-text answer, draft the list of domain-specific entity types this org needs beyond the 10 core. A GitHub org implies `repository` + `service`; Slack implies `channel`; an oil & gas SCADA stack implies `well` + `meter`. Each candidate is a name + a guessed layer; the actual definition (fields, FKs) is settled with the operator in PHASE 6. Nothing is written yet — this is a held list, not a schema.

### Step 4.3 — Present candidates, operator confirms

Present an evidence table grouped by candidate entity:

| Candidate entity | Layer | Evidence | Recommendation |
|---|---|---|---|
| service | thing | `gh` on PATH; `github` MCP registered; `.github/workflows/` in 3 repos | mint |
| repository | thing | same | mint |
| channel | thing | `slack` MCP registered | mint |
| well | thing | operator mentioned "Ignition" + "Quorum" in free-text | mint |

Operator confirms, aliases (a "squad" is just core `team`), or skips. Free to add an entity no probe matched (captures SaaS-heavy shops) or drop one the probes suggested.

### Step 4.4 — Record the confirmed list

Hold the confirmed candidate-entity list in agent context for PHASE 6, where each becomes a `source: custom` entry in `<vault>/schema.yaml`. Also hold the in-scope data systems (which feed PHASE 5's reachability probes).

### Asks

- Free-text discovery answer (step 4.1-F)
- Candidate-entity confirmation (step 4.3)

### Produces

- Confirmed list of candidate custom entities, held in agent context
- In-scope data systems, used by PHASES 5–6

---

## PHASE 5 — Inherit identity + reachability probes

Mindframe doesn't collect credentials. It reuses what the operator already has. PHASE 5 walks the in-scope systems from PHASE 4 and, per system, picks the cheapest working auth path — without ever handling the token.

**Resolution order, per system:**

1. **Loaded MCP that's already authed.** PHASE 4 already enumerated loaded MCPs (e.g. `gmail-mcp`, `slack`, `claude-browser-bridge`). If a system has an MCP that's wired up in `~/.claude/settings.local.json` and responds to a list-one-thing call, that's the auth path. Mindframe uses the MCP. Nothing more to do.
2. **CLI on PATH that's already authed.** Probe with the provider's own auth-status command — `gh auth status`, `az account show`, `gcloud auth list`, `aws sts get-caller-identity`, `sentry-cli info`, etc. If exit code is 0, use the CLI. Whatever the operator already logged into, mindframe inherits.
3. **CLI on PATH, not authed.** Ask the operator to run the provider's login flow themselves. Most are browser OAuth handoffs that pop a window the operator completes on their machine (`gh auth login`, `az login`, `gcloud auth login`, `aws sso login`). Token lands in the provider's own credential store. Mindframe never sees it. Re-probe after.
4. **No CLI, no MCP.** For POC, surface this as a gap and skip the system. Mark out-of-scope in the deployment config; PHASE 6/7 won't try to reach it. Raw-PAT collection is deferred — not in POC.

**Validation probe, per system** — runs whatever the resolved tool offers. Examples:

```bash
gh auth status                              # GitHub
az account show --output none               # Azure
gcloud auth list --filter=status:ACTIVE     # GCP
aws sts get-caller-identity --output text   # AWS
sentry-cli info                             # Sentry
```

The agent asserts on exit code. Stdout is fine to surface (these tools print account/org info, not tokens). Stderr on failure is fine too.

**On failure** — ask. Don't retry blindly. The operator may need to switch accounts, expand a scope, or accept a tenant. Surface the probe command and exit code; let them diagnose.

### Asks

- Operator to run their provider's own login flow if a CLI/MCP probe fails. Browser OAuth where applicable.

### Produces

- Per in-scope system: a resolved auth path (`mcp:<name>`, `cli:<binary>`, or `skipped`) recorded in the deployment config.
- A green probe per resolvable system. Unresolvable systems marked out-of-scope.
- Zero secrets stored by mindframe.

### What never happens

- Mindframe never asks for a raw PAT or token.
- No `~/.mindframe/secrets/` directory exists yet (PHASE 7.6 creates it only for the dispatcher bearer it generates itself).
- No credential value enters chat, a tool call argument, or a subagent spawn.

### POC simplification

Raw-PAT-via-file-handoff is *possible* (PHASE 0 spells out the pattern for generated tokens) but isn't part of POC. If a system needs it (no CLI, no MCP, no OAuth flow), defer it. We'll learn from design-partner feedback which systems show up uncovered before building the fallback.

---

## PHASE 6 — Assemble schema, bootstrap KB

Three sub-steps.

**6a. Assemble `<vault>/schema.yaml`.** Always include the 10 core entities (mindframe's universal baseline, defined in `docs/kb-schema.md`). Then walk the operator through each candidate domain entity confirmed in PHASE 4 (alias-or-mint per noun): pick its layer, fields, and FKs against the meta-schema, and write it as `source: custom`. Add custom fields to a core entity where the domain needs them (e.g. `Incident.fix_pr`).

**6b. Bootstrap entity notes from real source systems.** Per-source auto-extraction: GitHub org → repos + services; Slack → people + channels; Sentry → recent incidents. Stub notes presented for operator confirmation.

**6c. Manual seeding.** Top Products, active Projects, foundational Decisions, Conventions, Glossary terms.

Produces: `<vault>/` is a git repo with `schema.yaml`, `CATALOG.md`, `CLAUDE.md`, `Glossary.md`, and entity directories populated. Every pass commits.

Asks: a lot. This phase is the slowest and most conversational.

Resolves D-VAULT-SCHEMA-MISMATCH and D-NO-VALIDATOR by making the assembly explicit and writing it to disk where future probes can verify it.

---

## PHASE 7 — Guided authoring: first event source, first agent, simulated event

The most important phase for first-time use. Setup is a **teacher**, not a config script. The deliverable is not "the operator has a working mindframe deployment" — it's "the operator understands how to wire a new event source, define an agent that handles it, and see the loop close end-to-end."

Nothing ships pre-baked recipes — the operator authors theirs here. The dispatcher's documented recipe contract is the reference to learn from, not a pre-baked answer.

### Step 7.1 — Pick an event to wire (operator's actual use case)

Don't offer a generic menu. Ask the operator: *"What's an event in your stack that you'd want mindframe to act on automatically?"* Free text. Examples to suggest if they're stuck: a Sentry alert, an Azure Monitor alert, a Power BI refresh failure, a GitHub PR merged, a calendar reminder, a webhook from any internal system. The point is they describe *their* event, not pick from ours.

Capture verbatim.

### Step 7.2 — Ground the event in the recipe contract

For the picked event, show the dispatcher's recipe contract and walk the shape — *"a webhook from this source lands here, matches a route, spawns an agent with this brief"*. There are no pre-shipped example recipes; the operator authors the first one in Step 7.3 against the contract.

### Step 7.3 — Author the recipe together with the operator

Walk the operator through creating `~/.dispatcher/recipes/<recipe-name>/`:
- `recipe.yaml` — plugins, MCPs, model, channels
- `brief.json` — placeholders for context the dispatcher fills from the webhook payload
- `CLAUDE.md` — the agent's starter prompt and what artifact to produce

Explain each field as it's written. The operator builds it from scratch against the contract — and ends up understanding it well enough to author the next one alone.

### Step 7.4 — Define what the agent does

If a relevant skill is already installed, point the recipe at it. Otherwise walk the operator through writing a small SKILL.md inline — the first 80% of skill authoring is just "describe the task in plain language; the agent uses available tools." This is also where the operator learns the *option* to package this skill into a shareable plugin if it generalizes.

### Step 7.5 — Wire the route in channels.yaml

Use `/dispatcher:route` (or write directly to `~/.dispatcher/channels.yaml`) — `source: <event-source>`, `event_type: <type>`, `target: spawn:<recipe-name>`.

### Step 7.6 — Generate the dispatcher bearer + surface the webhook URL

Same file-handoff rule as PHASE 5, inverted direction: the agent *generates* a token the operator needs to paste into the source system's webhook config. The token still never enters the chat.

The agent runs:

```bash
mkdir -p ~/.mindframe/secrets && chmod 700 ~/.mindframe/secrets
openssl rand -hex 32 > ~/.mindframe/secrets/dispatcher-bearer.token
chmod 600 ~/.mindframe/secrets/dispatcher-bearer.token
```

Then registers that path with the dispatcher (whichever config field points at the bearer source) and prints to the operator:

> Dispatcher ingress URL: `<URL>` (local or public if `deploy` is wired).
> Bearer token: written to `~/.mindframe/secrets/dispatcher-bearer.token`.
> Copy it to your clipboard with one of:
> - Linux: `xclip -sel clip < ~/.mindframe/secrets/dispatcher-bearer.token`
> - macOS: `pbcopy < ~/.mindframe/secrets/dispatcher-bearer.token`
> Then paste into the source system's webhook auth header field.

This is the one step mindframe can't do for the operator — it has no credentials for their source systems' webhook config UIs. But the bearer value still never touches the agent transcript.

### Step 7.7 — Simulate the event

The aha moment. Setup constructs a synthetic event matching the recipe's expected shape and POSTs it at the dispatcher ingress. Same subprocess-substitution rule — the agent never reads the token:

```bash
curl -X POST http://127.0.0.1:8911/api/event \
  -H "Authorization: Bearer $(cat ~/.mindframe/secrets/dispatcher-bearer.token)" \
  -H "Content-Type: application/json" \
  -d '<synthetic payload matching the recipe>'
```

Then the agent watches and narrates:
- dispatcher audit row appears (`event-received`)
- route matches, taskpilot spawns the agent (`static-spawn`)
- agent runs the skill, produces its artifact, notifies
- dashboard pane materializes (PHASE 9)
- operator sees the closed loop

This is the canonical "first run" experience. If it works, the operator gets it — they can repeat it for any future event source.

### Asks

- Free-text event description (7.1)
- Recipe contents walk-through, with operator confirming each section (7.3)
- SKILL.md contents if authoring inline (7.4)
- Source-system webhook config (7.6, operator-side action)

### Produces

- One real `~/.dispatcher/recipes/<recipe-name>/` triple
- One route in `~/.dispatcher/channels.yaml`
- One synthetic event fired, agent ran, artifact produced — the loop closed once
- Operator who understands the pattern well enough to wire the next event themselves

---

## PHASE 8 — Surface what else operators might author next

PHASE 7 already produced the operator's first agent. PHASE 8 is light: show what else they could build, derived from this deployment's own context — the systems discovered in PHASE 4 and the entities seeded in PHASE 6. **Nothing ships pre-baked.** The operator authors every utility themselves.

Concrete:

- From the discovered systems + vault entities, the agent proposes a few candidate deliverables it can infer for this org (e.g. "you have GitHub + Sentry — a PR-merged → changelog agent, or a Sentry-alert → triage agent").
- Surface them as a list with `name`, `trigger`, `why`. Frame as "*here's what your stack makes easy — anything jump out as your next one?*"
- If the operator picks one to author now, repeat PHASE 7 against it. Otherwise this phase produces nothing on disk — it's a forward pointer.

This phase is short on purpose. The "where do deliverable skills come from" answer is settled: **from the operator, via guided authoring.** Discovery suggests what's worth doing; the operator does it.

Asks: optional — "want to author another now?" If yes, jump to PHASE 7 with the picked example. If no, proceed.

Produces: nothing on disk by default. If operator authors a second utility now, another recipe + skill + route lands.

---

## PHASE 9 — Launch dashboard: static frame + ephemeral panes on one canvas

The dashboard is the human-facing surface of the whole bundle. Two element lifespans co-exist on one canvas: a **static frame** (persistent topology + user-pinned elements) and **ephemeral panes** (per-task agent-authored surfaces with live action buttons). See `project_mindframe_dashboard_model.md` and `project_mindframe_dashboard_taskboard.md` for the architectural rationale and merge intent.

**No persistent dashboard agent.** The previous "dashboard agent" was removed 2026-05-21 — it was a structural mismatch for this model. The static frame is config the server reads at startup; ephemeral panes are HTML written by per-event taskpilot agents the dispatcher spawns. The dashboard server is now a thin static-shell + artifact-viewer.

### Step 9.1 — Write the static frame from setup context

The setup agent already has rich context after PHASE 4 (discovery) and PHASE 6 (vault bootstrap): discovered systems, services / repos / data sources, vault entities, deployment name. It writes a first-pass static frame to `mindframe/dashboard/state/static-frame.<deployment>.json` — tiles for the most important systems, recent-events feed, an empty "recent investigations" lane for archived ephemeral panes.

The dashboard server reads this file at boot. **No code surgery on the plugin itself.** No agent prompt to regenerate, no hardcoded vault path. Everything that varies per customer lives in the deployment's config file or vault.

### Step 9.2 — Operator alters the static frame as first-class first-run UX

Setup explicitly walks the operator through customizing the dashboard:
- The setup agent shows the seeded frame and proposes adjustments: *"I added tiles for your top three services. Want me to add an Azure spend tile too? A pinned link to your status page?"*
- Operator can be specific: *"Add a Power BI tile linking to our daily refresh dashboard."*
- Each change writes the same `static-frame.<deployment>.json`; the operator reloads to see the dashboard update.

**Mutability of the static frame is on the critical path, not a v2 feature.** A dashboard the operator can't reshape from day 1 will rot. Rollback / version history is "nice to have, not POC-required."

### Step 9.3 — Start the dashboard server locally

`cd mindframe/dashboard && python server.py` (via daemon-manager for reboot-persistence). No taskpilot or session-bridge dependency — the server just serves the SPA, artifacts, and shares. Probe `/api/health`; open the URL in the operator's browser via the browser-bridge.

### Step 9.4 — Ephemeral panes spec (what triggers them, what they do)

When PHASE 7's simulated event fires (or any real event later), the dispatcher spawns a taskpilot agent. That agent produces an ephemeral pane:

- Agent writes HTML to `mindframe/dashboard/artifacts/<sid>/latest.html` — relevant context, charts pulled live from MCPs, draft artifacts editable inline.
- The dashboard SPA notices the new artifact (poll or SSE-on-mtime — POC choice) and materializes the pane on the canvas.
- HTML includes **action buttons** that POST synthetic events back to the dispatcher (`source: dashboard-button`, payload describes the button's intent). Dispatcher routes them through the same `channels.yaml` machinery as external webhooks — closes the loop using existing infra.
- When the agent finishes (the artifact stops changing), the pane **auto-archives** into a "recent investigations" lane on the static frame. POC-grade lifecycle; long-term TBD.

The agent's primary notification becomes *"investigation ready — see [link]"* where the link is the ephemeral pane. The dashboard is the medium, not a side effect.

### Forward pointer — taskboard merge

The merge with the taskboard plugin lands after the POC stands up. taskboard owns the dashboard chassis (D3 topology, probes, layout); mindframe contributes the dispatcher-event-driven pane spawning that lands artifacts in the same place. Mindframe stops "owning a dashboard" — it just emits events any dashboard can render. The static frame written here becomes the seed input for taskboard's topology view.

### Asks

- Confirm seeded frame; add/remove tiles (9.2)
- Optional — port to bind on, whether to expose externally via `deploy` capability

### Produces

- Dashboard server running locally (static shell + artifact viewer; no daemons)
- Static frame `static-frame.<deployment>.json` composed from setup context, mutated by operator
- Server already prepared to materialize ephemeral panes when artifacts land
- Action-button → dispatcher event protocol live
- Operator's browser open to the URL

### POC simplifications worth naming

- Action button contract: POST to dispatcher. Might switch to mesh messages via session-bridge later if button work needs to address a specific running agent. Not v1.
- Pane lifecycle: auto-archive on completion. Long-term retention, deep-linking, share permissions all deferred.
- Static-frame storage: file-based. DB + version control deferred.
- Pane materialization mechanism (poll vs SSE-on-mtime): defer to whichever ships first when wiring up the new SPA.

---

## PHASE 9.5 — Automated knowledge-capture loop (deferred — being redesigned)

**Removed 2026-06-05, pending a rethink.** The bundle previously shipped two long-running vault agents here — `vault-keeper` (write side: scanned session transcripts and wrote schema-valid entries) and `vault-query` (read side: answered questions against the catalog) — plus a scheduler daemon. Both were deleted to redesign the capture loop from scratch. No vault agents install during setup right now; the vault is populated by setup's own bootstrap (PHASE 6) and by any deliverable skills the operator authors. This phase is a placeholder until the redesigned loop lands.

---

## PHASE 10 — End-to-end smoke test

Fire a synthetic event through the wire. The point: prove the whole path works on this deployment, not just on a fixture.

- Pick the simplest live route from PHASE 7 (calendar recipe is probably the test target — no real external system needed).
- POST a synthetic event at dispatcher-ingress.
- Observe the audit log: `event-received` → `static-spawn` → `static-spawn-result` → `spawned`.
- Confirm taskpilot launched the task in tmux.
- Confirm the agent produced its artifact and notified.

Asks: nothing unless a step fails — then interrogate.

Produces: green smoke test, operator sees the audit rows tick through, dashboard shows the new task.

---

## PHASE 11 — Summary + pointers

Final block. Printed inline; optionally written to `<vault>/INSTALL.md` for future reference.

- What's running (daemons, dashboard URL, webhook URL).
- What was installed (plugins, providers, MCPs).
- What the operator can do next:
  - "Trigger a deliverable manually: `/mindframe:<skill> <args>`."
  - "Open the dashboard."
  - "Re-run this URL to add more event sources or skills."
- Where to come back to:
  - **This URL is the canonical install doc.** Re-running it is idempotent.
  - To add a new event source / skill / data system later: re-paste this URL; the resumable phases skip what's done.
  - For troubleshooting: `/mindframe:doctor`.

Produces: operator has the URLs, the agent has captured the deployment state, install is done.

---

## Things this outline forces a decision on

These are choices implicit in the structure above. Worth naming so they don't sneak through during prose drafting.

1. **Nothing ships pre-baked.** Not recipes, not skills, not deliverables, not domain schema. The operator authors their first utility during PHASE 7's guided authoring. This is the single most important design commitment in the install flow: the "aha" of mindframe is *seeing the loop close on your own event for your own system*, and pre-shipped artifacts muffle that. Operators who get the loop the first time wire the second event without help; operators handed a pre-built integration don't.

   **Domain schema is synthesized, not shipped.** Beyond the 10 fixed core entities, every domain-specific type is minted as `custom` during PHASE 4 discovery + PHASE 6 assembly, against the meta-schema in `docs/kb-schema.md`. There are no bundled domain packs to activate. Adding a new vertical means the agent discovers and mints the right entities live — no new artifact to author or install.

   install.txt stays generic. Discovery shapes the domain schema. Operators author the workflows.

2. **Deliverable skills: shipped vs generated.** PHASE 8's open question. The "delete the triage skills, redesign from scratch" thread loops back here — whatever you decide for the rebuild is what gets installed during PHASE 8.

3. **Dashboard launch is now part of install.** Today the dashboard is "you cd into it and run server.py if you want." The flow above requires install to leave it running. Small implementation lift; the dashboard agent was killed 2026-05-21 so there's no per-customer prompt regeneration to do — PHASE 9 just writes `static-frame.<deployment>.json` and starts the (now-thin) server.

4. **Idempotency.** Re-pasting install.txt should be safe. Each phase needs to detect "already done" and skip cleanly. Specified in the preamble as a rule, but needs concrete checkpoint conventions per phase.

5. **Resumability — checkpoint storage = settings.json.** Phase completion records live at `~/.claude/settings.json` → `pluginConfigs.mindframe.install_state` (sibling to `options`). Derive done-ness from the world wherever possible (vault present, daemon green, route exists); `install_state` only carries conversational checkpoints that aren't observable elsewhere (scope confirmed, events selected). Implication: if the operator wipes settings.json, install.txt re-runs from scratch — the world's state still drives most decisions, so this is mostly safe, but a confirmation prompt before re-doing already-done work is worth adding.

6. **Webhook config is operator-side, not agent-side.** Mindframe can't reach into Sentry/GitHub and configure their webhooks for them. PHASE 7 generates a dispatcher bearer to a file and prints a copy-to-clipboard command; the operator pastes into each source system. This is a deliberate boundary worth naming in the preamble — the agent does not have credentials for the customer's source systems, only API access — and the token never enters the agent transcript (file-handoff per PHASE 0).

7. **The relationship to `setup` SKILL.md.** Once install.txt covers everything, setup becomes either (a) a thin "fetch install.txt and follow it" stub, or (b) deleted entirely with `/mindframe:setup` re-routed to "go paste this URL." (b) is cleaner.

8. **Versioning.** install.txt at the public URL can update under deployed customers. Either version the URL (`install.txt?v=0.4.0`) or write the doc against capability contracts stable across versions (no file-path references that might churn).
