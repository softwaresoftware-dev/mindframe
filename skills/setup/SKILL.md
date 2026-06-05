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

2. Follow every phase in order. That document is the operator script for end-to-end setup: rules, marketplace bootstrap, dependency install, deployment config, environment discovery, identity inheritance, schema assembly, KB bootstrap, guided event-source authoring, dashboard launch, smoke test, summary.

3. If `install.txt` is unreachable (network down, the static site is being redeployed, etc.), fall back to the design doc bundled inside this plugin at `${CLAUDE_PLUGIN_ROOT}/docs/install-outline.md`. Same structure, slightly more prose, fewer literal commands. install.txt is the source of truth when reachable.

## Why this is a stub

Mindframe's install flow exists in one place — `install.txt` at the URL above — so that:

- An operator on a fresh machine pastes the URL into Claude Code and gets the same flow
- An operator already inside a Claude session can run `/mindframe:setup` and get the same flow
- The flow updates in one place; no drift between two copies

The previous version of this skill duplicated install.txt's content and progressively diverged from it. That divergence is the bug; this stub is the fix.

## What's in install.txt (phase summary, for the agent's context)

The install flow is now **UI-based**: a small terminal bootstrap that births the
operator's first mindframe, then hands setup over to that mindframe. The terminal
agent does NOT run a long wizard — onboarding happens inside a web surface the
mindframe drives.

- **PHASE 0** — rules: identity inheritance, file-handoff for generated secrets, idempotency, user-scope-by-default, no Anthropic API key
- **PHASE 0.W** — Windows preflight (WSL2 required)
- **PHASE 1** — bootstrap the softwaresoftware marketplace + resolver
- **PHASE 2** — install mindframe + dependencies
- **PHASE 3** — BIRTH THE FIRST MINDFRAME: minimal config + vault, fill the setup brief (`${CLAUDE_PLUGIN_ROOT}/setup/brief.md`), spawn the `mindframe-setup` agent, run the surface server (`${CLAUDE_PLUGIN_ROOT}/surface/server.py`) as a managed daemon, open the browser
- **PHASE 4** — HAND OFF: the terminal agent steps out; setup continues in the surface

The setup mindframe (an agent owning one HTML surface it rewrites + a message
box) then runs the real onboarding arc: this-is-you → interview/schema →
discover connections → connect + synthesize → first signal. See
`${CLAUDE_PLUGIN_ROOT}/docs/onboarding-ux.md` and `setup/brief.md`.

When operating from this skill, you ARE the install agent install.txt addresses in second person. Read it, then act.
