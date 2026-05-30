---
name: setup
description: Onboard a new mindframe deployment. Fetches install.txt — the canonical end-to-end install + setup flow — and follows it. Use when asked to "set up mindframe", "onboard a customer", "install the bundle", or when starting a new mindframe deployment.
---

# Mindframe — Setup

This is a delegating stub. The full install + setup flow lives at one canonical URL so it stays in sync across direct paste-from-the-web installs and `/mindframe:setup` invocations from inside an existing Claude session.

## What to do

1. Fetch the canonical install document:

   ```bash
   curl -fsS https://mindframe.softwaresoftware.dev/install.txt
   ```

2. Follow every phase in order. That document is the operator script for end-to-end setup: rules, marketplace bootstrap, dependency install, deployment config, environment discovery, identity inheritance, schema assembly, KB bootstrap, guided event-source authoring, vault-keeper install, dashboard launch, smoke test, summary.

3. If `install.txt` is unreachable (network down, the static site is being redeployed, etc.), fall back to the design doc bundled inside this plugin at `${CLAUDE_PLUGIN_ROOT}/docs/install-outline.md`. Same structure, slightly more prose, fewer literal commands. install.txt is the source of truth when reachable.

## Why this is a stub

Mindframe's install flow exists in one place — `install.txt` at the URL above — so that:

- An operator on a fresh machine pastes the URL into Claude Code and gets the same flow
- An operator already inside a Claude session can run `/mindframe:setup` and get the same flow
- The flow updates in one place; no drift between two copies

The previous version of this skill duplicated install.txt's content and progressively diverged from it. That divergence is the bug; this stub is the fix.

## What's in install.txt (phase summary, for the agent's context)

- **PHASE 0** — rules: identity inheritance, file-handoff for generated secrets, idempotency, telemetry, user-scope-by-default, no Anthropic API key
- **PHASE 0.W** — Windows preflight (WSL2 required)
- **PHASE 1–2** — bootstrap the softwaresoftware marketplace + resolver, install mindframe + dependencies
- **PHASE 3** — deployment config (`deployment_name`, `vault_path`, telemetry consent)
- **PHASE 4** — environment discovery (probes A–F), pack activation
- **PHASE 5** — identity inheritance per in-scope system (never collect raw tokens)
- **PHASE 6** — assemble `<vault>/schema.yaml`, bootstrap KB from real sources
- **PHASE 7** — guided authoring: first event source → recipe → agent → simulated event (the aha moment)
- **PHASE 8** — surface what else operators might author next
- **PHASE 9** — launch dashboard as a managed daemon
- **PHASE 9.5** — spawn vault-keeper + vault-query, install the capture scheduler (added in v0.6.x)
- **PHASE 10** — end-to-end smoke test
- **PHASE 11** — summary + pointers

When operating from this skill, you ARE the install agent install.txt addresses in second person. Read it, then act.
