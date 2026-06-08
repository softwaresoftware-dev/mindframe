# Connectors

A **connector** is a Claude Code skill that represents a way to reach an external
system. It is an ordinary `SKILL.md` with one extra thing: a `connection:`
fingerprint in its frontmatter. The presence of that block is what makes the
dashboard treat the skill as a connection and what lets any agent discover and
use it.

This directory holds the **seed connectors** the bundle ships. They are installed
into the operator's user-scope skills (`~/.claude/skills/<name>/`) at setup, so
they load in every session. Agents author new ones the same way at runtime.

## The fingerprint

```yaml
---
name: github                       # the slug; also the skill / slash-command name
description: GitHub — ...           # the trigger an agent sees in its skill list
connection:
  label: GitHub                    # display name on the dashboard (optional)
  kind: cli                        # cli | http-api | sql | browser | mcp | file
  access: gh                       # binary / base_url / dsn-ref / url
  auth: gh-cli                     # POINTER to creds: gh-cli | env:NAME | file:PATH | oauth
  check: ["gh", "auth", "status"]  # exit 0 = connected; non-zero = needs-auth; can't run = hidden
  account: ["gh", "api", "user", "-q", ".login"]   # optional: prints the identity label
  docs: gh --help                  # optional: where a future agent learns to use it
---
# the body is the how-to an agent follows to use the connection
Reach GitHub through the `gh` CLI ...
```

Rules:

- **Named after the service** (`github`, `stripe`, `hubspot`) — not `connect-github`.
  The `connection:` block, not the name, is what marks it a connector.
- **`check`** is an argv list (preferred, cross-platform) or a shell string. It must
  exit non-zero when the connection is *not* usable (not just not-installed). If the
  command can't run at all (tool missing), the dashboard hides the connector.
- **`auth` is a pointer, never a secret.** Point at the provider's own credential
  store (`gh-cli`), an env var (`env:HUBSPOT_TOKEN`), or a file (`file:~/.config/...`).
- **`docs` points a future agent at the reference.** For a CLI it's almost always
  the help command (`gh --help`, `tailscale --help`); for an API it's the docs URL.
  An agent runs it (CLI) or fetches it (URL) when the body's common moves aren't
  enough — the live `--help` is version-accurate, so prefer it over a stale URL.
- **The body is the recipe** — the instructions an agent follows once it picks this
  connection. Keep irreversible/outward actions behind operator confirmation.

## How it's read

`dashboard/server/server.py` → `_discover_connections()` scans the skill
directories (`_skill_dirs()`), keeps every SKILL.md with a `connection:` block
(`_connector_skills()`), and merges the results with the MCPs Claude is connected
to. `/api/connections` returns the union; the home hub's **Connections** node
renders it.

**Live status is deferred.** Today the dashboard lists connections by presence
only — it does *not* run the `check`/`account` commands, so there is no
connected/needs-auth dot or identity label yet. The fields stay in the
fingerprint (forward-compatible); re-enabling status means probing them again,
in the background, off the request path.
