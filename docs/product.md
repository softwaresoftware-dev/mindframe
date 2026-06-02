# Mindframe — Product Overview

**Mindframe gives an organization a knowledge base of how it actually works — and AI agents that act on it. Packaged as one installable product.**

Mindframe builds a knowledge base from the systems a team already uses — Slack, GitHub, Gmail, its infrastructure — capturing how the organization runs: its services, projects, decisions, people, and past incidents. Then it runs agents that turn that knowledge into work: producing reports and reviews, triaging incidents, answering "how does X work here," watching for problems.

Mindframe is not a framework you build on and not a dashboard you log into. It is a *bundle* — seven components that already do the work, wired together so a customer can install and onboard them in a single flow.

---

## The problem

An organization's working knowledge — who owns what, what was decided and why, what shipped last quarter, what broke and how it was fixed — is real, but it isn't anywhere an agent can use it. It's scattered across Slack threads, email, documents, source control, and a few people's heads.

So every time you want an AI agent to do something genuinely useful — triage an incident, draft a quarterly review, answer a new hire's question — it starts from nothing. It has the reasoning ability but not the context, and the context is exactly the slow part.

Incident response is one place this bites hard: the same five manual minutes of *which service, who owns it, what broke it last time, where the runbook is* — every time. A quarterly business review is another: a week of someone assembling what happened from a dozen tools. Different deliverables, same missing ingredient.

Mindframe's answer is to make that knowledge a first-class, queryable thing — and then let agents draw on it.

## What Mindframe does

**It builds and maintains a knowledge base.** A guided setup probes the systems a team uses and bootstraps a per-customer vault — plain Markdown with structured frontmatter, grep-friendly, no embeddings, owned by the customer as a git repo. A librarian agent keeps it correct over time.

**It runs agents that act on that knowledge**, on two paths:

- **The push path** reacts to events. A webhook (Sentry, PagerDuty, GitHub) hits an ingress; a router decides what to do; an ephemeral agent spawns, runs a deliverable skill, produces its output, and exits.
- **The pull path** watches continuously. A dashboard probes services, daemons, agents, and telemetry and renders current status — the things no event announced.

**The work itself is a library of deliverable skills.** Each takes a request, grounds it in the knowledge base plus live connectors, and produces something a human can use: an incident triage with a prime-suspect commit, a drafted review, an answer with its sources. Incident triage is the first skill in the library; it is not the whole product.

## What you get

Mindframe installs seven components as one product:

| Component | What it is |
|---|---|
| **Agent runtime** | Spawns and supervises `claude` processes — reboot-persistent, tmux-backed — plus a mesh so agents and humans can message each other. |
| **Knowledge base** | The customer vault and a librarian agent that keeps it correct. Persistent memory for the whole system. |
| **Event router** | A public webhook ingress and a router that turns events into agent spawns. |
| **Setup wizard** | `/mindframe:setup` — a Claude-driven onboarding that discovers the environment, collects credentials, bootstraps the knowledge base, and runs a smoke test. |
| **Deliverable skills** | A library of skills that turn the knowledge base into work — incident triage, reviews and reports, answers. Incident triage (Sentry, Kubernetes) ships first; the library grows. |
| **Dashboard** | A generative-UI status surface — describe what you want to see, the agent builds it. |
| **Perception MCPs** | Browser automation plus adopt-on-install connectors for Slack, GitHub, Gmail, Sentry, GCP logging, Grafana. |

## Who it's for

Organizations that want their institutional knowledge to be usable by agents — engineering and ops teams first, and the business functions around them. The common thread is a team whose real context is scattered across tools and people, and who would rather have an agent assemble and act on it than do it by hand. Mindframe is sold as a vendor-installable bundle: a provider can stand it up against a client's stack, or a team can dogfood it against its own.

## How it's installed

Mindframe is a Claude Code plugin bundle. It declares the *capabilities* it needs; the `softwaresoftware` resolver picks providers that fit the host environment and installs them in dependency order.

```
claude plugin marketplace add softwaresoftware-dev/softwaresoftware-plugins
claude plugin install softwaresoftware@softwaresoftware-plugins
/softwaresoftware:install mindframe
/mindframe:setup
```

The first three steps install the bundle. `/mindframe:setup` does the rest: probes the machine for the data systems already in use, walks the operator through credentials, bootstraps the knowledge base from real source systems, wires the event router, and runs an end-to-end smoke test.

> **Onboarding redesigned 2026-06-02.** Setup is now UI-based: a small terminal bootstrap births the operator's first mindframe, which facilitates the rest of onboarding in a web surface as the user watches their knowledge base take shape. The model lives in [`onboarding-ux.md`](onboarding-ux.md); the flow is the hosted `install.txt` plus the setup mindframe's brief at `setup/brief.md`. (`install-flow-v2.md` is a superseded earlier draft.)

## Principles

- **Capability-based.** Every dependency is an abstract capability, not a named product. Notifications go to Slack today and email tomorrow by swapping a provider — no change to the bundle.
- **No API keys.** Agents authenticate through the Claude Code subscription. There is no Anthropic API key to provision, rotate, or leak.
- **Runs where your work lives.** Mindframe runs locally under Claude Code, against your real systems. Nothing about your organization is uploaded to run it.
- **The human owns the action.** Agents assemble knowledge and recommend. Executing a rollback, merging a fix, sending something externally — those stay with a person.

## Further reading

- [`architecture.md`](architecture.md) — how the seven components fit together,
  the push/pull split, and the runtime flows.
- [`interfaces.md`](interfaces.md) — the contracts between subsystems: the
  event API, routing config, recipe format, and knowledge-base schema.
- [`kb-schema.md`](kb-schema.md) — the knowledge-base schema library: the meta-schema, core entities, domain packs, and the per-install manifest.
