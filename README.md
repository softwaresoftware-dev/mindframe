# mindframe

Customer-installable bundle that gives an organization a knowledge base of how
it works — and AI agents that act on it.

Mindframe builds a knowledge base from the systems a team already uses (Slack,
GitHub, Gmail, infrastructure), then runs agents that turn that knowledge into
work: reports and reviews, incident triage, answers to "how does X actually
work here."

Mindframe-the-plugin is **manifest-first**: it ships skills, the customer-domain
knowledge-base schema (`docs/kb-schema.md`), and a `requires` list. The actual
work is done by the bundled providers (taskpilot, session-bridge, taskboard,
dispatcher, knowledge-base, browser-bridge).

## Architecture

See [CLAUDE.md](CLAUDE.md) for the bundle composition (7 buckets), runtime flow,
and decisions. See [`docs/`](docs/) for the product overview, architecture, and
subsystem interfaces.

## Commands

- `/mindframe:setup` — onboarding wizard. Probes the environment for available
  data sources, walks the operator through credentials, bootstraps the
  customer-domain knowledge base from real source systems, wires the event
  router, and runs an end-to-end smoke test.
- `/mindframe:doctor` — diagnose and heal the bundle. Walks every plugin —
  agent runtime, knowledge base, event router, dashboard, perception MCPs —
  checks for missing capabilities, dead daemons, broken config, and schema
  drift, fixes what is safe to fix, and reports the rest with evidence.
No deliverable skills ship in the current bundle. The deliverable-skills bucket
is part of the architecture; first entries pending redesign.

The dashboard component lives in the sibling [`taskboard`](../taskboard/) plugin.
