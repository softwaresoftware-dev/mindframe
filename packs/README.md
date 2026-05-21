# Mindframe Packs

A **pack** is a self-contained bundle of customer-domain knowledge: entity types, field extensions to core entities, and the activation evidence that signals when the pack applies. Packs are what let one mindframe install target a software company and an oil & gas company without forking the install flow.

## v0.x: packs ship inside mindframe

Today, packs live as subdirectories here (`mindframe/packs/<name>/`). They ship with the mindframe plugin. The setup skill reads them directly from `${CLAUDE_PLUGIN_ROOT}/packs/` — no separate install step, no GitHub fetch, no marketplace round-trip.

This is a deliberate temporary simplification:
- The customer base is two design partners. No third-party pack authoring yet, so the marketplace + plugin overhead has no payoff.
- The standalone `pack-*` plugin pattern (each pack as its own plugin) required GitHub repos that don't exist yet, creating a chicken-and-egg problem during install.
- Bundling lets the install flow actually run end-to-end on day one.

## v1+: pack-as-plugin

The architectural target is each pack as its own Claude Code plugin (`pack-software-ops`, `pack-microsoft-stack`, …) with its own marketplace entry, GitHub repo, and version. Customers install only the packs they want; third parties author new ones without forking mindframe.

Migrate when external pack authoring becomes real demand. The pack manifest schema in `pack.yaml` is the same in both models, so migration is mostly directory moves + GitHub-repo creation + marketplace entries.

A v1 snapshot of the standalone pattern is preserved at `archive/pack-upstream-oil-gas-standalone-2026-05-21/` for reference.

## Anatomy of a pack

```
packs/<pack-name>/
├── pack.yaml            # manifest — probes, entities, extensions, companions
├── extraction-hints.md  # (optional) per-source recipes for bootstrapping
└── README.md            # (optional) public-facing description
```

`pack.yaml` is the only required file. Look at `software-ops/pack.yaml` for the canonical shape.

## Manifest format

| Section | Purpose |
|---|---|
| `name`, `version`, `description` | identity |
| `activation.evidence` | probe rules — when this pack applies (binary-exists, mcp-registered, file-exists, code-root-marker, operator-mentions, operator-declares) |
| `entities` | typed entity definitions (layer, identity, directory, fields, foreign_keys) |
| `extends_core` | extra fields this pack adds to core entity types |
| `companions.perception_mcps` | recommended MCPs the pack's entities are populated from |
| `companions.example_deliverables` | informational — example workflows operators commonly author against this pack's entities |

**Important:** packs ship no skills and no recipes. Operators author their own utilities during `/mindframe:setup` PHASE 7 (guided authoring). The pack tells the system *what kinds of things exist in this domain*; the operator decides what to *do* about them.

## Activation

Packs are activated at setup time and recorded in `<vault>/schema.yaml`:

```yaml
packs: [core, software-ops, communications]
```

The `core` pack (10 universal entity types) is always active; its definitions live in `docs/kb-schema.md`. Other packs require operator confirmation, driven by the evidence probes in PHASE 4.

## Pack status

| Pack | Status | Notes |
|---|---|---|
| `software-ops` | v0.1.0-scaffold | GitHub/cloud/observability for software companies. Finalized with the software-co design partner. |
| `microsoft-stack` | v0.1.0-scaffold | Azure / M365 / Teams / Power Platform. Co-designed with Flywheel. |
| `upstream-oil-gas` | v0.1.0-demo | Wells, leases, meters, freeze-offs. Ships with `extraction-hints.md` for bootstrapping from Ignition / Quorum / FlowCal / production-accounting systems. |
| `projects` | v0.1.0 | Extends core `project` with status/priority/needs. Absorbs the personal-vault project-tracker pattern. |

## Authoring a new pack

1. Create `packs/<pack-name>/pack.yaml`.
2. Declare activation evidence — be conservative; false positives auto-recommend the pack.
3. List entity types with layer / identity / fields / foreign_keys.
4. Add `extends_core` field extensions for core types if the pack needs them.
5. List `companions.example_deliverables` as inspiration — operators author their own utilities.
6. Validate the file parses as YAML and FK targets resolve within the pack or to core entities.
7. The setup skill picks it up on next run; no plugin install needed.

## Open questions

- **Pack source of truth for activation evidence.** Today `pack.yaml` carries `activation.evidence`. When we migrate to pack-as-plugin v1+, should evidence move into `marketplace.json` so candidates can be evaluated without installing first? Or stay in `pack.yaml` and require install-then-prune?
- **Pack versioning.** Each pack has a `version` field but no enforcement. Worth defining a compatibility policy before customers depend on specific pack versions.
- **Cross-pack FKs.** `freeze-off` had a `related_runbook` FK that referenced `runbook` (defined in `software-ops`, not in `upstream-oil-gas`). Removed for now. Decide later whether cross-pack FKs are allowed and how to declare them.
