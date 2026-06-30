---
name: sync
description: Pull fresh data from connected sources into the mindframe vault. Use when asked to "sync the vault", "refresh the knowledge base", "pull from GitHub", "update from Confluence", "refresh from sources", or triggered automatically by a dispatcher event. Run after connecting a new source, or on a schedule to keep vault entities current.
---

# Vault Sync

Pull fresh data from connected sources into a **workspace's** vault, conforming
to that workspace's `schema.yaml`. Sync is **per-workspace**: each workspace is a
partition under `~/.mindframe/workspaces/<id>/` with its own vault, its own
connector skills, and its own connections. Each connector skill may declare a
`sync:` block — this skill reads those blocks and drives the update.

## Input

- A **workspace id** — from the dispatcher brief (`brief.workspace`) when an event
  triggers the sync, or from the operator (`/mindframe:sync <workspace> [source]`).
  If omitted, default to `personal` (or ask, if several workspaces exist).
- Optionally a **source** name to sync just one connector; otherwise sync every
  connector in that workspace with a `sync:` block.

Resolve the partition once and use it throughout:

```bash
WS_ID="${WS_ID:-personal}"
WS="$HOME/.mindframe/workspaces/$WS_ID"
VAULT="$WS/.mindframe/vault"
SKILLS="$WS/.claude/skills"
[ -d "$WS" ] || { echo "workspace '$WS_ID' not found under ~/.mindframe/workspaces/"; exit 1; }
```

## Step 1 — Find syncable connectors

Scan **this workspace's** connector skills for a `sync:` block:

```bash
grep -rl "^sync:" "$SKILLS"/*/SKILL.md 2>/dev/null
```

If a specific source was requested, filter to that name. If none found, tell the operator that this workspace has no connections with sync configured and suggest `/mindframe:connect` to add one.

## Step 2 — For each connector

Process matching connectors (in parallel if multiple):

**a. Verify connection**
Run the connector's `check` command. If it exits non-zero, skip this connector and report it needs re-auth — don't abort the whole sync.

**b. Pull fresh data**
Run `sync.pull`. Capture stdout. If it fails, report the error and skip this connector.

**c. Map to vault entities**
Read `$VAULT/schema.yaml` to know valid entity types, required fields, FK targets, and directories. Then:

- The `sync.entities` list in the connector declares which types this source populates.
- For each object in the pulled data, identify the matching entity type and derive a `name` or `slug` (following the meta-schema identity rules: Things/Knowledge/Process use plain kebab names; Events prefix with `YYYY-MM-DD-`).
- Check whether a vault note already exists at the expected path. A note exists if the file is present.
- **New entity**: compose a conforming note — frontmatter from the source data, a one-line body stub, and `last_synced: YYYY-MM-DD`. Write it.
- **Existing entity**: re-read the note. Update only frontmatter fields that come from this source — never overwrite the body prose or any field the connector's `sync.entities` doesn't own. Bump `last_synced`. Write it back.

**d. What to write vs. preserve**

| Write / update | Never overwrite |
|---------------|-----------------|
| Frontmatter fields directly from the source | Body prose written by humans or agents |
| `last_synced: YYYY-MM-DD` | Frontmatter fields not provided by this source |
| New entities that don't exist | Existing entities absent from the pull (absence ≠ deletion) |

**e. FK integrity**
Before writing, verify every FK value resolves to an existing vault note or is being created in this same pass. If an FK target doesn't exist and isn't being created, write the value as a string but add a `# unresolved FK` comment — don't skip the note.

**f. Update CATALOG.md**
Read `$VAULT/CATALOG.md`. Add rows for new entities; update rows where frontmatter changed. Never remove a row unless its note file was deleted. Re-sort each section by name/slug.

**g. Commit**
If `$VAULT` is a git repo, commit: `sync: pull from <source> (<N> entities, <M> new)`.

## Step 3 — Schedule

Dispatcher wiring and schedule event-sources are set up automatically when a connection is made via `/mindframe:connect`. The operator should not need to wire this manually.

A workspace's event-sources, routes, and recipes live in its own partition
(`~/.mindframe/workspaces/<id>/.mindframe/dispatcher/`); the one shared dispatcher
reads them and derives the workspace from the source, so the spawned sync runs in
the right workspace's HOME.

**How automatic sync triggers work:**
- **Event-driven sources (GitHub):** the dispatcher polls via an event-source YAML in the workspace's partition; push/repository events route to `spawn:vault-sync` with `brief: { workspace: <id>, source: github }`.
- **Schedule-based sources (Confluence, REST APIs):** a `schedule` event-source fires a synthetic event (`source: schedule, event_type: <source>`) when its cron is due; the poller routes it to `spawn:vault-sync` with the workspace in the brief.

Both paths spawn the `vault-sync` recipe, which runs this skill with `WS_ID` set
from `brief.workspace`. The schedule event-source fires at the cadence from the
connector's `sync.schedule` field.

If the operator asks about a wiring issue or sync is not triggering, run `/mindframe:doctor` — it checks the recipe, channels.yaml routes, and event-source YAMLs (including schedule sources) and reports what's broken.

## Step 4 — Report

After all syncs complete, report:

- Which connectors were synced
- How many entities were created vs. updated per connector
- Any connectors skipped (no sync block, check failed, pull error)
- Whether the vault was committed

---

## The `sync:` block contract

A connector skill's `sync:` block tells this skill what to pull, what it produces, and when to trigger:

```yaml
sync:
  entities: [repository, service]   # entity types this source populates (must match schema.yaml)
  pull: "gh repo list --json name,description,visibility,pushedAt,languages,topics --limit 200"
  schedule: daily                   # hourly | daily | weekly | manual
  triggers: [push, pull_request]    # dispatcher event types that should re-trigger this sync
```

- **`entities`** — which vault entity types this pull produces. Must be declared in `schema.yaml` or the sync skips them.
- **`pull`** — a shell command whose stdout contains the raw source data (JSON, YAML, CSV, or prose — the agent interprets it). Use `bash -lc "..."` if the command needs env vars or shell expansions.
- **`schedule`** — the desired refresh cadence. The sync skill wires this up if a scheduler is available.
- **`triggers`** — dispatcher event types that should fire `/mindframe:sync <source>`. Match against the `type` field in dispatcher events.

### GitHub example

```yaml
---
name: github
description: GitHub — repos, issues, PRs. Use when a task needs GitHub data or actions.
connection:
  label: GitHub
  kind: cli
  access: gh
  auth: gh-cli
  check: ["gh", "auth", "status"]
  account: ["gh", "api", "user", "-q", ".login"]
  docs: gh --help
sync:
  entities: [repository, person]
  pull: "gh repo list --json name,description,visibility,pushedAt,topics,languages --limit 200 && echo '---' && gh api orgs/$(gh api user -q .login)/members 2>/dev/null || true"
  schedule: daily
  triggers: [push, create, repository]
---
```

### Confluence example

```yaml
---
name: confluence
description: Confluence — decision records, runbooks, conventions, project specs.
connection:
  label: Confluence
  kind: http-api
  access: https://yourorg.atlassian.net/wiki
  auth: env:CONFLUENCE_TOKEN
  check: ["bash","-lc","curl -fsS -H 'Authorization: Bearer $CONFLUENCE_TOKEN' 'https://yourorg.atlassian.net/wiki/rest/api/space' >/dev/null"]
  docs: https://developer.atlassian.com/cloud/confluence/rest/v1/
sync:
  entities: [decision, convention, project]
  pull: "bash -lc \"curl -s -H 'Authorization: Bearer $CONFLUENCE_TOKEN' 'https://yourorg.atlassian.net/wiki/rest/api/content?type=page&limit=50&expand=metadata.labels,version,space'\""
  schedule: daily
  triggers: [page_created, page_updated]
---
```

### Meeting transcripts

Meeting transcripts are not synced via this skill — they're a one-time event, not a polling source. Use `/meeting-transcribe:distill` after each transcript is finalized; it writes directly to the vault following the same schema.

---

## Authoring guidance for new connectors

When authoring a sync block for a new connector:

1. **Keep `pull` cheap and read-only.** Prefer list/metadata endpoints over full-content fetches. The agent interprets the output — it doesn't need every field, just enough to produce correct frontmatter.
2. **Scope `entities` tightly.** Only list types this source is authoritative for. Don't list `person` if your source has a user field that might conflict with the HR system that owns the canonical person list.
3. **Test the pull command manually** before adding it to the connector, verifying it exits 0 and returns parseable output.
4. **One authoritative source per field.** If both GitHub and Jira know about a project, decide which owns the vault entry. The other can link but shouldn't overwrite.
