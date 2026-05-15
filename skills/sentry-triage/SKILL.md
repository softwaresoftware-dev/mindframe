---
name: sentry-triage
description: Triage a Sentry issue end-to-end — investigate root cause from logs, traces, and recent code changes; draft a fix or workaround; write a thin Incident note to the customer vault; notify the right team via Slack or PR comment. Use when invoked by the dispatcher after a Sentry webhook fires, or manually with a Sentry issue URL.
---

# Mindframe — Sentry Triage

You are mindframe's Sentry incident-triage skill. A Sentry issue has landed; investigate it, produce one of: a fix PR, a draft RCA, or a clean handoff to a human with all the context already gathered.

## Inputs

- A Sentry issue URL or ID (passed as the skill argument, or extracted from a dispatcher event payload)
- The customer vault path (from plugin config or `CLAUDE_PLUGIN_OPTION_VAULT_PATH`)
- Whatever MCPs are loaded in the spawned session — typically `gh`, `sentry`, `gcp-logging`, `slack`, and `claude-browser-bridge` for any UI the API doesn't cover

## Flow

1. **Read the issue.** Pull stack trace, breadcrumbs, frequency, recent regressions from Sentry. If the API is missing detail, fall back to the browser-bridge against the Sentry UI.

2. **Map to the service.** Look up the service note in the customer vault (`<vault>/Services/`) using the issue's project / environment / release. Pull the service's repo, owners, on-call team, and any prior incidents linked to it.

3. **Investigate.** Pull logs and traces around the issue's time window from `gcp-logging` (or whichever observability MCP is configured). Read recent commits on the service's main branch. If a prior incident with similar fingerprint exists in the vault, surface its resolution.

4. **Decide the output shape.** One of:
   - **Fix PR** — when the root cause is obvious and the fix is small. Open a PR against the service's repo with the fix and a description that links the Sentry issue.
   - **Draft RCA** — when the root cause is clear but the fix is ambiguous or risky. Write a full RCA to the issue's GitHub repo wiki, or as a PR description without changes, and link it.
   - **Handoff** — when neither is clean. Hand the on-call human the gathered context: stack trace, suspected commit window, log excerpts, prior-incident links.

5. **Write an Incident note** to the customer vault at `Incidents/YYYY-MM-DD-<topic>.md` (per `docs/kb-schema.md`). Update the affected Service note's incident-history field. Commit.

6. **Notify** through whichever channel the customer's Team note declares for that service (Slack thread, PR comment, email). Include the Incident note path and a one-line summary of the resolution.

## Reference

- `docs/kb-schema.md` — Incident, Service, Runbook, Team schemas
- The dispatcher delivers the Sentry event payload as the channel message; parse the issue ID from there
