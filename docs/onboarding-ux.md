# Mindframe — Onboarding UX & Surface Model

How first-run setup works, what the operator sees, and the principles that keep
an agent-driven setup empowering instead of bewildering. This is the reference
the interface work (`install.txt`, `schema.yaml` assembly, the dashboard's
connection and vault views) implements against.

Setup is not special-cased UI. It is the **first mindframe**: a terminal
bootstrap births a setup agent into a frame, and the rest of onboarding happens
inside the Surface, in the same shape every other mindframe uses.

---

## The surface model

A mindframe is a conversation where the agent's replies are full web pages
instead of text:

- The agent **owns one HTML document and rewrites the whole thing in place.** The
  browser is the renderer. No typed-block renderer, no component library, no
  layout DSL.
- The operator has **one message box.** Input is linear (free text);
  presentation is not (the full mutated page).
- The substrate is the **Surface** (`dashboard/`): one server serves every
  mindframe at `/m/<id>`, owns the shell + message rail, and serves the agent's
  `index.html`. The agent rewrites the file; the shell polls a revision counter
  and reloads.
- **Human-in-the-loop is rendered, not bypassed.** The agent draws a pending act
  onto the page (what it wants to do, why, the consequence) and waits for a
  message to approve. Anything irreversible or outward-facing is gated this way.

**The UI is not the hard part.** Given a minimal prompt (domain only, zero UI
guidance), independent agents reproduce and beat a hand-built first-run surface,
converging on the same vocabulary (phases, conversation, a connections rail, the
schema, signals, an input) grounded in the real environment. So the harness is
freeform agent-generated UI plus the surface substrate plus live state binding.
Do not build a component library.

---

## The first-run surface

One screen, three zones. The Surface comes up early and the operator opens it to
*continue* setup, so setup happens **inside the product**, not before it.

```
  YOUR SCHEMA            ·  you (the seed node)  ·            CONNECTIONS
  (left rail)               + graph of what's              (right rail)
  the shapes                actually known                 the taps
```

- **Center — the graph.** Seeds on a single `you` (Person) node, derived from
  identity (gh/git config). Everything the KB learns radiates from here. The
  graph only ever contains **real data**; it never shows anything the operator
  hasn't connected.
- **Left — the schema legend.** The entity types in this deployment's
  `schema.yaml`, shown as chips. `pending` = in your schema, no data yet; `lit`
  (with count) = has real entities. Teaches the structure and doubles as a
  progress meter.
- **Right — the connections rail.** Live-discovered systems the machine can
  reach (see below).

## The flow

1. **Identity seeds you.** One node: the operator, from inherited identity.
   "This is you. We don't know much yet."
2. **The interview shapes the SCHEMA, not the graph.** "What do you do?" → the
   agent derives which entity *types* belong. Legend chips appear `pending`. **No
   nodes are written.** The graph stays just `you`.
3. **Connecting a data source fills the graph.** Only a connected source mints
   nodes. Connect GitHub → real Repository nodes bloom, the Repositories chip
   lights. Data is never fabricated to fill a shape.

Two reward moments, kept distinct: the schema assembling (interview) and the
graph filling (connect a source).

## Schema

Per [`kb-schema.md`](kb-schema.md): a fixed meta-schema plus a per-install entity
set recorded in `<vault>/schema.yaml`. The interview *assembles* that entity set.
It is **seeded** (core types) and **grown** (custom types the agent mints from
the interview, `source: custom`). The legend reads `schema.yaml`; no hardcoded
type list. *(The Knowledge layer is under active redesign — see
[`../CLAUDE.md`](../CLAUDE.md).)*

## Connections

A **connection** is one authenticated way to reach a system. It is not "a data
source"; data-source is a *role* a connection plays when it has read tools. An
MCP *or* an authed CLI both qualify.

### Tool roles (one MCP carries several)

Classify each tool, not the MCP. Signal: MCP annotations
(`readOnlyHint`/`destructiveHint`), fallback to verb heuristics or agent
judgment.

| Role | Reads/writes | Destination |
|------|-------------|-------------|
| **ingest** | read persistent state | populates the KB as entities |
| **query** | read live state | answered at runtime, *not stored* |
| **act** | mutates the world | gets things done; can record back to the KB |

A connection's ingest tools make it a data source; its act tools make it a
toolbelt for agents. The KB is the shared spine.

### Discovery, not a catalog

The rail is populated by **live discovery** of the real machine, never a curated
list, served by the Surface's `/api/connections`:

- MCPs: `claude mcp list`, minus the bundle's own runtime.
- CLIs: `gh`/`gcloud`/`aws`/`az` auth probes (inherited identity).

How full the rail is depends entirely on what the operator already has authed. A
working dev machine lights up instantly; a blank laptop starts empty and fills
via guided auth.

### Two paths to a connection

- **Discovery (passive):** finds what's reachable now.
- **Add (active):** the operator declares a system that isn't reachable yet
  ("what about Sentry?"). Evidence for a `wanted` connection: the operator said
  so, or a trace exists (an unauthed CLI, a config reference, a transcript
  mention). Provenance is recorded.

### States

`connected` (discovered, working) · `needs-auth` (discovered, present, unauthed)
· `wanted` (declared, not reachable → guided setup) · `dismissed` (operator
denied a suggestion; recorded so it isn't re-suggested; reversible).

### Connector resolution = research, not a lookup

When the operator names a system, the agent *investigates* how to reach it, using
embedded model knowledge + tools + probing the environment: "a CLI exists,"
"there's a Postgres replica," "no API, drive the web UI." It picks the best door
and records the recipe. A curated catalog is a cache/accelerator for common
systems, not the foundation — this is how "connect M365" works without mindframe
devs predicting every system.

### Capabilities are skills / MCPs / CLIs, not KB records

Skills and MCPs self-inject into an agent at startup; a CLI capability is wrapped
as a skill whose body is the recipe. The knowledge base stores what the org *is*
(entities, history), never the capability registry. There is no `Connection` KB
entity; connections are discovered live via shell.

---

## The agent-led principle

Enable non-technical operators by having the agent do the technical work, without
letting them fall into a hole they don't understand.

> The agent owns the mechanics. The operator owns the model. Autonomy is gated by
> **consequence, not complexity**. Everything is reversible and surfaces in a
> model the operator already understands.

1. **Translate, don't expose.** Report at the meaning level ("GitHub's connected,
   read-only, keeps your repos current"), not the mechanics. Detail on demand.
2. **Gate on consequence, not complexity.** Act freely on reversible,
   low-consequence steps (install a CLI, read data, add a shape). Stop for
   plain-language consent only on consequential / hard-to-reverse steps (write
   scopes, spend, data leaving the machine, deletes, broad permissions).
3. **No orphan actions.** Every technical action must surface as a change in a
   model the operator already holds (schema / connections / graph) and be
   undoable from there. Unsurfaced = a hole. Irreversible = a trap. Forbid both.

Corollary: **the agent owns maintenance, not the operator** (expired tokens,
fragile paths). When something breaks, the agent heals it (the doctor loop).

---

## Open threads

- The "stop and ask" list for rule 2 (the small, namable set of consent gates).
- Scoped/approved investigation for enterprise (locked-down machines, admin
  approval of MCP installs and connection paths). Likely the home of the
  `human-approval` capability.
- Connection state persistence (provenance / path / fidelity / recipe /
  dismissed).
- Relevance curation (discovery finds noise; the agent deprioritizes it).
- On-surface approve/deny buttons (today approval is a message).
