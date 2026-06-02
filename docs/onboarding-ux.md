# Mindframe — Onboarding UX & State Model

The design for first-run setup: what the user sees, how the schema and
connections come to life, and the principles that keep an agent-driven setup
empowering instead of bewildering. This is the reference the interface work
(install.txt, schema.yaml assembly, dashboard `/kb` view, connection state)
implements against.

Status: design agreed, wiring in progress. Prototype lives (throwaway) at
`dashboard/artifacts/kb-live/` driven by `setup-*` JSON files + the live
`/api/vaults/<name>/graph` endpoint.

## The first-run surface

One screen, three zones. The dashboard comes up early and the user opens it to
*continue* setup, so setup happens **inside the product**, not before it.

```
  YOUR SCHEMA            ·  you (the seed node)  ·            CONNECTIONS
  (left rail)               + graph of what's              (right rail)
  the shapes                actually known                 the taps
```

- **Center — the graph.** Seeds on a single `you` (Person) node, derived from
  identity (gh/git config). Everything the KB learns radiates from here
  (the `owner: person` hub-and-spoke from `kb-schema.md`). The graph only ever
  contains **real data**; it never shows anything the user hasn't connected.
- **Left — the schema legend.** The entity types in this deployment's
  `schema.yaml`, shown as chips. `pending` = in your schema, no data yet;
  `lit` (with count) = has real entities. Teaches the structure and doubles as
  a progress meter.
- **Right — the connections rail.** Live-discovered systems the machine can
  reach (see below).

## The flow

1. **Identity seeds you.** One node: the operator, from inherited identity.
   "This is you. We don't know much yet."
2. **The interview shapes the SCHEMA, not the graph.** "What do you do?" →
   the agent derives which entity *types* belong (a software business gets
   Repositories, Services, Products, Projects, Customers). Legend chips appear
   `pending`. **No nodes are written.** The graph stays just `you`.
3. **Connecting a data source fills the graph.** Only a connected source mints
   nodes. Connect GitHub → real Repository nodes bloom, the Repositories chip
   lights. Data is never fabricated to fill a shape.

Two reward moments, kept distinct: the schema assembling (interview) and the
graph filling (connect a source).

## Schema

Per `kb-schema.md`: a fixed meta-schema + a per-install entity set recorded in
`<vault>/schema.yaml`. The interview *assembles* that entity set. The schema is
**seeded** (core types) and **grown** (pack types + custom types the agent
mints from the interview, `source: custom`). The legend reads `schema.yaml`;
no hardcoded type list.

## Connections

A **connection** is the first-class primitive — one authenticated way to reach
a system. It is **not** "a data source"; data-source is a *role* a connection
plays when it has read tools. An MCP *or* an authed CLI both qualify.

### Tool roles (one MCP carries several)

Classify each tool, not the MCP. Signal: MCP annotations
(`readOnlyHint`/`destructiveHint`), fallback to verb heuristics or agent judgment.

| Role | Reads/writes | Destination |
|------|-------------|-------------|
| **ingest** | read persistent state | populates the KB as entities |
| **query** | read live state | answered at runtime, *not stored* (kb-schema principle #7) |
| **act** | mutates the world | gets things done; can record back to the KB |

A connection's ingest tools make it a data source; its act tools make it a
toolbelt for agents. The KB is the shared spine.

### Where the rail comes from: discovery, not a catalog

Populated by **live discovery** of the real machine, never a curated list:

- MCPs: `claude mcp list`, minus mindframe's own runtime (the bundle `requires`:
  taskpilot, tmux-session, daemon-manager, session-bridge, mindframe,
  softwaresoftware, tokenboard, claude-browser-bridge, email-triage).
- CLIs: `gh`/`gcloud`/`aws`/`az` auth probes (inherited identity).

How full the rail is depends entirely on what the user already has authed.
A working dev machine lights up instantly ("it already knows me"); a blank
laptop starts empty and fills via guided auth.

Implemented server-side: `GET /api/connections` (replaces the hardcoded
`KNOWN_SOURCES`/`/api/sources`).

### Two paths to a connection

- **Discovery (passive):** finds what's reachable now.
- **Add (active):** the user declares a system that isn't reachable yet
  ("what about Sentry?"). Evidence for a `wanted` connection: the user said so,
  OR a trace exists (an unauthed CLI, a config reference, a transcript mention).
  Provenance is recorded.

### States

`connected` (discovered, working) · `needs-auth` (discovered, present,
unauthed) · `wanted` (declared, not reachable → guided setup) · `dismissed`
(user denied a suggestion; recorded so it isn't re-suggested; reversible).

Each connection record carries: **provenance** (why it's here), **state**,
**path** (CLI / MCP / API / database / browser / file), **fidelity** (a SQL
read is ground truth; a browser scrape is best-effort), and the **recipe**
(how to reach it).

### Connector resolution = research, not a lookup

When the user names a system, the agent *investigates* how to reach it, using
embedded model knowledge + tools + probing the environment: "a CLI exists,"
"there's a Postgres replica," "no API, drive the web UI." It picks the best
door and records the recipe. The curated catalog is a **cache/accelerator** for
common systems, not the foundation — this is how "connect M365" works without
mindframe devs predicting every system. Connecting a novel system mints a
reusable recipe that can be shared back (community-grown library).

## The agent-led principle (the middle ground)

Enable non-technical users by having the agent do the technical work, without
letting them fall into a hole they don't understand.

> The agent owns the mechanics. The user owns the model. Autonomy is gated by
> **consequence, not complexity**. Everything is reversible and surfaces in a
> model the user already understands.

1. **Translate, don't expose.** Report at the meaning level ("GitHub's
   connected, read-only, keeps your repos current"), not the mechanics. Detail
   available on demand (progressive disclosure).
2. **Gate on consequence, not complexity.** Act freely on reversible,
   low-consequence steps (install a CLI, read data, add a shape). Stop for
   plain-language consent only on consequential / hard-to-reverse steps (write
   scopes, spend, data leaving the machine, deletes, broad permissions). The
   user only ever decides things they can actually judge — outcomes, not internals.
3. **No orphan actions.** Every technical action must surface as a change in a
   model the user already holds (schema / connections / graph) and be undoable
   from there. Unsurfaced = a hole. Irreversible = a trap. Forbid both.

Corollary: **the agent owns maintenance, not the user** (expired tokens,
fragile paths). When something breaks, the agent heals it (the doctor loop).
The user is never left holding broken plumbing.

## Open threads

- The "stop and ask" list for rule 2 (the small, namable set of consent gates).
- Scoped/approved investigation for enterprise (locked-down machines, admin
  approval of MCP installs and connection paths) — where "agent finds any door"
  meets real walls. Likely the home of the `human-approval` capability.
- Connection state persistence (provenance/path/fidelity/recipe/dismissed).
- Relevance curation layer (discovery finds Beats; the agent deprioritizes it).

## What we built and learned next (2026-06-02)

The sections above were written mid-design. These are the decisions and findings
that followed, and they supersede anything above that conflicts.

### A mindframe is a spatial surface, not a linear conversation

The append-only block-stream (`docs/mindframe-block-stream-api.md`) is a **failed
default** — appending blocks forces a chat feel. The right model: **input is
linear, presentation is not.** A mindframe is an agent-composed **spatial**
surface (graph centerpiece, rails, signal cards, an input) that the agent
**mutates in place**, not a transcript you scroll. The block-stream stays as
fallback plumbing only.

### Setup is a mindframe

The onboarding flow is not special-cased UI — it is the **first mindframe**. The
schema rail, connections rail, and knowledge-graph are reusable components any
mindframe composes. `install-flow-v2.md` is that mindframe's recipe.

### The intent primitive (how interaction works)

- The **agent is a durable JSON transcript reached by id**, not a running
  process. It's resumed per interaction; its transcript is its memory of what it
  rendered.
- A UI element carries **only an element id** (+ optional runtime context like a
  row). The resumed agent resolves meaning from its own history. No `data-context`
  restating what the agent knows; a human-readable label is fine.
- One intent channel; every element auto-wires to it (so **dead buttons are
  impossible by construction**). Render states: **idle → working →
  awaiting-approval → settled.**
- The `awaiting-approval` state is the human-in-the-loop gate as a first-class,
  on-surface object (see `install-flow-v2.md` PHASE 0 — taught, present,
  away-path, editable). The same channel carries clicks down and approval
  requests up.
- Design pressure moves from "keep agents alive" to **"keep resume cheap"**:
  don't store rendered markup in the transcript; keep histories lean; warm-cache.

### Generative-UI finding — the UI is no longer the hard part

Given a minimal prompt (domain only, zero UI guidance), independent agents
**reproduced and beat** the hand-built first-run surface, converging on the same
vocabulary (phases, conversation, connections rail, schema, signals, input) plus
extras, grounded in the real environment. Conclusion: **do not build a component
library or layout DSL.** The minimal harness is freeform agent-generated UI + the
intent primitive + live state binding.

### Proven on real infrastructure, and the one real blocker

A real Claude agent (taskpilot, subscription, reached by **task id**, resumed per
message) interpreted element-id clicks from its own brief, ran real `gh` (pulled
40 repos), surfaced an honest auth error and self-corrected, and the surface
reflected it live with a consequence-gated approval state. The keystone works.

The blocker is **delivery transport, not the concept**: taskpilot delivers
messages by typing keystrokes into the Claude TUI in tmux, and submits drop
intermittently (plus a missing `taskpilot/hooks/on-prompt.py` — likely install
drift, and spawned agents have a sandboxed `$HOME` so `gh` needs
`GH_CONFIG_DIR`). A production mindframe needs a **reliable resume channel**
(queue/API), not keystroke injection. Prototypes (local, **not committed**):
`slice/` (incl. `slice/live`, the real-agent run) and
`dashboard/artifacts/{kb-live,genui-1,2,3}` (the latter under gitignored `artifacts/`).
