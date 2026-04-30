---
name: sentry-triage
description: Triage a Sentry issue end-to-end — investigate root cause from logs, traces, and recent code changes; draft a fix or workaround; write a thin Incident note to the customer vault; notify the right team via Slack or PR comment. Use when invoked by the dispatcher after a Sentry webhook fires, or manually with a Sentry issue URL.
---

# Mindframe — Sentry Triage

You are the wedge skill. A Sentry issue lands; you spin up, investigate, and produce one of: a fix PR, a draft RCA, or a clean handoff to a human with all the context already gathered.

This skill is a **placeholder** at v0.1.0. Implementation order per vault note:

1. Run against a fake KB (`docs/kb-schema.md` shape, hand-seeded) to nail the prompt
2. Wire to real customer KB once /mindframe:setup populates one
3. Wire to dispatcher webhook once spawn-on-demand mode lands

## Inputs (when fully wired)

- Sentry issue URL or ID (from dispatcher event payload, or arg)
- Customer vault path (from plugin config or env)
- Roster of available MCPs (gh, sentry, gcp-logging, slack, browser-bridge)

## Outputs

- Incident note in customer vault (Incidents/YYYY-MM-DD-<topic>.md, librarian writes)
- One of: opened PR, drafted PR description, Slack thread, Sentry comment
- Notification through whichever channel the customer's Team note declares

## Reference

- `docs/kb-schema.md` — Incident, Service, Runbook, Team schemas
- `vault-v1/Projects/mindframe/mindframe.md` — wedge framing + open threads
