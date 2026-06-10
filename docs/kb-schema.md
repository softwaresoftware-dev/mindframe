# KB Schema — Customer Knowledge Base

The persistent memory layer for a mindframe deployment: a Markdown + frontmatter Obsidian-style vault, owned by the customer — a plain local directory of notes, populated at setup and by the mindframe agents that read and write it.

> **Status:** descriptive of today's vault. The Knowledge layer is under redesign in a separate effort — treat this schema as current, not final.

## What this document is

This document is the **library**, not the contract.

Different organizations have different entities — a software company has Services and Repositories, a paper mill has Machines and Suppliers, a law firm has Matters. No fixed entity list fits all of them. So mindframe's schema has two parts:

- **The meta-schema** — the universal, fixed rules every entity obeys regardless of domain. This *is* the contract, and it never changes without a version bump.
- **The entity library** — core entities (every install gets them) and custom entities (synthesized per install). Core is fixed; everything domain-specific is custom, minted at setup.

Each deployment assembles its own schema from the library and records it in the vault's **`schema.yaml` manifest** (see "The schema manifest"). The manifest is the contract *for that vault*. Skills read the manifest — never a hardcoded entity list.

## Design principles

1. **Markdown + frontmatter**, not a database. Greppable, human-editable, diffable in git, renderable in Obsidian.
2. **Frontmatter is the contract.** The body is prose that humans and agents both read.
3. **Foreign keys are names, not paths.** Resilient to reorganization.
4. **CATALOG.md is the index.** Agents read it first; opening notes is the second hop.
5. **Per-customer single-tenant.** One vault, one customer, one local directory.
6. **Every writer validates against the schema.** The vault is written by setup's bootstrap and by mindframe agents as they work; each conforms a note to `schema.yaml` before it writes. The schema is the gate, not a dedicated agent.
7. **Live state is not in the vault.** Active alerts, current PRs, deploy status: queried from source systems at runtime.
8. **Secrets are referenced, never stored.** Frontmatter holds a keychain entry name; the value never appears in markdown.
9. **The schema is per-install.** The meta-schema is fixed; the entity set is assembled at setup and recorded in `schema.yaml`.

---

## The meta-schema

The fixed, universal contract. Every entity in every deployment — core or custom — obeys these rules.

### The four layers

Every entity type belongs to exactly one **layer** — a category answering a different question about the organization:

| Layer | Answers | Nature |
|-------|---------|--------|
| **Thing** | what exists | persistent entities you track the current state of |
| **Event** | what happened | immutable, dated records of an occurrence |
| **Knowledge** | what's true / what the rules are | declarative reference |
| **Process** | how the organization operates | the procedures and practices it runs |

A layer is a *category of entity types*, not an entity itself. The Thing layer holds Person, Team, …; the Process layer holds whatever procedures a deployment defines. A layer may be entirely populated by custom entity types (the Process layer has no core types — every Process entity is custom).

### Identity rules

- Entity **type** names are lowercase kebab-case (`service`, `code-review`).
- Each entity instance has a **name** (Things, Knowledge, Process) or a **slug** (Events).
- Names/slugs are kebab-case and unique within an entity type. `payments` can be a Team and a Customer, but not two Teams.
- **Event slugs prefix the date:** `YYYY-MM-DD-<topic>` — sorts chronologically, deduplicates same-day topics. Only Events do this; Things/Knowledge/Process use plain names.
- The file path matches the name/slug exactly: `Services/payment-api.md`, `Incidents/2026-03-12-payment-api-outage.md`.
- Frontmatter `name` or `slug` must match the filename minus `.md`.

### Entity definition

Every entity type — wherever it comes from — is defined by:

- a `type` (kebab-case)
- a **layer** (one of the four)
- an **identity** mode (`name` or `slug`)
- a **directory** (flat-by-type: `People/`, `Runbooks/`, …)
- a set of **fields** (frontmatter keys)
- a set of **foreign keys** — fields whose value is the name/slug of another entity

Every note carries `type` in its frontmatter. The note does *not* carry its layer — the manifest maps type → layer.

### Foreign keys

A foreign key names another entity; its target must resolve to an existing note. An FK declares a **target**, which is one of:

- **A single entity type.** The value must name a note of that type. Written as the bare type name: `team: team`.
- **A whole layer.** The value may name *any* entity type in that layer. Written `any:<layer>`: `affected: any:thing` means the value may name any Thing — a Service, a Machine, a Product. The four layer names (`thing`, `event`, `knowledge`, `process`) are reserved for this; no entity type may use them.

Layer-wide targets exist because some relationships are inherently polymorphic — an Incident's `affected` points at whatever broke, which differs by domain. Without them you would hardcode one type (and break on the next customer) or carry a field per type (`services_affected`, `machines_affected`, …). The surface is small: in the core schema only `Incident.affected` and `Incident.related_process` are layer-wide.

Self-referential FKs are allowed (`manager: person`) but must not form cycles.

**The `owner` convention.** Most Thing and Knowledge entities carry an `owner: person` FK naming the person accountable for the entry. This is what makes the knowledge graph a connected hub-and-spoke rather than a dust cloud of orphan notes — every repo, product, service, decision, convention, and glossary term links back to a person, and in a single-operator deployment that person is the gravitational center of the graph (the dashboard's first-run view seeds on exactly this node). Writers must fill `owner` whenever the type defines it; an entity with no resolvable FK is an orphan. The graph builds edges from frontmatter FKs *and* body `[[wikilinks]]`, so a relationship asserted either way connects.

---

## The schema manifest

Each vault carries `schema.yaml` at its root — the assembled, self-contained schema for that deployment. Setup generates it; mindframe agents read it.

```yaml
schema_version: 2
entities:
  person:
    layer: thing
    source: core                           # core | custom
    identity: name
    directory: People
    foreign_keys: { team: team, manager: person }
  runbook:
    layer: process
    source: custom                         # synthesized by setup, confirmed by the operator
    identity: slug
    directory: Runbooks
    foreign_keys: { service: service, notify: team }
  incident:
    layer: event
    source: core
    identity: slug
    directory: Incidents
    foreign_keys: { affected: any:thing, related_process: any:process }
  mill:
    layer: thing
    source: custom                         # synthesized by setup, confirmed by the operator
    identity: name
    directory: Mills
    foreign_keys: { facility: facility }
```

A foreign-key target is a bare type name (`team`) or a layer (`any:thing`).

`source` records provenance: `core` (always present) or `custom` (synthesized for this install). An entity type absent from `entities` simply does not exist in this vault — a paper company's manifest has no `service`, no `repository`.

Because the manifest is self-contained, the vault owns and versions its own schema. There is no central schema to migrate against.

---

## Core entities

Every install gets these. They are the entities every organization has, regardless of domain.

### Person — *Thing*

```yaml
---
type: person
name: alice-okafor
display_name: Alice Okafor
email: alice@customer.com
role: Staff Engineer
team: payments-team                     # FK -> Team
manager: dana-li                        # FK -> Person (no cycles)
---
```

Body: responsibilities, areas of expertise, anything an agent should know before routing work or a question to this person.

### Team — *Thing*

A group of people with a shared remit — team, department, squad, crew.

```yaml
---
type: team
name: payments-team
description: "Owns payments, refunds, billing"
parent_team: ~                          # FK -> Team (org hierarchy)
lead: alice-okafor                      # FK -> Person
members: [alice-okafor, bob-singh]      # FK -> Person
---
```

Body: charter, scope, working hours, how to reach them. A deployment may add custom fields to Team — e.g. an org on Slack adds `slack_channel`.

### Customer — *Thing*

Who the organization serves. Customers, clients, accounts, patients — whatever the domain calls them.

```yaml
---
type: customer
name: northwind-traders
display_name: Northwind Traders
status: active                          # prospect | active | churned
segment: enterprise
owner: alice-okafor                     # FK -> Person (account owner)
products: [checkout]                    # FK -> Product
since: 2025-06-01
---
```

Body: relationship history, key contacts, current health, open concerns.

### Partner — *Thing*

An external organization the org works with that is not a customer — a supplier, vendor, reseller, contractor, or collaborator. The `relationship` field records which.

```yaml
---
type: partner
name: kerchanshe-trading
display_name: Kerchanshe Trading
relationship: supplier                  # supplier | vendor | reseller | integration | collaborator
status: active                          # prospect | active | dormant | ended
owner: alice-okafor                     # FK -> Person (relationship owner)
since: 2025-02-01
---
```

Body: relationship history, key contacts, terms, current state.

### Project — *Thing*

In-flight work — an initiative with a goal and an end. A Thing, not an Event: it is a living entity whose current state (`status`) you track and update, not a point-in-time record.

```yaml
---
type: project
name: fraud-detection-v2
title: Fraud detection v2
status: active                          # proposed | active | paused | shipped | abandoned
priority: p1                            # p0 | p1 | p2 | p3
start_date: 2026-04-01
target_date: 2026-06-30
shipped_date: ~
owner_team: payments-team               # FK -> Team
sponsor: alice-okafor                   # FK -> Person
related_decisions: [2026-03-15-fraud-ml-stack]   # FK -> Decision
related_incidents: [2026-02-04-fraud-bypass]     # FK -> Incident
---
```

Body: goals, scope, milestones, current status, blockers, open questions.

### Product — *Thing*

What the organization provides — a product, an offering, a service line, a program.

```yaml
---
type: product
name: checkout
description: "End-to-end purchase flow"
status: ga                              # proposed | beta | ga | deprecated
owner_team: payments-team               # FK -> Team
owner: alice-okafor                     # FK -> Person (accountable owner)
---
```

Body: what it is, key flows, success measures, current concerns.

### Decision — *Event*

A choice made at a point in time, with rationale. Standard ADR shape.

```yaml
---
type: decision
slug: 2026-03-15-postgres-over-dynamo
title: Use Postgres for the orders table
date: 2026-03-15
status: accepted                        # proposed | accepted | superseded | deprecated
deciders: [alice-okafor, dave-mensah]   # FK -> Person
owner: alice-okafor                     # FK -> Person (accountable owner)
supersedes: ~                           # FK -> Decision
superseded_by: ~                        # FK -> Decision
---
```

Body: `## Context`, `## Decision`, `## Consequences`, `## Alternatives considered`.

### Incident — *Event*

A recorded occurrence of something going wrong, with cause and resolution. Domain-neutral: a software outage, a machine breakdown, a missed deadline. A deployment may add custom fields — a software org adds `fix_pr`, `sentry_project`.

```yaml
---
type: incident
slug: 2026-03-12-payments-outage
title: Payments processing outage
date: 2026-03-12
severity: p1                            # p0 | p1 | p2 | p3
affected: [payment-api]                 # FK -> any:thing (service, product, machine, …)
root_cause: "Connection pool exhaustion under a traffic spike"
resolution: "Pool size raised 20 -> 100"
related_process: payment-api-oom        # FK -> any:process (a runbook, a deployment, …)
authored_by: setup                      # human | setup | <skill>
---
```

Body: timeline, impact, what went well, what could go better, action items.

### Convention — *Knowledge*

A rule or standard the organization complies with — declarative. (Contrast a Process, which is procedural — steps you execute.)

```yaml
---
type: convention
slug: pr-review-policy
title: PR Review Policy
applies_to: [code]                      # domain-defined scope tags
enforcement: required                   # required | recommended | suggested
owner_team: platform-team               # FK -> Team
owner: alice-okafor                     # FK -> Person (accountable owner)
last_reviewed: 2026-04-28
---
```

Body: the actual rules, imperative form.

### Glossary — *Knowledge*

One file at the vault root, all terms inline and alphabetized. Keeps the vault from accumulating hundreds of two-line files.

```markdown
---
type: glossary
last_reviewed: 2026-04-28
---

# Glossary

## A
### ARR
Annual Recurring Revenue. …
```

---

## Custom entities

Everything domain-specific is a custom entity. Core covers the universal nouns; the rest — a software company's `service`, `repository`, and `runbook`; a comms tool's `channel`; a paper mill's `machine`; a law firm's `matter` — setup synthesizes per install from what discovery finds and what the operator confirms. Mindframe ships no pre-baked domain bundles: the agent mints the types this org actually needs, live.

A custom entity is new in its *name and fields*, never in its *structure*: it must obey the meta-schema (a layer, kebab `type`, name/slug identity, FK-by-name, a CATALOG section). A deployment may also add custom fields to a core entity (e.g. `Incident.fix_pr` for a software org, `Team.slack_channel` for an org on Slack) — same provenance, recorded against the core type.

Setup's custom-entity step:

1. **Detect the gap** — a core noun of the business that isn't already a core entity. Discovery surfaces the candidates: probed systems (a GitHub org implies `repository` + `service`), the operator's free-text answer, the interview.
2. **Alias or mint** — first ask whether it is genuinely new or a renamed core entity. "Squad" is just Team; "Matter" may be a richer Project or its own type. Do not over-mint: a renamed core entity is an alias, not a new type.
3. **Define against the meta-schema** — pick the layer, name the `type`, choose fields and FKs, with the operator.
4. **Record** — write it into `schema.yaml` with `source: custom`.

From that point the custom entity is first-class *in that install*: writers validate and write it, the catalog indexes it — because it conforms to the meta-schema.

When a custom entity recurs across many deployments, that is the signal to consider promoting it into the core set in a future schema version.

---

## CATALOG.md

A reading agent reads CATALOG.md first on every query. It has one section per *active* entity type (read from the manifest), encoding the most-queried fields so an agent can filter without opening every note.

```markdown
# Catalog
Last updated: 2026-04-28

## People
| Name | Role | Team |

## Teams
| Name | Lead | Members |

## Services            (custom — only if this deployment defines it)
| Name | Criticality | Team | Repos |

## Runbooks            (custom — only if this deployment defines it)
| Slug | Service | Symptom |

## Incidents (last 90 days)
| Slug | Severity | Affected | Date |
```

Sections exist only for entity types the manifest declares. Events (Incidents, and shipped Projects' archival rows) roll out of the catalog after 90 days; the full notes remain on disk.

## Directory layout

Flat-by-type. Each entity type's `directory` comes from the manifest:

```
<customer-vault>/
  schema.yaml                # the assembled schema for this deployment
  CATALOG.md
  CLAUDE.md                  # operating procedures for vault writers
  Glossary.md

  People/        Teams/        Customers/     Partners/      Projects/      Products/
  Decisions/     Incidents/
  Conventions/
  Runbooks/      Deployments/  CodeReviews/                  # custom (a software org)
  Channels/                                                  # custom (an org on Slack)
  Mills/         Machines/                                   # custom (a paper mill)
```

A vault only has the directories for entity types its manifest declares.

## Schema invariants

These hold for every vault, driven by its `schema.yaml` — never a hardcoded list.

- **Foreign-key integrity.** Every FK value resolves to an existing note of the declared target type or layer. No self-reference cycles.
- **Catalog integrity.** Every CATALOG row points to a real note; every note (except Glossary) has a row; row fields match the note's frontmatter.
- **Identity integrity.** Names/slugs unique per type; filename matches `name`/`slug`; Event slugs start with `YYYY-MM-DD-`.
- **Manifest conformance.** Every note's `type` is declared in `schema.yaml`; every note's frontmatter keys are within that type's defined fields + FKs.

Validation has two homes:

- **Runtime — the writer.** The vault is written by setup's bootstrap and by mindframe agents as they work; each checks its notes against `schema.yaml` before writing, so validation happens at write time. There is no separate curator agent — every writer is responsible for conformance.
- **Development — a regression test (planned).** A fixture-vault test inside mindframe's pytest suite — well-formed and intentionally broken vaults run through the checks — is a tracked follow-up. Until it lands, the invariants are pinned by this document and by the writers, not by an automated suite.

## Bootstrap

Setup populates the vault after it has assembled and written `schema.yaml`. Three passes:

1. **Auto-discovery** — per-source extraction. Each connected source knows how to read its system into entity notes (GitHub org → `repository` + `service`; Slack workspace → `person` + `channel`; …). Stub notes are presented for confirmation.
2. **Manual seeding** — what discovery can't infer: top Products, active Projects, foundational Decisions, Conventions, Glossary terms.
3. **Organic growth** — Events and most Processes start empty; mindframe agents add to them as they work.

## Authoring discipline

The vault is written by setup's bootstrap and by mindframe agents as they work. A writer validates against `schema.yaml`, writes the note, and updates CATALOG.md and bidirectional links. There is no separate curator agent or automated capture loop.

## What is NOT in the vault

| Out of scope | Where it lives |
|--------------|----------------|
| Live alerts, current PRs, deploy status | source systems, queried at runtime |
| Secret values | system keychain, referenced by name |
| Source code, raw documents | their systems; the vault stores metadata |
| Logs, metrics, traces | observability systems, queried at runtime |

## Versioning

- The vault is a plain local directory the customer owns — it holds the current state, with no built-in version history. A customer who wants history can place it under version control themselves; mindframe neither requires nor manages that.
- `schema.yaml` carries `schema_version`. Because each vault owns its schema, there is no central schema to migrate against — a vault evolves its own `schema.yaml`, and writers validate against the new version.
