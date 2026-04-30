# mindframe

Customer-installable bundle for shipping AI-agent infrastructure. Wraps existing tools (taskpilot, session-bridge, taskboard, dispatcher, librarian, browser-bridge) into one onboardable product, plus a wedge skill on top.

First wedge: **AI Sentry triage** — Sentry issue lands, an ephemeral Claude triages it against the customer's vault, drafts a fix or RCA, and notifies the right team.

Mindframe-the-plugin is **manifest-first**: it ships skills, customer-domain templates (`docs/kb-schema.md`), and a `requires` list. The actual work is done by the bundled providers.

## Architecture

See [CLAUDE.md](CLAUDE.md) for the bundle composition (7 buckets), runtime flow, and decisions.

## Commands

- `/mindframe:setup` — onboarding wizard (placeholder, see SKILL.md)
- `/mindframe:sentry-triage` — wedge skill (placeholder, see SKILL.md)

## Status

v0.1.0 — manifest scaffolded, KB schema documented, skills are placeholders.
The dashboard component lives in the sibling [`taskboard`](../taskboard/) plugin (formerly the body of this plugin).
