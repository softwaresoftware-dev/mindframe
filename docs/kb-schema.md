# KB Schema (customer-domain)

The persistent memory layer for a mindframe deployment. A markdown + frontmatter Obsidian-style vault, owned by the customer, queried and maintained by the librarian agent.

This schema is the contract between the librarian, the setup wizard, the wedge skills (sentry-triage, on-call-buddy, pr-review, etc.), and the validator. Skills query the catalog and read notes. The librarian writes notes. The validator enforces invariants.

This is the customer-domain schema. Thatcher's personal vault stays on its existing project-tracker schema and does not migrate.

## Design principles

1. **Markdown + frontmatter**, not a database. Greppable, human-editable, diffable in git, renderable in Obsidian.
2. **Frontmatter is the contract.** Body is for prose humans and agents both read.
3. **Foreign keys are names, not paths.** Resilient to reorganization.
4. **CATALOG.md is the index.** Agents read it first to find the right note. Body queries are a second hop.
5. **Per-customer single-tenant.** One vault, one customer, one git repo. No cross-tenant joins.
6. **The librarian is the only writer.** Other agents request changes through the librarian via the session-bridge mesh. The librarian validates, writes, commits.
7. **Live state is not in the vault.** Active alerts, current PRs, deploy status: queried from source systems at runtime.
8. **Secrets are referenced, never stored.** Frontmatter holds the keychain entry name; the value never appears in markdown.

## Entity catalog

Three layers. Eleven entity types. Files per type vary from one (Glossary) to hundreds (Incidents over time).

| Layer | Entity | Files | One-line role |
|-------|--------|-------|---------------|
| Things | Service | one per service | Deployable software unit |
| Things | Repository | one per repo | Source code |
| Things | Team | one per team | People, channels, on-call |
| Things | Product | one per product | Customer-facing capability |
| Things | Integration | one per system | External system endpoint + auth pointer |
| Events | Project | one per initiative | In-flight work |
| Events | Decision | one per ADR | Architectural choice with rationale |
| Events | Incident | one per failure | Past failure with root cause + fix |
| Knowledge | Runbook | one per procedure | Operational response |
| Knowledge | Convention | one per standard | Engineering policy |
| Knowledge | Glossary | one file total | Domain terms |

## Identity rules

- Names use kebab-case.
- Names are unique within an entity type. `payment-api` can be a Service and a Repository, but not two Services.
- Event-type slugs prefix the date: `YYYY-MM-DD-<topic>`. Sorts chronologically, deduplicates same-day topics.
- File path matches the name or slug exactly: `Services/payment-api.md`, `Incidents/2026-03-12-payment-api-outage.md`.
- Frontmatter `name` or `slug` field must match the filename minus `.md`.

## Per-entity schema

### Service

A deployable software unit. The thing Sentry projects, log streams, K8s deployments, and metrics attach to.

```yaml
---
type: service
name: payment-api
description: "Charges customer payment methods, issues refunds"
criticality: tier-1                     # tier-1 (revenue-critical) | tier-2 (degraded UX) | tier-3 (internal)
runtime: python-3.11                    # informational
environments: [production, staging]
repos: [payment-api]                    # FK -> Repository.name
team: payments-team                     # FK -> Team.name
products: [checkout, refunds]           # FK -> Product.name
depends_on: [auth-api, redis-payments]  # FK -> Service.name (no cycles, no self-ref)
deploy_target: gke/payments-prod        # informational
sentry_project: payment-api-prod        # external ID, queried by sentry-triage
gcp_service: payment-api                # external ID, queried by gcp-logging
github_team: payments                   # for codeowners lookups
---
```

Body: prose description, architecture sketch, gotchas, links to ADRs, anything an agent or new engineer should know before changing this service.

### Repository

Source code. Most often 1:1 with a Service, but monorepos may contain many services.

```yaml
---
type: repo
name: payment-api
description: "Python service for payment processing"
github: customer-org/payment-api
default_branch: main
languages: [python]
build_system: bazel
ci: github-actions
deploy_via: argo-cd
review_team: payments-team              # FK -> Team.name
review_policy: pr-review-policy         # FK -> Convention.slug
services: [payment-api]                 # FK -> Service.name; reverse of Service.repos
---
```

Body: how to build locally, project layout, where entry points live, where tests live.

### Team

People plus ownership plus channels. Where notifications go.

```yaml
---
type: team
name: payments-team
description: "Owns payments, refunds, billing"
slack_channel: "#payments"
slack_alerts_channel: "#payments-alerts"
email_alias: payments@customer.com
on_call_schedule: payments-rotation     # external ID (PagerDuty, opsgenie)
manager: alice@customer.com
members:
  - alice@customer.com
  - bob@customer.com
github_team: payments
---
```

Body: charter, scope, working hours, escalation paths.

### Product

Customer-facing capability. Different lens from Service. Lets agents reason about user impact.

```yaml
---
type: product
name: checkout
description: "End-to-end purchase flow"
status: ga                              # proposed | beta | ga | deprecated
owner_team: payments-team               # FK -> Team.name
critical_path: true                     # P0 if this breaks, customers cannot transact
services: [checkout-ui, payment-api, cart-api, inventory-api]   # FK -> Service.name
slo_targets:
  availability: "99.95%"
  p99_latency_ms: 500
docs_url: https://internal.customer.com/products/checkout
---
```

Body: what users see, key flows, success metrics, current concerns.

### Integration

External system endpoint plus auth pointer. One per system per environment when isolation matters.

```yaml
---
type: integration
system: sentry                          # sentry | gcp | github | grafana | slack | pagerduty | datadog | stripe | aws | ...
name: sentry-prod
description: "Production error monitoring"
url: https://customer-org.sentry.io
org: customer-org
auth_method: api_token                  # api_token | oauth | service_account | webhook
auth_secret_ref: sentry_token_prod      # name of keychain entry; NEVER the value
scopes: [project:read, issue:write]
covers_environments: [production, staging]
covers_services: all                    # all | [service-name, ...]
rotation_due: 2026-09-01
maintainer: platform-team               # FK -> Team.name
---
```

Body: how to test connectivity, escalation if credentials expire, links to vendor admin console.

### Project

In-flight initiative. The "what are we working on right now" question.

```yaml
---
type: project
slug: 2026-q2-fraud-detection-v2
title: Fraud detection v2
status: active                          # proposed | active | paused | shipped | abandoned
priority: p1                            # p0 | p1 | p2 | p3
start_date: 2026-04-01
target_date: 2026-06-30
shipped_date: ~
owner_team: payments-team               # FK -> Team.name
sponsor: alice@customer.com
services_touched: [payment-api, fraud-service]      # FK -> Service.name
products_affected: [checkout]                       # FK -> Product.name
linked_decisions: [2026-03-15-fraud-ml-stack]       # FK -> Decision.slug
linked_incidents: [2026-02-04-fraud-bypass]         # FK -> Incident.slug
external_tracker: https://linear.app/customer/project/abc123
---
```

Body: goals, scope, milestones, current status, blockers, open questions.

### Decision

Architectural choice with rationale. Standard ADR shape.

```yaml
---
type: decision
slug: 2026-03-15-postgres-over-dynamo
title: Use Postgres for orders table
date: 2026-03-15
status: accepted                        # proposed | accepted | superseded | deprecated
deciders: [alice@customer.com, dave@customer.com]
context_services: [orders-api]          # FK -> Service.name
context_products: [checkout]            # FK -> Product.name
supersedes: ~                           # FK -> Decision.slug
superseded_by: ~                        # FK -> Decision.slug
---
```

Body: standard ADR sections.

```markdown
## Context
What problem are we solving, what constraints apply.

## Decision
What we chose and why.

## Consequences
What this enables, what this prevents, what gets harder.

## Alternatives considered
Options we weighed and why we rejected them.
```

### Incident

Past failure. Postmortem-lite shape. Authored by humans for serious incidents, by `/sentry-triage` for routine ones.

```yaml
---
type: incident
slug: 2026-03-12-payment-api-redis-outage
title: payment-api Redis outage
date: 2026-03-12
duration_min: 47
severity: p1                            # p0 | p1 | p2 | p3
services_affected: [payment-api, refunds-api]       # FK -> Service.name
products_affected: [checkout, refunds]              # FK -> Product.name
root_cause: "Connection pool exhaustion under traffic spike"
detection: "Sentry alert on Redis ConnectionTimeoutError"
resolution: "Increased pool size from 20 to 100, deployed via PR #1234"
fix_pr: customer-org/payment-api#1234
related_runbook: payment-api-redis-timeout          # FK -> Runbook.slug
related_decision: ~                                 # FK -> Decision.slug
authored_by: librarian                              # human | librarian | sentry-triage
---
```

Body: timeline, impact summary, what went well, what could go better, action items with owners.

### Runbook

Operational response procedure. Triggered by symptom, scoped to a service.

```yaml
---
type: runbook
slug: payment-api-redis-timeout
title: Payment API Redis Timeout
service: payment-api                    # FK -> Service.name
symptom: "Redis ConnectionTimeoutError on /charge or /refund"
applies_when: "kind == 'redis.TimeoutError' AND service == 'payment-api'"
severity_if_unaddressed: p1
notify: payments-team                   # FK -> Team.name (who to ping while running this)
---
```

Body: numbered steps. Diagnostic queries first, then decision tree, then mitigation, then escalation. Plain markdown so humans and agents both read it.

### Convention

Engineering standard. Small set, broad applicability. Examples: `pr-review-policy`, `branching-strategy`, `code-style-python`, `deploy-windows`, `secrets-handling`, `dependency-policy`.

```yaml
---
type: convention
slug: pr-review-policy
title: PR Review Policy
applies_to: [code]                      # code | infra | docs | all
enforcement: required                   # required | recommended | suggested
owner_team: platform-team               # FK -> Team.name
last_reviewed: 2026-04-28
---
```

Body: the actual rules. Imperative form so agents follow them when drafting changes.

### Glossary

One file at vault root. All terms inline, alphabetized. Keeps the vault from accumulating hundreds of two-line files.

`Glossary.md`:

```markdown
---
type: glossary
last_reviewed: 2026-04-28
maintainer: docs-team
---

# Glossary

## A

### Authoring service
The component that ... See [[Services/auth-api]].

### ARR
Annual Recurring Revenue. Reported monthly to ...

## B

### BMU
Billing Management Unit. Internal abstraction representing ...
```

Cross-link to Services, Products, and Decisions where relevant. Wikilinks are stable across renames if Obsidian-style.

## CATALOG.md

The librarian reads CATALOG.md first on every query. It encodes the most-queried fields per entity so the agent can filter without opening every note.

```markdown
# Catalog

Last updated: 2026-04-28

## Services
| Name | Tier | Team | Repos | Products | Sentry project |
|------|------|------|-------|----------|----------------|
| payment-api | tier-1 | payments-team | payment-api | checkout, refunds | payment-api-prod |

## Repositories
| Name | GitHub | Languages | Services |

## Teams
| Name | Slack | On-call | Members |

## Products
| Name | Status | Owner | Critical path |

## Integrations
| System | Name | URL | Covers environments |

## Projects (active)
| Slug | Title | Priority | Owner | Target |

## Projects (shipped, last 90 days)
| Slug | Title | Shipped | Owner |

## Decisions
| Slug | Title | Status | Date |

## Incidents (last 90 days)
| Slug | Severity | Services | Date | One-line cause |

## Runbooks
| Slug | Service | Symptom |

## Conventions
| Slug | Applies to | Enforcement |

## Glossary
1 file. See Glossary.md.
```

### Catalog rolling windows

Incidents and shipped Projects roll out of CATALOG.md after 90 days. The full notes still exist in `Incidents/` and `Projects/`. Older history is reachable by direct read or by asking the librarian for a longer time window. This bounds CATALOG.md growth without losing data.

## Directory layout

```
<customer-vault>/
  CATALOG.md
  CLAUDE.md                             # operating procedures for the librarian
  Glossary.md

  Services/
    payment-api.md
    auth-api.md
  Repositories/
    payment-api.md
  Teams/
    payments-team.md
  Products/
    checkout.md
  Integrations/
    sentry-prod.md
    gcp-prod.md
    github.md

  Projects/
    2026-q2-fraud-detection-v2.md
  Decisions/
    2026-03-15-postgres-over-dynamo.md
  Incidents/
    2026-03-12-payment-api-redis-outage.md

  Runbooks/
    payment-api-redis-timeout.md
  Conventions/
    pr-review-policy.md
    branching-strategy.md
```

Flat-by-type beats folder-per-service because runbooks and incidents are cross-cutting, each type has its own update cadence, and the catalog stays simpler.

## Schema invariants

The validator enforces these at write time and on a periodic full sweep.

### Foreign-key integrity

| From | Field | Must exist as |
|------|-------|---------------|
| Service | repos[*] | Repository |
| Service | team | Team |
| Service | products[*] | Product |
| Service | depends_on[*] | Service (no self-ref, no cycles) |
| Repository | review_team | Team |
| Repository | review_policy | Convention |
| Repository | services[*] | Service, with reverse pointer in Service.repos |
| Product | owner_team | Team |
| Product | services[*] | Service, with reverse pointer in Service.products |
| Integration | covers_services | "all" or list of Services |
| Integration | maintainer | Team |
| Project | owner_team | Team |
| Project | services_touched[*] | Service |
| Project | products_affected[*] | Product |
| Project | linked_decisions[*] | Decision |
| Project | linked_incidents[*] | Incident |
| Decision | context_services[*] | Service |
| Decision | context_products[*] | Product |
| Decision | supersedes / superseded_by | Decision |
| Incident | services_affected[*] | Service |
| Incident | products_affected[*] | Product |
| Incident | related_runbook | Runbook |
| Incident | related_decision | Decision |
| Runbook | service | Service |
| Runbook | notify | Team |
| Convention | owner_team | Team |

### Catalog integrity

- Every CATALOG row points to a real note.
- Every note (except Glossary) has a CATALOG row.
- Catalog row fields match the source note's frontmatter.

### Identity integrity

- Names unique per entity type.
- Slugs unique per entity type.
- Filename matches `name` or `slug` field.
- Event slugs start with `YYYY-MM-DD-`.

### Bidirectional links

- `Service.repos` and `Repository.services` agree.
- `Service.products` and `Product.services` agree.
- `Decision.supersedes` and the older Decision's `superseded_by` agree.

The validator runs as `/mindframe:validate-kb` and as a pre-commit hook in the vault repo.

## Bootstrap

Customer vaults are populated in three passes.

### Pass 1: auto-discovery (setup wizard)

Read what already exists in source systems, write stub notes, present for confirmation.

| Source | Discovers | Produces |
|--------|-----------|----------|
| GitHub org | All repos | Repository notes (1 per repo), Service notes inferred 1:1 |
| Sentry org | All projects | Service notes with `sentry_project` field |
| GCP project | Services, deployments | Service notes with `gcp_service` field |
| PagerDuty / opsgenie | Schedules | Team notes with `on_call_schedule` field |
| Slack workspace | Channels matching team patterns | Team `slack_channel` fields |
| GitHub teams | Membership | Team `members` and `github_team` fields |

Auto-discovered entries get frontmatter and a body stub: `_[needs description]_`. The wizard then presents results to the user for confirm / edit / delete.

### Pass 2: manual seeding (setup wizard)

Auto-discovery cannot infer these. The wizard prompts for the top 3 to 5 of each:

- Products (customer-facing capabilities)
- Active Projects
- Foundational Decisions / ADRs
- Conventions (link to existing engineering handbook if one exists)
- Glossary terms (top 10)

### Pass 3: organic growth (post-deploy)

Runbooks and Incidents start empty. Populated by skills as they run:

- `/sentry-triage` writes a thin Incident note on resolution.
- `/incident-postmortem` upgrades Incidents from "thin" to "full" when a human runs it.
- `/runbook-from-incident` proposes a Runbook draft after a recurring incident pattern.
- `/decision-record` prompts during `/sentry-triage` if the fix represents a real architectural choice.

## Authoring discipline

The librarian is the sole writer to the vault. Other agents send change requests through the session-bridge mesh:

```
sentry-triage  -> librarian: "create Incident with these fields"
pr-review      -> librarian: "Service X gained a dependency on Service Y"
on-call-buddy  -> librarian: "Runbook X step 3 was wrong, here's the fix"
```

The librarian:

1. Validates the change against the schema.
2. Writes / updates the note.
3. Updates CATALOG.md.
4. Updates bidirectional links if applicable.
5. Commits with a message like `incident: 2026-04-28 payment-api timeout (auto from sentry-triage)`.

This keeps schema enforcement in one place and prevents race conditions between concurrent agents.

## What is NOT in the vault

| Out of scope | Where it lives |
|--------------|----------------|
| Live alerts, current PRs, deploy status | Sentry, GitHub, deploy system, queried at runtime |
| Secret values | System keychain, referenced by name in frontmatter |
| Source code | GitHub repos, vault stores metadata only |
| Customer / CRM data | Customer's CRM (Salesforce, HubSpot) |
| Logs, metrics, traces | gcp-logging, Grafana, Sentry, queried at runtime |
| Personally identifiable information | Anywhere except vault |

## Versioning and ownership

- Vault is a git repository, customer-owned (their GitHub or GitLab org).
- Branch model: writes go to `main` directly. Big restructures use feature branches with human review.
- Commits are small-grained. The librarian commits per change, not per session.
- The setup wizard initializes the vault and pushes the first commit. The customer owns the repo from that point.

## Open questions to revisit before implementation

1. Multi-environment Integration: one note per system or one per environment? Current schema supports both (`Integration.covers_environments` list); decide convention during wizard build.
2. Monorepo handling: when one Repository hosts ten Services, do we want a `paths_per_service` field on Repository to disambiguate? Defer until first monorepo customer hits it.
3. Glossary pagination: at 500+ terms a single file gets unwieldy. Split-by-letter (`Glossary/A.md`, etc.) when that becomes a problem; not before.
4. Project to Decision back-pressure: if a Project produces multiple Decisions, the Project's `linked_decisions` list grows. Probably fine; flag if it exceeds 20 on any project.
5. Schema versioning: when we add a new entity type or field, how do existing customer vaults migrate? Out of scope for v1; revisit when we have two customers.
