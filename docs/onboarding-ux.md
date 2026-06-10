# Mindframe — Onboarding UX & Surface Model

How first-run setup works, what the operator sees, and the principles that
keep an agent-driven setup empowering instead of bewildering. The
implementation is `setup/install.txt` (the terminal bootstrap) plus
`setup/brief.md` (the setup mindframe's standing brief).

Setup is not special-cased UI. It is the **first mindframe**: a terminal
bootstrap births a setup agent into a frame, and the rest of onboarding
happens inside the Surface, in the same shape every other mindframe uses.

---

## The surface model

A mindframe is a conversation where the agent's replies are full web pages
instead of text:

- The agent **owns one HTML document and rewrites the whole thing in place.**
  The browser is the renderer. No typed-block renderer, no component library,
  no layout DSL.
- The operator has **one message box.** Input is linear (free text);
  presentation is not (the full mutated page).
- The substrate is the **Surface** (`dashboard/`): one server serves every
  mindframe at `/m/<id>`, owns the shell + message rail, and serves the
  agent's `index.html`. The agent rewrites the file; the shell polls a
  revision counter (the file's mtime) and reloads.
- **Human-in-the-loop is rendered, not bypassed.** The agent draws a pending
  act onto the page (what it wants to do, why, the consequence) and waits for
  a message to approve. Anything irreversible or outward-facing is gated this
  way.

**The UI is not the hard part.** Given a minimal prompt (domain only, zero UI
guidance), agents reproduce and beat a hand-built first-run surface. So the
harness is freeform agent-generated UI on the surface substrate — no component
library.

---

## The onboarding arc

The setup mindframe runs five beats, each a surface mutation that advances the
story (full script in `setup/brief.md`):

1. **This is you.** Seed the operator's Person node from their git/gh
   identity; one node, schema and connections as quiet promises.
2. **Interview → schema.** "What do you do?" → the agent derives the entity
   *types* for this deployment and writes `<vault>/schema.yaml`. No data nodes
   yet — shapes, not data.
3. **Discover.** Probe the real machine for what's reachable. Only real,
   discovered connections are shown.
4. **Connect + synthesize.** Pull the strongest reachable source and interpret
   it into schema-valid entities; the graph fills with real data. Scope is
   reported honestly. Data is never fabricated to fill a shape.
5. **First signal.** One real, actionable thing from the connected source —
   the turn from setup into use.

Two reward moments, kept distinct: the schema assembling (interview) and the
graph filling (connect a source).

## Connections

A **connection** is one authenticated way to reach a system — an MCP Claude is
connected to, or a **connector skill** (a `SKILL.md` carrying a `connection:`
fingerprint in `~/.claude/skills/`). It is not "a data source"; data-source is
a role a connection plays when it has read tools.

**Discovery, not a catalog.** Connections are live-discovered from the real
machine, never read from a curated list:

- The Surface's `/api/connections` lists MCPs (`claude mcp list`) plus
  connector skills, minus the bundle's own runtime plugins. **Presence only**
  today — it does not run auth probes or the connectors' `check` commands;
  richer per-connection status is deferred.
- The setup agent additionally probes the operator's environment directly via
  shell as it narrates (`gh auth status`, `claude mcp list`, `git config`),
  per its brief.

**No stored credentials.** Connecting never moves a token into mindframe.
Agents act through the operator's existing CLIs and MCPs (identity
inheritance); if a tool is unauthed, the operator runs that provider's own
login flow.

**Adding one.** `/mindframe:connect <service>` researches the best door in
(MCP / authed CLI / REST API / SQL / browser), authors the connector skill or
registers the MCP, and verifies it. Nothing is pre-shipped — connectors are
authored per operator.

---

## The agent-led principle

Enable non-technical operators by having the agent do the technical work,
without letting them fall into a hole they don't understand.

> The agent owns the mechanics. The operator owns the model. Autonomy is gated
> by **consequence, not complexity**. Everything is reversible and surfaces in
> a model the operator already understands.

1. **Translate, don't expose.** Report at the meaning level ("GitHub's
   connected, read-only, keeps your repos current"), not the mechanics.
   Detail on demand.
2. **Gate on consequence, not complexity.** Act freely on reversible,
   low-consequence steps (install a CLI, read data, add a shape). Stop for
   plain-language consent only on consequential / hard-to-reverse steps
   (write scopes, spend, data leaving the machine, deletes, broad
   permissions).
3. **No orphan actions.** Every technical action must surface as a change in a
   model the operator already holds (schema / connections / graph) and be
   undoable from there.

Corollary: **the agent owns maintenance, not the operator** (expired tokens,
fragile paths). When something breaks, the agent heals it (`/mindframe:doctor`).
