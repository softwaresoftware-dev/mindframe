# mindframe

Gives an organization a knowledge base of how it works, and AI agents that act
on it. Mindframe builds the knowledge base from the systems a team already uses
(Slack, GitHub, Gmail, infrastructure), then runs agents that turn that
knowledge into work: reports and reviews, incident triage, answers to "how does
X actually work here."

Mindframe is **manifest-first**: it ships skills, the customer-domain
knowledge-base schema (`docs/kb-schema.md`), and a `requires` list. The work is
done by the layers it composes. The one exception is the **Surface** (the
dashboard), which mindframe owns and ships directly.

## The stack

Six runtime layers, each a plugin or MCP bound by capability (except the Surface):

| Layer | What runs it |
|---|---|
| **Surface** | the dashboard (`dashboard/`) — one server for every mindframe |
| **Agent runtime** | `taskpilot` — tmux-backed `claude`, fed over the Mesh |
| **Event ingress** | `dispatcher` (`:8911`) — dedupe, route, spawn |
| **Knowledge** | the vault at `~/.mindframe/vault` *(under redesign)* |
| **Mesh** | `session-bridge` (`:8910`) — agent↔agent↔human |
| **Perception** | `claude-browser-bridge` + adopt-on-install MCPs |

See [`CLAUDE.md`](CLAUDE.md) for the layer table with state and providers, and
[`docs/architecture.md`](docs/architecture.md) for the full reference.

## Commands

- `/mindframe:setup` — onboarding. A terminal bootstrap births the operator's
  first mindframe, which runs the rest of setup inside the Surface: probes the
  environment, inherits the operator's identity, assembles and bootstraps the
  vault, and wires the first event.
- `/mindframe:doctor` — diagnose and heal the bundle. Walks every layer, checks
  for missing capabilities, dead daemons, and broken config, fixes what is safe,
  and reports the rest with evidence.

A mindframe agent does the work directly — interactively in its surface, or per
event via a dispatcher recipe an operator wires. Mindframe ships no pre-built
workflow artifacts. See [`docs/`](docs/) for the product overview, architecture,
and interfaces.
