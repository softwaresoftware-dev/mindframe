# Mindframe — Product Overview

**Mindframe gives an organization a knowledge base of how it actually works — and AI agents that act on it. Packaged as one installable product.**

Mindframe builds a knowledge base from the systems a team already uses (Slack, GitHub, Gmail, its infrastructure), capturing how the organization runs: its services, projects, decisions, people, and past incidents. Then it runs agents that turn that knowledge into work: producing reports and reviews, triaging incidents, answering "how does X work here."

Mindframe is not a framework you build on. It is a *stack* of six runtime layers that already do the work, packaged so a customer can install and onboard them in a single flow.

---

## The problem

An organization's working knowledge — who owns what, what was decided and why, what shipped last quarter, what broke and how it was fixed — is real, but it isn't anywhere an agent can use it. It's scattered across Slack threads, email, documents, source control, and a few people's heads.

So every time you want an AI agent to do something genuinely useful — triage an incident, draft a quarterly review, answer a new hire's question — it starts from nothing. It has the reasoning ability but not the context, and the context is exactly the slow part.

Incident response is one place this bites hard: the same five manual minutes of *which service, who owns it, what broke it last time, where the runbook is* — every time. A quarterly business review is another: a week of someone assembling what happened from a dozen tools. Different outputs, same missing ingredient.

Mindframe's answer is to make that knowledge a first-class, queryable thing — and then let agents draw on it.

## What Mindframe does

**It builds a knowledge base.** A guided setup probes the systems a team uses and bootstraps a vault: plain Markdown with structured frontmatter, grep-friendly, no embeddings, stored as a local directory the customer owns. Setup seeds it; mindframe agents add to it as they work. (The Knowledge layer is under active redesign.)

**It runs agents that act on that knowledge.** Agents reach the runtime two ways, through the same stack. An operator opens the dashboard and creates or messages a mindframe (a persistent agent that owns one live page it rewrites plus a message box). Or an event arrives: the ingress acquires it, a router decides what to do, and an ephemeral agent spawns, does one job, and exits. Either way the agent draws on the vault, reaches live systems through the perception layer, and recommends.

**The work is what a mindframe agent does.** You message a mindframe, or an event spawns one, and it grounds the request in the knowledge base plus live connectors and produces something a human can use: an incident triage with a prime-suspect commit, a drafted review, an answer with its sources. There is no library of pre-built workflows — the agent does the work directly. For event-driven work an operator wires a dispatcher recipe.

## What you get

Mindframe installs six runtime layers as one product:

| Layer | What it is |
|---|---|
| **Surface** | The dashboard. One local web app that hosts every mindframe, the knowledge base, and the operator's connections. The piece mindframe owns directly. |
| **Agent runtime** | Spawns and supervises `claude` processes, tmux-backed. Messages reach an agent over the mesh. |
| **Event ingress** | An event router that acquires external events (poll-first) and turns them into agent spawns. |
| **Knowledge** | The vault, persistent memory for the whole system. Seeded at setup, grown by mindframe agents as they work. *(Under redesign.)* |
| **Mesh** | The message bus connecting agents and humans, and the agent runtime's delivery channel. |
| **Perception** | Browser automation plus whatever MCPs and authed CLIs the operator already has — discovered live, never shipped. |

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

The first three steps install the bundle. `/mindframe:setup` is a small terminal bootstrap that births the operator's first mindframe and ends by handing off to it: onboarding continues inside the web surface, where the setup mindframe interviews the operator, discovers what the machine can already reach (no credentials are collected — agents inherit the operator's existing identity), and builds the knowledge base in front of them. Event wiring is a later chapter, driven from the surface. The model lives in [`onboarding-ux.md`](onboarding-ux.md); the flow is `setup/install.txt` plus the setup mindframe's brief at `setup/brief.md`.

## Principles

- **Capability-based.** Every dependency is an abstract capability, not a named product. Any composed layer — the agent runtime, the event router, the mesh — is swappable per customer with no change to the bundle.
- **No API keys, no stored credentials.** Agents authenticate through the Claude Code subscription — there is no Anthropic API key to provision, rotate, or leak. Mindframe stores no third-party credentials either: agents act through the operator's existing CLIs and MCPs.
- **Runs where your work lives.** Mindframe runs locally under Claude Code, against your real systems. Nothing about your organization is uploaded to run it.
- **The human owns the action.** Agents assemble knowledge and recommend. Executing a rollback, merging a fix, sending something externally — those stay with a person.

## Further reading

- [`architecture.md`](architecture.md) — the six layers in depth, what runs each,
  and the runtime flow.
- [`interfaces.md`](interfaces.md) — the contracts between layers: the event API,
  routing config, recipe format, spawn interface, mesh tools, and the Surface app API.
- [`kb-schema.md`](kb-schema.md) — the knowledge-base schema library *(under redesign)*.
