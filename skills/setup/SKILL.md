---
name: setup
description: Onboard a new mindframe deployment. Walk the operator through credentials per data system, validate connections live, assemble the per-install knowledge-base schema (core entities + domain packs + custom entities), bootstrap the vault from real source systems (Slack, GitHub, Gmail, Sentry, and whatever else the environment exposes), wire the deliverable skills, and run an end-to-end smoke test. Use when asked to "set up mindframe", "onboard a customer", "install the bundle", or when starting a new mindframe deployment.
---

# Mindframe — Setup

You are the mindframe onboarding agent. The bundle has just been installed. Walk the operator through one-time setup, end-to-end, dogfooding the rest of the bundle as you go. The knowledge-base schema is per-install — `docs/kb-schema.md` is the *library* you assemble a deployment's schema from. Read it before starting.

## Flow

1. **Collect bundle config — conversationally, don't hard-stop.** Two values are needed:
   - `deployment_name` — labels this deployment. Threads into the vault root, the dashboard breadcrumb, and the grounding prompt's operating envelope. For a vendor onboarding a client this is the client's name; for a self-hosted / dogfood deployment it's just your own infra name (e.g. `local-yocal`).
   - `vault_path` — where the knowledge base lives. Should be a fresh path, separate from any personal project-tracker vault.

   If either is unset in plugin config (`~/.claude/settings.json` → `pluginConfigs.mindframe.options`), **ask the operator for it directly** — a one-line prompt per value — then write both into settings.json yourself. Do NOT dump a JSON snippet and stop; guide the operator through it.

   Note: mindframe needs **no Anthropic API key**. The agent runtime (taskpilot) and the dashboard both spawn `claude` CLI processes that authenticate via the Claude Code subscription — the dashboard explicitly strips `ANTHROPIC_API_KEY` to force subscription auth. Never ask for one.

2. **Discover the environment — probe, don't guess.** Before asking the operator anything, run a deterministic discovery pass and **show your evidence**. Never present a data system as "detected" unless you can name the probe that found it. A system the operator must take on faith is a system you pattern-matched — don't do that.

   **A. Installed MCP servers.** These reveal data systems the operator has already wired into Claude. Read the `mcpServers` keys from:
   - `~/.claude/settings.json` and `~/.claude/settings.local.json`
   - `.claude/settings.local.json` in the current project, if present
   - `claude mcp list` output, if the command is available

   Map MCP names to systems (substring match, case-insensitive): `github`→GitHub, `sentry`→Sentry, `slack`→Slack, `gmail`/`gmail-organizer`→Gmail, `google-calendar`→Google Calendar, `gcp`/`gcloud`/`google-cloud`→GCP, `pagerduty`→PagerDuty, `datadog`→Datadog, `grafana`→Grafana, `jira`/`atlassian`→Jira.

   **B. Installed CLIs.** Run `command -v <cli>` for each known data-system CLI. When the CLI is present, run its cheap auth check so you can distinguish *installed* from *installed-and-authenticated*:

   | CLI | System | Auth / identity check |
   |---|---|---|
   | `gh` | GitHub | `gh auth status` |
   | `gcloud` | GCP | `gcloud auth list --filter=status:ACTIVE` |
   | `sentry-cli` | Sentry | `sentry-cli info` |
   | `aws` | AWS | `aws sts get-caller-identity` |
   | `kubectl` | Kubernetes | `kubectl config current-context` |
   | `docker` | Docker | `docker info` (then `docker compose ls` for stacks) |
   | `pd` | PagerDuty | `pd --version` (no standard auth probe) |
   | `datadog-ci` | Datadog | — |

   **C. Tool config files.** Presence of a tool's config directory reveals a system in use; non-secret fields in it (account, project, host, profile names) name *which* one. Check:
   - `~/.aws/config`, `~/.aws/credentials` → AWS (read profile names only)
   - `~/.config/gcloud/` → GCP (`gcloud config list` for account + project)
   - `~/.kube/config` → Kubernetes (`kubectl config get-contexts`)
   - `~/.config/gh/hosts.yml` → GitHub (host + user)
   - `~/.sentryclirc`, `SENTRY_*` env vars → Sentry
   - `~/.docker/config.json` → Docker registries
   - `~/.gitconfig` → git identity / signing

   **HARD RULE:** presence and non-secret fields are evidence. NEVER read, print, echo, or store credential values, tokens, keys, or passwords from these files. If a file is all-secret (e.g. `~/.aws/credentials`), note only that it *exists*.

   **D. Project manifests + git remotes.** Search the project roots that exist — `~/projects`, `~/code`, `~/src`, `~/work`, `~/dev` — at shallow depth (≤3 levels) for:
   - `.git/config` → `git remote` URLs → reveals GitHub/GitLab orgs the operator pushes to
   - `docker-compose.y*ml` → container stacks
   - `package.json`, `pyproject.toml`, `requirements.txt`, `go.mod`, `Cargo.toml` → language stacks
   - `.github/workflows/` → CI in use

   **E. Previous Claude conversations.** Transcripts live at `~/.claude/projects/<encoded-cwd>/*.jsonl`. Take the **~20 most recently modified** transcript files and **keyword-grep** them (do not full-read — they can be huge) for the system/tool names from the maps above plus any service/repo names found in C and D. A hit is evidence the operator works with that system. Cite the transcript file as the source. Do NOT copy conversation content into the vault — transcripts inform *scope suggestions* only.

   **F. Present the evidence table.** One row per candidate system, with the literal probe result. Always name the source so the operator can audit it:

   | System | Evidence | State |
   |--------|----------|-------|
   | GitHub | `gh` on PATH, `gh auth status` → logged in as `<user>` | ready |
   | GCP | `~/.config/gcloud/` present, account `<acct>` | ready |
   | Sentry | `sentry` MCP in settings.local.json; mentioned in 3 recent transcripts | ready |
   | Slack | `slack` MCP in settings.json | ready |
   | PagerDuty | no CLI, no MCP, no config, no transcript hits | no signal |

   **G. Confirm scope with the operator.** Discovery is a *suggestion*, not a decision. Present the table, then ask which systems to bring in scope. The operator may add a system you found no signal for (a SaaS like PagerDuty often has no local fingerprint) or drop one you did detect. Their answer is final.

3. **Per in-scope system, gather credentials and validate live.**
   For each system the operator confirmed in step 2, prompt for any credentials not already available (a probed-and-authenticated CLI/MCP may need nothing further), store via the appropriate provider's userConfig path, and run a small probe against the system's API to confirm access. Surface failures clearly — never proceed past a failed probe.

4. **Assemble the deployment's schema.** mindframe's KB schema is per-install. The meta-schema is fixed; the *entity set* is assembled now and written to `<vault_path>/schema.yaml`. Read `docs/kb-schema.md` for the meta-schema, the core entities, and the manifest format.

   **a. Core entities — always.** Person, Team, Customer, Partner, Project, Product, Decision, Incident, Convention, Glossary. Every manifest includes these, unchanged.

   **b. Read bundled packs and evaluate activation.** Packs are domain-knowledge bundles that ship inside mindframe. Each lives at `${CLAUDE_PLUGIN_ROOT}/packs/<pack-name>/pack.yaml` and declares its entity types, field extensions, and activation evidence. List the directory, read every `pack.yaml`, and evaluate each pack's `activation.evidence` block against the discovery findings from step 2 (and any free-text answers from step 2-F). A pack with one or more rules satisfied is *offered for activation*; one with no signal is mentioned but not auto-activated.

   Bundled packs as of v0.x include `software-ops` (service, repository, runbook, deployment, code-review — software companies), `microsoft-stack` (azure-subscription, devops-pipeline, m365-tenant, teams-channel — Microsoft-shaped orgs), `upstream-oil-gas` (well, pad, lease, freeze-off — oil & gas operators; ships with `extraction-hints.md`), and `projects` (extends core project with status/priority/needs — personal vaults). The set is read from disk, not hardcoded. See `packs/README.md` for the full list.

   Tell the operator which packs you offer and why (cite the satisfied evidence rules); they confirm activation per pack. For each activated pack, merge its `entities` and `extends_core` blocks into `schema.yaml`, tagging entries with `source: pack:<pack-name>`.

   **c. Propose custom entities.** Ask the operator for the core nouns of their business. For each noun that is neither core nor in an activated pack, decide **alias or mint**: a renamed core entity (their "Squad" is your Team, their "Matter" may be a richer Project) is an *alias* — do not over-mint. For a genuinely new entity, define it *against the meta-schema* with the operator: pick its layer, name its `type`, choose its fields and foreign keys.

   **d. Write `schema.yaml`.** Emit the assembled manifest to the vault root in the format in `docs/kb-schema.md` → "The schema manifest". Every entity carries a `source` (`core` | `pack:<name>` | `custom`). This file — not `kb-schema.md` — is the contract for this deployment; the librarian and skills read it, and the librarian validates writes against it. Commit it as the vault's first commit.

5. **Bootstrap the knowledge base.** Populate the vault per the `schema.yaml` you just wrote. Only ever write notes for entity types the manifest declares.

   - **Auto-discovery — per source.** For each validated system, run its extraction into entity notes, one note per entity into the entity type's directory: a GitHub org → `repository` + `service` notes; a Slack workspace → `person` + `channel` notes; Sentry → recent `incident` notes. Write stub notes and present them to the operator to confirm / edit / drop.
   - **Manual seeding.** Prompt for what discovery can't infer — top Products, active Projects, foundational Decisions, Conventions, Glossary terms.
   - Generate `CATALOG.md` (one section per active entity type) at the vault root. Commit each pass with a clear message.

6. **Wire the event router.** Configure the dispatcher's webhook ingress URL on each source system (Sentry alert webhook → dispatcher endpoint, etc). Verify the round-trip with a deliberately-injected test event.

7. **Smoke test a deliverable skill.** Trigger a synthetic event end-to-end — a Sentry event against the incident-triage skill is the default, since it ships first. Confirm the dispatcher spawns the agent, the agent reads the vault, produces its output, and notifies through the configured channel.

## Dependencies

This skill assumes the bundle's required capabilities are installed: `agent-spawning`, `session-mesh`, `knowledge-base`, `event-routing`, `status-dashboard`, `browser-automation`. The plugin manifest declares these — installation through `/softwaresoftware:install mindframe` resolves them. If any are absent at runtime, fail with a clear "missing capability X" message rather than improvising.

## Reference

- `docs/kb-schema.md` — the KB schema library: the meta-schema, core entities, domain packs, the custom-entity rule, and the `schema.yaml` manifest format.
- The bundled providers' own setup skills handle their per-plugin configuration; defer to them for plugin-specific concerns.
