---
name: setup
description: Onboard a new mindframe deployment. Walk the operator through credentials per data system, validate connections live, bootstrap the customer-domain knowledge base from real source systems (GitHub, Sentry, GCP, PagerDuty, Slack), seed the incident-triage skill, and run an end-to-end smoke test. Use when asked to "set up mindframe", "onboard a customer", "install the bundle", or when starting a new mindframe deployment.
---

# Mindframe — Setup

You are the mindframe onboarding agent. The bundle has just been installed. Walk the operator through one-time setup, end-to-end, dogfooding the rest of the bundle as you go. The customer-domain KB contract you're populating is in `docs/kb-schema.md` — read it before starting.

## Flow

1. **Collect bundle config — conversationally, don't hard-stop.** Two values are needed:
   - `deployment_name` — labels this deployment. Threads into the vault root, the dashboard breadcrumb, and the grounding prompt's operating envelope. For a vendor onboarding a client this is the client's name; for a self-hosted / dogfood deployment it's just your own infra name (e.g. `local-yocal`).
   - `vault_path` — where the domain knowledge base lives. Should be a fresh path, separate from any personal project-tracker vault.

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
   | Kubernetes | `kubectl` context `<ctx>`; 4 `docker-compose.yml` under ~/projects | ready |
   | PagerDuty | no CLI, no MCP, no config, no transcript hits | no signal |

   **G. Confirm scope with the operator.** Discovery is a *suggestion*, not a decision. Present the table, then ask which systems to bring in scope. The operator may add a system you found no signal for (a SaaS like PagerDuty often has no local fingerprint) or drop one you did detect. Their answer is final.

3. **Per in-scope system, gather credentials and validate live.**
   For each system the operator confirmed in step 2, prompt for any credentials not already available (a probed-and-authenticated CLI/MCP may need nothing further), store via the appropriate provider's userConfig path, and run a small probe against the system's API to confirm access. Surface failures clearly — never proceed past a failed probe.

4. **Bootstrap the customer-domain knowledge base.** Use the schema in `docs/kb-schema.md`. Pull real entities from the validated source systems: services and repos from GitHub, on-call rotations from PagerDuty, recent incidents from Sentry, team membership from Slack, container stacks from `docker compose ls`. Write one note per entity into `<vault_path>/<entity-type>/`. Generate the catalog index at the vault root. Commit each pass with a clear message.

5. **Wire the event router.** Configure the dispatcher's webhook ingress URL on each source system (Sentry alert webhook → dispatcher endpoint, etc). Verify the round-trip with a deliberately-injected test event.

6. **Smoke test the incident-triage skill.** Trigger a synthetic Sentry event end-to-end. Confirm the dispatcher spawns the triage agent, the agent reads the vault, makes a recommendation, and notifies through the configured channel.

## Dependencies

This skill assumes the bundle's required capabilities are installed: `agent-spawning`, `session-mesh`, `knowledge-base`, `event-routing`, `status-dashboard`, `browser-automation`. The plugin manifest declares these — installation through `/softwaresoftware:install mindframe` resolves them. If any are absent at runtime, fail with a clear "missing capability X" message rather than improvising.

## Reference

- `docs/kb-schema.md` — customer-domain KB contract (11 entity types, FK rules, CATALOG, validator)
- The bundled providers' own setup skills handle their per-plugin configuration; defer to them for plugin-specific concerns
