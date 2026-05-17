# Mindframe — Product Overview

**Mindframe is AI-agent incident-response infrastructure, packaged as one installable product.**

It gives an engineering org a set of agents that watch its stack and act on
it: when an error fires, an agent investigates the root cause, checks the
runbooks, names the suspect change, and hands the on-call human a structured
recommendation — before a person has finished reading the alert.

Mindframe is not a framework you build on and not a dashboard you log into. It
is a *bundle* — seven components that already do the work, wired together so a
customer can install and onboard them in a single flow.

---

## The problem

Incident response is mostly the same five minutes, over and over:

1. An alert fires — a Sentry error, a `CrashLoopBackOff`, a PagerDuty page.
2. Someone context-switches out of what they were doing.
3. They pull logs, skim recent commits, guess which deploy did it.
4. They search the wiki for a runbook — if one exists, and if they can find it.
5. They post a summary somewhere and decide whether to roll back.

Steps 2–5 are investigation, not judgement. They are slow because they are
manual, and they are manual because the context — which service, who owns it,
what broke it last time, where the runbook is — lives in a dozen tools and a
few people's heads.

Mindframe automates steps 2–4 so the human arrives at step 5 with the work
already done.

## What Mindframe does

Mindframe runs agents on two paths:

- **The push path** reacts to events. A webhook (Sentry, PagerDuty, GitHub)
  hits an ingress; a router decides what to do; an ephemeral agent spawns,
  runs an incident-triage skill, and posts a recommendation. The agent is gone
  when it's done.
- **The pull path** watches continuously. A dashboard probes services,
  daemons, agents, and telemetry and renders current status — the eyes that
  notice what no event announced.

Both paths draw on the same **knowledge base**: a per-customer vault of
services, repositories, runbooks, owners, on-call rotations, and past
incidents — plain Markdown with structured frontmatter, grep-friendly, no
embeddings. The agent reads it the way a senior engineer would: "this is
`payments-api`, it's owned by backend-payments, here's the OOM runbook, here's
what happened in March."

The output of a triage run is a **recommendation a human can act on in one
move**: the failure mode, a prime-suspect commit with a confidence score, the
matching runbook, and a rollback affordance. The agent recommends; the human
approves.

## What you get

Mindframe installs seven components as one product:

| Component | What it is |
|---|---|
| **Agent runtime** | Spawns and supervises `claude` processes — reboot-persistent, tmux-backed — plus a mesh so agents and humans can message each other. |
| **Knowledge base** | The customer vault and a librarian agent that keeps it correct. Persistent memory for the whole system. |
| **Event router** | A public webhook ingress and a router that turns events into agent spawns. |
| **Setup wizard** | `/mindframe:setup` — a Claude-driven onboarding that discovers your environment, collects credentials, bootstraps the vault, and runs a smoke test. |
| **Incident-triage skill** | The thing the customer pays for: root-cause analysis → draft fix → notify. Sentry and Kubernetes variants ship in the box. |
| **Dashboard** | A generative-UI status surface — describe what you want to see, the agent builds it. |
| **Perception MCPs** | Browser automation plus adopt-on-install connectors for Sentry, GCP logging, GitHub, Grafana, and Slack. |

## Who it's for

Engineering organizations that run production software and carry a pager —
teams with on-call rotations, a backlog of half-written runbooks, and an MTTR
they would like to cut. Mindframe is sold as a vendor-installable bundle: a
provider can stand it up against a client's stack, or a team can dogfood it
against its own.

## How it's installed

Mindframe is a Claude Code plugin bundle. It declares the *capabilities* it
needs; the `softwaresoftware` resolver picks providers that fit the host
environment and installs them in dependency order.

```
claude plugin marketplace add softwaresoftware-dev/softwaresoftware-plugins
claude plugin install softwaresoftware@softwaresoftware-plugins
/softwaresoftware:install mindframe
/mindframe:setup
```

The first three steps install the bundle. `/mindframe:setup` does the rest:
probes the machine for the data systems already in use, walks the operator
through credentials, bootstraps the knowledge base from real source systems,
wires the event router, and runs an end-to-end smoke test.

## Principles

- **Capability-based.** Every dependency is an abstract capability, not a named
  product. Notifications go to Slack today and email tomorrow by swapping a
  provider — no change to the bundle.
- **No API keys.** Agents authenticate through the Claude Code subscription.
  There is no Anthropic API key to provision, rotate, or leak.
- **Runs where your code runs.** Mindframe runs locally under Claude Code,
  against your real environment. Nothing about your stack is uploaded to run it.
- **The human owns the action.** Agents investigate and recommend. Executing a
  rollback, merging a fix, paging a team — those stay with a person.

## Further reading

- [`architecture.md`](architecture.md) — how the seven components fit together,
  the push/pull split, and the runtime flows.
- [`interfaces.md`](interfaces.md) — the contracts between subsystems: the
  event API, routing config, recipe format, and knowledge-base schema.
- [`kb-schema.md`](kb-schema.md) — the customer-domain knowledge-base contract.
