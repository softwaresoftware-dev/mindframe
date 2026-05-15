# mindframe

Customer-installable bundle for shipping AI-agent infrastructure. Wraps existing tools (taskpilot, session-bridge, taskboard, dispatcher, librarian, browser-bridge) into one onboardable product, plus an incident-triage skill on top.

Lead use case: **AI Sentry triage** — Sentry issue lands, an ephemeral Claude triages it against the customer's vault, drafts a fix or RCA, and notifies the right team.

Mindframe-the-plugin is **manifest-first**: it ships skills, customer-domain templates (`docs/kb-schema.md`), and a `requires` list. The actual work is done by the bundled providers.

## Architecture

See [CLAUDE.md](CLAUDE.md) for the bundle composition (7 buckets), runtime flow, and decisions.

## Commands

- `/mindframe:setup` — onboarding wizard. Walks the operator through credentials per data system, validates connections, bootstraps the customer-domain knowledge base from real source systems, wires the dispatcher webhook, and runs an end-to-end smoke test.
- `/mindframe:sentry-triage` — incident-triage skill. A Sentry issue arrives; the agent investigates from logs, traces, and recent code; drafts a fix PR or RCA; writes an Incident note; notifies the configured channel.

The dashboard component lives in the sibling [`taskboard`](../taskboard/) plugin.
