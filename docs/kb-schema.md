# KB Schema — Customer Knowledge Base

The persistent memory layer for a mindframe deployment: a Markdown + frontmatter Obsidian-style vault, owned by the customer, queried and maintained by the librarian agent.

## What this document is

This document is the **library**, not the contract.

Different organizations have different entities — a software company has Services and Repositories, a paper mill has Machines and Suppliers, a law firm has Matters. No fixed entity list fits all of them. So mindframe's schema has two parts:

- **The meta-schema** — the universal, fixed rules every entity obeys regardless of domain. This *is* the contract, and it never changes without a version bump.
- **The entity library** — core entities (every install gets them), domain packs (opt-in sets), and custom entities (defined per install). This is a menu.

Each deployment assembles its own schema from the library and records it in the vault's **`schema.yaml` manifest** (see "The schema manifest"). The manifest is the contract *for that vault*. The librarian and skills read the manifest — never a hardcoded entity list.

## Design principles

1. **Markdown + frontmatter**, not a database. Greppable, human-editable, diffable in git, renderable in Obsidian.
2. **Frontmatter is the contract.** The body is prose that humans and agents both read.
3. **Foreign keys are names, not paths.** Resilient to reorganization.
4. **CATALOG.md is the index.** Agents read it first; opening notes is the second hop.
5. **Per-customer single-tenant.** One vault, one customer, one git repo.
6. **The librarian is the only writer.** Other agents request changes through the librarian over the session-bridge mesh.
7. **Live state is not in the vault.** Active alerts, current PRs, deploy status: queried from source systems at runtime.
8. **Secrets are referenced, never stored.** Frontmatter holds a keychain entry name; the value never appears in markdown.
9. **The schema is per-install.** The meta-schema is fixed; the entity set is assembled at setup and recorded in `schema.yaml`.

---

## The meta-schema

The fixed, universal contract. Every entity in every deployment — core, pack, or custom — obeys these rules.

### The four layers

Every entity type belongs to exactly one **layer** — a category answering a different question about the organization:

| Layer | Answers | Nature |
|-------|---------|--------|
| **Thing** | what exists | persistent entities you track the current state of |
| **Event** | what happened | immutable, dated records of an occurrence |
| **Knowledge** | what's true / what the rules are | declarative reference |
| **Process** | how the organization operates | the procedures and practices it runs |

A layer is a *category of entity types*, not an entity itself. The Thing layer holds Person, Team, …; the Process layer holds Runbook, Deployment, …. A layer may be entirely populated by pack or custom entity types (the Process layer has no core types).

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

Each vault carries `schema.yaml` at its root — the assembled, self-contained schema for that deployment. Setup generates it; the librarian and skills read it.

```yaml
schema_version: 2
packs: [software-ops, communications]      # packs activated at setup
entities:
  person:
    layer: thing
    source: core                           # core | pack:<name> | custom
    identity: name
    directory: People
    foreign_keys: { team: team, manager: person }
  runbook:
    layer: process
    source: pack:software-ops
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
    source: custom                         # proposed by setup, confirmed by the operator
    identity: name
    directory: Mills
    foreign_keys: { facility: facility }
```

A foreign-key target is a bare type name (`team`) or a layer (`any:thing`).

`source` records provenance: `core` (always present), `pack:<name>` (from an activated pack), or `custom` (defined for this install). An entity type absent from `entities` simply does not exist in this vault — a paper company's manifest has no `service`, no `repository`.

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

Body: charter, scope, working hours, how to reach them. Packs may extend Team — the communications pack adds `slack_channel`.

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

A recorded occurrence of something going wrong, with cause and resolution. Domain-neutral: a software outage, a machine breakdown, a missed deadline. Packs extend it — the software-ops pack adds `fix_pr`, `sentry_project`.

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
authored_by: librarian                  # human | librarian | <skill>
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

## Domain packs

A pack is a named set of entity types for a domain. Setup activates packs based on what discovery finds. A pack may also **extend** a core entity with extra fields.

### `software-ops` pack

Activated when discovery finds GitHub / Sentry / a container runtime / etc.

| Entity | Layer | Role |
|--------|-------|------|
| `service` | Thing | A deployable software unit. FKs: `repos -> repository`, `team -> team`, `products -> product`, `depends_on -> service`. |
| `repository` | Thing | Source code. FKs: `services -> service`, `review_team -> team`. |
| `integration` | Thing | An external system endpoint + auth pointer (`auth_secret_ref` names a keychain entry). FK: `maintainer -> team`. |
| `runbook` | Process | Incident-response procedure. `trigger: symptom`. FKs: `service -> service`, `notify -> team`. Fields: `symptom`, `failure_modes`, `severity_if_unaddressed`. |
| `deployment` | Process | How a team ships. `trigger: manual`. FK: `team -> team`. Fields: `environments`, `rollback`, `approval_required`. |
| `code-review` | Process | `trigger: event`. FK: `team -> team` (or org-wide). |
| `release` | Process | `trigger: schedule`/`manual`. FK: `team -> team`. |

Extends core: `Incident` gains `fix_pr` and `services_affected`; `Service` is the natural target of `Incident.affected`.

### `communications` pack

Activated when discovery finds Slack / Teams / etc.

| Entity | Layer | Role |
|--------|-------|------|
| `channel` | Thing | A conversation venue in a comms tool. Fields: `platform` (slack/teams/…), `purpose`. FKs: `members -> person`, `team -> team`. |

Extends core: `Team` gains `slack_channel`.

Channel is *not* core — it is an artifact of a particular tool, not of an organization. An org on email and phone has no channels.

---

## Custom entities

When a customer has an entity no pack ships — a paper mill's `machine`, a law firm's `matter` — setup defines it for that install. A custom entity is new in its *name and fields*, never in its *structure*: it must obey the meta-schema (a layer, kebab `type`, name/slug identity, FK-by-name, a CATALOG section).

Setup's custom-entity step:

1. **Detect the gap** — a core noun of the business that is neither core nor in a pack.
2. **Alias or mint** — first ask whether it is genuinely new or a renamed core entity. "Squad" is just Team; "Matter" may be a richer Project or its own type. Do not over-mint: a renamed core entity is an alias, not a new type.
3. **Define against the meta-schema** — pick the layer, name the `type`, choose fields and FKs, with the operator.
4. **Record** — write it into `schema.yaml` with `source: custom`.

From that point the custom entity is first-class *in that install*: the librarian validates and writes it, the catalog indexes it — because it conforms to the meta-schema.

When a custom entity recurs across deployments, that is the signal to promote its cluster into a new pack.

---

## CATALOG.md

The librarian reads CATALOG.md first on every query. It has one section per *active* entity type (read from the manifest), encoding the most-queried fields so an agent can filter without opening every note.

```markdown
# Catalog
Last updated: 2026-04-28

## People
| Name | Role | Team |

## Teams
| Name | Lead | Members |

## Services            (only if the software-ops pack is active)
| Name | Criticality | Team | Repos |

## Runbooks            (only if the software-ops pack is active)
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
  CLAUDE.md                  # operating procedures for the librarian
  Glossary.md

  People/        Teams/        Customers/     Partners/      Projects/      Products/
  Decisions/     Incidents/
  Conventions/
  Runbooks/      Deployments/  CodeReviews/                  # software-ops pack
  Channels/                                                  # communications pack
  Mills/         Machines/                                   # custom, this install only
```

A vault only has the directories for entity types its manifest activates.

## Schema invariants

These hold for every vault, driven by its `schema.yaml` — never a hardcoded list.

- **Foreign-key integrity.** Every FK value resolves to an existing note of the declared target type or layer. No self-reference cycles.
- **Catalog integrity.** Every CATALOG row points to a real note; every note (except Glossary) has a row; row fields match the note's frontmatter.
- **Identity integrity.** Names/slugs unique per type; filename matches `name`/`slug`; Event slugs start with `YYYY-MM-DD-`.
- **Manifest conformance.** Every note's `type` is declared in `schema.yaml`; every note's frontmatter keys are within that type's defined fields + FKs.

Validation has two homes:

- **Runtime — the librarian.** The librarian is the vault's sole writer, so it is the only thing that can introduce a violation. It knows these invariants and checks each note against `schema.yaml` before it commits. Because nothing else writes to the vault, that write-time check is the gate — no separate pre-commit hook is needed.
- **Development — a plugin test.** The invariants are codified as a test in the knowledge-base plugin: fixture vaults, well-formed and intentionally broken, run through the checks under `make test`. That is where the rules are pinned down precisely and regression-guarded.

## Bootstrap

Setup populates the vault after it has assembled and written `schema.yaml`. Three passes:

1. **Auto-discovery** — per-source extraction. Each activated pack/source knows how to read its system into entity notes (GitHub org → `repository` + `service`; Slack workspace → `person` + `channel`; …). Stub notes are presented for confirmation.
2. **Manual seeding** — what discovery can't infer: top Products, active Projects, foundational Decisions, Conventions, Glossary terms.
3. **Organic growth** — Events and most Processes start empty; deliverable skills add to them as they run.

## Authoring discipline

The librarian is the sole writer. Other agents send change requests over the session-bridge mesh; the librarian validates against `schema.yaml`, writes the note, updates CATALOG.md and bidirectional links, and commits per change.

## What is NOT in the vault

| Out of scope | Where it lives |
|--------------|----------------|
| Live alerts, current PRs, deploy status | source systems, queried at runtime |
| Secret values | system keychain, referenced by name |
| Source code, raw documents | their systems; the vault stores metadata |
| Logs, metrics, traces | observability systems, queried at runtime |

## Versioning

- The vault is a customer-owned git repository.
- `schema.yaml` carries `schema_version`. Because each vault owns its schema, there is no central schema to migrate against — a vault evolves its own `schema.yaml`, and the librarian validates against the new version.
- The librarian commits per change, small-grained.
