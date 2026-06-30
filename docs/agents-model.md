# The agent model — concept cleanup

> Status: design / plan (2026-06-29). Supersedes the "watch" vocabulary and the
> dispatcher "adapter" concept at the product surface. Implementation sequenced
> below; nothing here is shipped yet.

## Why

The product surface grew too many overlapping nouns. A user who wants *"something
always watching, acting on my behalf, pulling me in when it matters"* currently
has to learn: **watch** (the standing automation), **agent** (the per-event
worker process), **recipe** + **route** (its plumbing), **adapter** (per-system
poll code), **frame / mindframe** (the output), **connection** (a tool). Six
concepts for one idea.

This collapses it to **five**, each with one meaning:

| Concept | What it is | Was called |
|---|---|---|
| **Connection** | a tool the system can reach — an MCP or a connector skill. The one concept for both sensing and acting. | connection (unchanged) + **adapter** (folded in) |
| **Agent** | a standing automation that watches and acts on your behalf. Spawns a run on a trigger. Its output is always a mindframe. | **watch** |
| **Run** | one execution of an agent — the ephemeral process that does the work and exits. | **agent** (worker sense) / taskpilot task |
| **Event** | what triggers a run: an external thing happening **or** a schedule tick. Cron is just an event. | route trigger + (no schedule existed) |
| **Mindframe** | the live page an agent delivers — verdict, evidence, decisions as buttons. The human-in-the-loop surface. | frame / mindframe (reframed as agent output) |

Removed from the vocabulary entirely: **watch**, **adapter**. Kept as internal
plumbing only (not surfaced to the user): **recipe**, **route**.

## The two key reframes

### 1. Agent is the standing thing; run is the execution

Today "agent" means the transient worker the dispatcher spawns per event, and
"watch" means the standing automation. We swap them: **agent** is the standing
automation a user creates and thinks in; **run** is its per-event execution.
This aligns the product surface with the dispatcher's own `.agent.md` vocabulary
(an "agent" there is already the portable, standing definition).

An agent's output is **always a mindframe** (for now — no configurable
ping-vs-mindframe). The PR-review agent delivers a context page; the meeting-prep
agent delivers the prep. The page *is* the loop-in.

### 2. Adapter folds into connection — "the poll is a scheduled run"

Today ingestion uses hand-written **adapters** (`dispatcher/app/adapters/github.py`):
bespoke poll code per system, run headless in the poller with no agent, so it
can't use the operator's MCPs/skills. That's the only reason "adapter" exists
separately from "connection."

Replace it: **a scheduled run uses a connection to check for new state.** A cheap
agent wakes on a schedule, uses its connection (Gmail MCP, GitHub MCP, a
connector skill) to look, and delivers a mindframe only if warranted. The poll
*is* an agent using a tool.

```
TODAY:  poller ──▶ adapters/github.py (bespoke) ──▶ event ──▶ spawn worker
NEW:    schedule ──▶ run (cheap agent) ──uses──▶ connection (MCP/skill) ──▶ delivers mindframe
```

Consequence: **no more per-system adapters.** "Read my email every morning" needs
no email adapter — it's a scheduled agent whose connection is the Gmail MCP that
already exists. The dispatcher's GitHub poll-adapter may remain as an optional
optimization for high-volume push, but the default door is scheduled-run +
connection.

This is why scheduling is not a side feature: **schedule-as-event is what makes
"connections are the only tool concept" true.** It replaces the poll loop.

## What this maps to in today's code

The rename is almost entirely contained in the **mindframe repo**; dispatcher and
taskpilot need no code changes for the rename itself (confirmed by review).

- A "watch" is a *derived join* over `recipes/<id>/` + `channels.yaml` routes —
  there is no stored watch object. "Agent" stays a derived join for now (smaller
  change); promoting it to a first-class `.agent.md` object is a later option.
- The dashboard already has `/api/runs` and a `"watch-run"` kind — the "run"
  noun is half-present already.
- The dispatcher already calls the standing definition an **agent**
  (`lib/agent_def.py`, `.agent.md` + `.binding.yaml`, with a `trigger:` block) —
  but its **agent+binding → recipe compiler is unimplemented** (validator only).
  Adopting that object as the dashboard's "agent" is net-new work; deferred.

## Build sequencing

### Phase 1 — Rename + reframe (pure refactor, no new infra)

Make the product legible. No behavior change.

- **Dashboard API** (`dashboard/server/server.py`): `/api/watches` → `/api/agents`;
  `list_watches`→`list_agents`, `_watch_runs`→`_agent_runs`,
  `_watch_deliveries`→`_agent_deliveries`, `_move_watch_routes`→`_move_agent_routes`,
  `pause_watch`/`resume_watch`/`open_watch` → `*_agent`; `WATCH_BRIEF`→`AGENT_BRIEF`.
  Frame kind `"watch"` → `"agent"`. `origin.watch` → `origin.agent` **with
  back-compat read** (existing delivered frames keep provenance). `/api/runs`
  stays as-is (already correct).
- **Dashboard UI** (`dashboard/public/main.js`): `drawerWatches`→`drawerAgents`;
  the old `drawerAgents` (which lists runs) → `drawerRuns`; drawer keys/labels in
  `HUB_NODES` + `FOOT`; the footer "everything — frames · watches · …" line; the
  new-watch form → new-agent; the creation prompt text.
- **Docs**: `CLAUDE.md`, `README.md`, `docs/architecture.md`, `docs/interfaces.md`,
  `docs/delivered-frames.md` (the `origin.watch` field + the starter-prompt
  contract), `docs/design-system.md`, `skills/open/SKILL.md`,
  `.claude/rules/single-stack-contract.md`. Drop "adapter" from the surface
  vocabulary; add the five-concept glossary above. Leave generic "AI agent" prose
  alone.

### Phase 2 — Schedule as an event (the cron seam)

Make "every morning / daily check" real. A schedule trigger emits a synthetic
event into the dispatcher, which spawns a run like any other event. Smallest
high-leverage piece; unlocks the connection-as-poller model. (Mechanism TBD:
taskpilot dropped cron in v0.13.0 and the dispatcher has none — likely a tiny
scheduler that POSTs to the dispatcher, or the `scheduling` capability.)

### Phase 3 — Create-an-agent UI

Natural-language → propose an agent (trigger: event **or** schedule · the job ·
which connections · output = mindframe) → approve → write the recipe + route
(+ schedule). Builds on the existing drafting-mindframe creation flow. Works
end-to-end for GitHub-shaped events today; for everything else, rides on Phase 2.

### Phase 4 — Generalize (later, bigger)

Connection-as-poller as the default ingestion path (retire bespoke adapters), and
— if we promote "agent" to the first-class `.agent.md` object — implement the
agent + binding → recipe compiler that `agent_def.py` documents but doesn't yet
provide.

## Open decisions

1. **Schedule mechanism** (Phase 2): standalone scheduler daemon vs the
   `scheduling` capability vs reviving a taskpilot timer.
2. **Agent = derived join or first-class object?** Keep the join (cheap) now;
   adopt `.agent.md` + write the compiler later. Recommended: join now.
3. **How aggressive is the create-an-agent UI** (Phase 3): polish the existing
   text-box → drafting-mindframe flow, vs a guided structured builder.
