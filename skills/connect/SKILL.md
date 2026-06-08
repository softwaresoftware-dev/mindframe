---
name: connect
description: Connect a tool or service so mindframe and its agents can reach it. Researches the best way in (an MCP, an authed CLI, a REST API, SQL, or the browser), drafts a connector, helps the operator place the credential, writes it, and verifies it works. Use when the operator says "connect <service>", "add a connection", "hook up <tool>", "I use <X>, can you wire it up", or from a launchpad "connect X" suggestion.
---

# Mindframe — Connect a tool

Take a service the operator names and make it a real, usable **connection** — something their agents can act through and that shows on the dashboard's Connections node. You do the technical work; the operator only provides credentials and approves.

A connection is one of two things, and your output is one of them, verified:
- an **MCP** Claude is connected to, or
- a **connector skill**: a `SKILL.md` carrying a `connection:` fingerprint.

## Input

The target service — from the argument (`/mindframe:connect hubspot`) or the operator's message. If it's unclear *what* they want or *what they use it for*, ask one question before researching; the use shapes which door is best.

## Step 1 — Don't duplicate

Before anything, check whether it's already reachable:
- `claude mcp list` — is there already an MCP for it?
- Is there already a connector skill at `~/.claude/skills/<service>/SKILL.md`?
- Is an authed CLI already on PATH (`command -v <tool>`)?

If a working path already exists, say so and stop — offer to re-verify or re-auth instead of making a duplicate.

## Step 2 — Research the door (don't guess)

Investigate how to actually reach this service, best option first. Use your own knowledge, the web (search + fetch the *official* API/auth docs), an available browser-automation tool, and probing the machine:

1. **MCP** — is there an official or community MCP server for it? (Best: structured tools.)
2. **Authed CLI** — does the operator already have a CLI for it (`stripe`, `gh`, `doctl`, `tailscale`, …)?
3. **REST API** — find the base URL, the auth scheme (API key / OAuth / bot token), and a cheap, read-only endpoint to use as the health `check`.
4. **SQL** — a connection string or read replica.
5. **Browser** — if there's no API at all, the door is driving the web UI.

Pick the best door you can actually stand up. Note the door, the auth method, the `check`, and the `docs` pointer (the CLI's `--help` command, or the service's API docs URL) before you propose anything. Never invent an endpoint or auth scheme — if you can't verify it from real docs or a real probe, say so.

## Step 3 — Branch on the door

### If the door is an MCP
Connecting means installing/registering that MCP, not writing a connector skill (the dashboard already lists MCPs). Use an available install mechanism to add it, guide any auth it needs, then confirm it shows in `claude mcp list`. Skip to Step 6.

### Otherwise (CLI / API / SQL / browser) — draft a connector skill
Compose a connector **named after the service**. Show the operator the draft and the exact credential step BEFORE writing anything.

The connector is `~/.claude/skills/<service>/SKILL.md`:

```
---
name: <service>                  # the slug, lowercase, == the service name
description: <service> — <what it is>. Use when a task needs <service> data or actions.
connection:
  label: <Display Name>
  kind: cli | http-api | sql | browser
  access: <binary | base_url | dsn-ref | url>
  auth: <pointer — gh-cli | env:NAME | file:PATH | oauth — NEVER the secret>
  check: [...]                   # argv that exits 0 only when usable; non-zero = needs-auth
  account: [...]                 # optional: prints the identity label
  docs: <help-cmd or URL>        # where a future agent learns to use it (CLI: `<tool> --help`; API: docs URL)
---
<body: the how-to an agent follows to use this connection. Keep irreversible
or outward-facing actions behind operator confirmation.>
```

Rules:
- **`auth` is a POINTER, never the secret.** The credential lives in the provider's own store, an env var, or a file — never inline in the skill.
- **`check` must exit non-zero when the connection is not usable** (logged out, no key) and be cheap and read-only.
- **`docs` points a future agent at the reference** — almost always `<tool> --help` for a CLI door, or the API docs URL. Prefer the live `--help` (version-accurate) over a stale URL.
- A worked example for **every door kind** is in **Examples by door kind** at the end of this skill — adapt the one matching your door.

## Step 4 — Get the credential in place

Tell the operator exactly what credential is needed and how to provide it WITHOUT handing it to you:
- a CLI login (`<tool> login`, `gh auth login`), or
- an env var they set, or
- a file they create at a path.

Where they have to fetch a key, name the exact page or menu. Wait for them to confirm it's in place. Never accept a pasted secret into the skill or the conversation.

## Step 5 — Write + verify

Once the operator approves the draft and the credential is in place:
1. Create the directory and write the SKILL.md with the Write tool.
2. Run the `check` command yourself. Exit 0 → connected. Non-zero → report what failed (usually the credential isn't where the `auth` pointer says), help fix it, then re-check.
3. `~/.claude/skills/` is watched, so the connector loads immediately — no restart.

Never report a connection working you didn't actually run the `check` against.

## Step 6 — Confirm

Tell the operator plainly: `<service>` is connected, it shows on the dashboard's Connections node, and any agent can now use it (the connector's body is the how-to). End by asking whether they want to connect anything else, or to put this connection to work now.

## Examples by door kind

One reference per door. Copy the shape, fill in only values you actually verified.

### CLI — the operator already has an authed CLI
```yaml
---
name: github
description: GitHub — repos, issues, PRs, releases. Use when a task needs GitHub data or actions.
connection:
  label: GitHub
  kind: cli
  access: gh
  auth: gh-cli                          # uses gh's own login; no token here
  check: ["gh", "auth", "status"]
  account: ["gh", "api", "user", "-q", ".login"]
  docs: gh --help
---
Reach GitHub via `gh` (runs as the operator). e.g. `gh pr list`, `gh api <endpoint>`.
```

### MCP — there's an MCP server for it
**Don't write a connector skill** — an MCP *is* the connection. Install/register it; the dashboard lists it automatically.
```bash
claude mcp add <name> -- <command...>    # or use the bundle's install flow
claude mcp list                          # confirm <name> appears
```

### HTTP API — no CLI/MCP, but a REST API with a token
```yaml
---
name: hubspot
description: HubSpot — CRM contacts, deals, companies. Use when a task needs HubSpot data.
connection:
  label: HubSpot
  kind: http-api
  access: https://api.hubapi.com
  auth: env:HUBSPOT_TOKEN               # pointer; the token lives in the env, never here
  check: ["bash","-lc","curl -fsS -H \"Authorization: Bearer $HUBSPOT_TOKEN\" https://api.hubapi.com/account-info/v3/details >/dev/null"]
  docs: https://developers.hubspot.com/docs/api/overview
---
curl the v3 REST API with `Authorization: Bearer $HUBSPOT_TOKEN`. Read-only by default.
```

### SQL — a database (often a read replica)
```yaml
---
name: analytics-db
description: Analytics Postgres (read replica) — metrics, events, users. Use for analytical questions.
connection:
  label: Analytics DB
  kind: sql
  access: file:~/.mindframe/secrets/analytics.dsn   # the connection string lives in this file
  auth: file:~/.mindframe/secrets/analytics.dsn
  check: ["bash","-lc","psql \"$(cat ~/.mindframe/secrets/analytics.dsn)\" -c 'select 1' >/dev/null"]
  docs: psql --help
---
psql with the DSN in the file above. Read-only replica — SELECT only unless told otherwise.
```

### Browser — no API at all; drive the web UI
```yaml
---
name: vendor-portal
description: Vendor Portal — the only way in is the web UI (no API). Use to read/act in the portal.
connection:
  label: Vendor Portal
  kind: browser
  access: https://portal.vendor.com
  auth: browser-session                 # operator stays logged in; no token
  # no cheap check — verifying means driving the browser, so list by presence only
  docs: https://portal.vendor.com/help
---
Reach it with an available browser-automation tool; the operator keeps a logged-in session.
```

### File — a folder of dumps or a mounted share
```yaml
---
name: exports
description: Exports — CSV/JSON dumps in a local folder. Use when a task needs the exported data.
connection:
  label: Exports
  kind: file
  access: ~/Exports
  auth: ~                               # local files, no auth
  check: ["bash","-lc","test -d ~/Exports"]
  docs: ~
---
Read files under `~/Exports` (CSV/JSON); parse with the appropriate tool.
```
