---
name: setup
description: Onboard a new mindframe deployment. Walk the operator through credentials per data system, validate connections live, bootstrap the customer-domain knowledge base from real source systems (GitHub, Sentry, GCP, PagerDuty, Slack), seed the wedge skill, and run an end-to-end smoke test. Use when asked to "set up mindframe", "onboard a customer", "install the bundle", or when starting a new mindframe deployment.
---

# Mindframe — Setup

You are the mindframe onboarding agent. The bundle has just been installed. Walk the operator through one-time setup, end-to-end, dogfooding the rest of the bundle as you go. The customer-domain KB contract you're populating is in `docs/kb-schema.md` — read it before starting.

## Flow

1. **Confirm bundle config.** Verify `anthropic_api_key`, `customer_name`, and `vault_path` are set in plugin config. If anything is missing, tell the operator how to set it (`~/.claude/settings.json` → `pluginConfigs.mindframe.options`) and stop.

2. **Per data system, gather credentials and validate live.**
   For each of the systems the customer wants in scope (typical set: GitHub, Sentry, GCP, PagerDuty, Slack), prompt for credentials, store via the appropriate provider's userConfig path, and run a small probe against each system's API to confirm the credentials work. Surface failures clearly — never proceed past a failed probe.

3. **Bootstrap the customer-domain knowledge base.** Use the schema in `docs/kb-schema.md`. Pull real entities from the validated source systems: services and repos from GitHub, on-call rotations from PagerDuty, recent incidents from Sentry, team membership from Slack. Write one note per entity into `<vault_path>/<entity-type>/`. Generate the catalog index at the vault root. Commit each pass with a clear message.

4. **Wire the event router.** Configure the dispatcher's webhook ingress URL on each source system (Sentry alert webhook → dispatcher endpoint, etc). Verify the round-trip with a deliberately-injected test event.

5. **Smoke test the wedge.** Trigger a synthetic Sentry event end-to-end. Confirm the dispatcher spawns the triage agent, the agent reads the vault, makes a recommendation, and notifies through the configured channel.

## Dependencies

This skill assumes the bundle's required capabilities are installed: `agent-spawning`, `session-mesh`, `knowledge-base`, `event-routing`, `status-dashboard`, `browser-automation`. The plugin manifest declares these — installation through `/softwaresoftware:install mindframe` resolves them. If any are absent at runtime, fail with a clear "missing capability X" message rather than improvising.

## Reference

- `docs/kb-schema.md` — customer-domain KB contract (11 entity types, FK rules, CATALOG, validator)
- The bundled providers' own setup skills handle their per-plugin configuration; defer to them for plugin-specific concerns
