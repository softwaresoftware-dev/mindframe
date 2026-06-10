# mindframe

Mindframe gives an organization a knowledge base of how it works and AI agents
that act on it. It installs a six-layer agentic stack as one Claude Code plugin
bundle: a local web dashboard (the Surface) hosting persistent agents that each
own one live HTML page, an agent runtime, an event router, a plain-files
knowledge vault, a session mesh, and browser-based perception. Everything runs
locally, on the operator's Claude Code subscription, with no API keys and no
stored third-party credentials.

## Commands

| Command | What it does |
|---|---|
| `/mindframe:setup` | Onboard a deployment — a terminal bootstrap births your first mindframe, which runs the rest of setup inside the web surface. |
| `/mindframe:open` | Open the mindframe home (the hub graph) in your browser, starting the dashboard daemon if needed. |
| `/mindframe:connect <service>` | Research the best way into a tool (MCP / CLI / API / SQL / browser), author the connector, and verify it. |
| `/mindframe:doctor` | Diagnose and heal the bundle — probe every daemon and config, fix what's safe, report the rest with evidence. |

## Install

```
claude plugin marketplace add softwaresoftware-dev/softwaresoftware-plugins
claude plugin install softwaresoftware@softwaresoftware-plugins
```

Then, inside Claude Code:

```
/softwaresoftware:install mindframe
```

The resolver installs the bundle's providers (agent-spawning, session-mesh,
event-routing, browser-automation, daemon) in dependency order. Run
`/mindframe:setup` to onboard.

## Tests

```bash
python3 -m pytest dashboard/tests/ tests/e2e_wire/ tests/e2e_fresh/
```

Three tiers: `dashboard/tests/` (unit — vault graph), `tests/e2e_wire/`
(Tier 1 — hermetic surface-API wire tests against a stub taskpilot daemon),
`tests/e2e_fresh/` (Tier 3 — fresh-install invariants + dashboard boot). CI
runs all three on Linux/macOS/Windows × Python 3.11/3.12.

## Operations

- **Uninstall:** `/softwaresoftware:uninstall mindframe` removes the bundle
  and any dependencies no other plugin needs.
- **Vault:** your knowledge base lives at `~/.mindframe/vault` as plain
  Markdown files. Back it up by copying the directory or putting it under git.
- **Security:** the dashboard binds `127.0.0.1` only and is unauthenticated by
  design — never expose it beyond localhost. See
  [`docs/interfaces.md`](docs/interfaces.md#9-security-posture).

## Docs

[`docs/product.md`](docs/product.md) — what it is and who it's for.
[`docs/architecture.md`](docs/architecture.md) — the six layers in depth.
[`docs/interfaces.md`](docs/interfaces.md) — the contracts between layers.
[`docs/kb-schema.md`](docs/kb-schema.md) — the knowledge-base schema *(under redesign)*.
[`docs/onboarding-ux.md`](docs/onboarding-ux.md) — the setup UX model.
