# vault-keeper

Automated knowledge capture from Claude Code working sessions into the mindframe deployment's vault. Runs as part of the mindframe bundle.

## Architecture

Two pieces working together:

- **The agent** — a long-running taskpilot service-kind Claude session (`agent/CLAUDE.md`). Receives channel messages with job pointers, reads schemas + transcripts fresh per job, writes vault entries that conform to the deployment's schema. Validates against the freshness contract (git pull, fresh schema, fresh catalog) on every write.

- **The trigger** — `keeper.py`, a thin Python script invoked on a schedule (or directly). Scans Claude Code's session transcripts (`~/.claude/projects/<encoded>/*.jsonl`), extracts the user/assistant text, drops a job file in the queue, and pings the agent via session-bridge.

The agent does the thinking; the trigger does the plumbing. The agent has zero auth requirements (uses Claude Code subscription auth); the trigger has zero LLM calls.

## How it captures knowledge

```
schedule fires → keeper.py runs
  ↓
keeper.py scans ~/.claude/projects/<encoded>/*.jsonl since last run
  ↓
extracts user+assistant text (skips tool_use, file snapshots, etc — typically 2% of bytes are signal)
  ↓
writes job file: { vault_path, transcript_text_path, project_label, since, until }
  ↓
POSTs channel message to vault-keeper agent: "vault-keeper job: <path>"
  ↓
agent receives message, runs freshness contract:
  - git pull on vault
  - read schema.yaml fresh
  - read CATALOG.md fresh
  ↓
agent classifies substantive items per schema's entity types
  ↓
agent writes vault entries with proper frontmatter + FK references
  ↓
agent updates CATALOG, commits, pushes (if remote configured)
  ↓
agent replies on channel with summary, deletes job file
```

## Setup (on a mindframe deployment)

Prerequisites already installed by the bundle: `taskpilot`, `session-bridge`, a vault with `schema.yaml`.

1. **Spawn the agent** (one-time, via taskpilot):
   ```
   /taskpilot:spawn name=vault-keeper kind=service model=sonnet \
     description="... read agent/CLAUDE.md from this plugin and follow it ..."
   ```
   See the live deployment for the exact spawn command — it points the agent at `${CLAUDE_PLUGIN_ROOT}/vault_keeper/agent/CLAUDE.md`.

2. **Configure the schedule.** systemd timer is the recommended path:

   ```ini
   # ~/.config/systemd/user/vault-keeper.service
   [Unit]
   Description=vault-keeper trigger — capture knowledge from Claude Code transcripts

   [Service]
   Type=oneshot
   ExecStart=/usr/bin/python3 %h/projects/softwaresoftware/projects/plugins/frameworks/mindframe/vault_keeper/keeper.py
   ```

   ```ini
   # ~/.config/systemd/user/vault-keeper.timer
   [Unit]
   Description=vault-keeper hourly during business hours

   [Timer]
   OnCalendar=Mon..Fri 09..18:00:00
   Persistent=true

   [Install]
   WantedBy=timers.target
   ```

   ```bash
   systemctl --user enable --now vault-keeper.timer
   ```

3. **vault_path resolution.** `keeper.py` reads `pluginConfigs.mindframe.options.vault_path` from `~/.claude/settings.json`. Set during `/mindframe:setup`. Override per invocation with `--vault-path`.

## Usage

```bash
# Standard scheduled run — scan transcripts since last successful run
keeper.py

# Backfill from a specific timestamp
keeper.py --since 2026-05-29T12:00:00

# Print what would happen without messaging the agent
keeper.py --dry-run

# Scope to one project
keeper.py --project -home-thatcher-projects-softwaresoftware-projects

# Direct mode (for simulations / ad-hoc) — bypass the jsonl scan, send
# a pre-extracted transcript file to the agent
keeper.py --transcript-file /path/to/extracted.txt --vault-path /path/to/vault
```

## State

- `~/.mindframe/vault-keeper/state.json` — last run timestamp, last counts
- `~/.mindframe/vault-keeper/queue/` — job files + transcript snapshots awaiting agent processing
- `~/.taskpilot/vault-keeper/state.json` — agent's per-job state (last job id, etc.)

## Freshness contract

Every write goes through:

```
git -C <vault> pull --quiet         # pull latest from remote, no-op if local-only
cat <vault>/schema.yaml              # re-read schema fresh
cat <vault>/CATALOG.md               # re-read catalog fresh
... classify, write, commit ...
git -C <vault> push --quiet          # push if remote, no-op otherwise
```

This is the load-bearing property when multiple agents or humans can edit the same vault. The agent's local memory of the vault state is invalidated as soon as it finishes a write — next write requires re-read.

## What ships in the bundle vs what's manual today

| Capability | Status |
|------------|--------|
| Agent code + CLAUDE.md (the "what to do") | Ships with the bundle |
| Trigger script (the "when to fire") | Ships with the bundle |
| Freshness contract honored on every write | Built in |
| Schema-driven writes per deployment's schema.yaml | Built in |
| Spawning the agent on bundle install | Manual today; future `/mindframe:setup` step |
| Installing the systemd timer | Manual today (sample units in this README); future `/mindframe:setup` step |

The capability is there; the install-time automation is the next layer. The bundle's `mindframe:setup` skill will eventually do both.

## Validated end-to-end via the simulation framework

`simulations/run.py` exercises the full pipeline (schema creation → synthetic transcript → vault-keeper writes → vault-query answers) against a persona. See `simulations/README.md` for the test harness; see `simulations/personas/vc-partner.md` for the first persona definition.

## Sibling: vault-query

`vault_query/` is the read-side companion. Same agent shape, same freshness contract, answers questions against the vault with wikilink-cited responses. See `vault_query/agent/CLAUDE.md`.
