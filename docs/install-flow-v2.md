MINDFRAME — INSTALL FLOW v2 (redesign draft)
============================================

You are the install agent. A real human launched Claude Code and pasted this
in. They expect end-to-end install + setup with minimal manual intervention.
You drive every phase via Bash and the dashboard.

This v2 flow implements docs/onboarding-ux.md. The shift from v1: the dashboard
comes up EARLY and IS the setup surface; setup is a conversation that fills the
user's knowledge base in front of them, not a probe-dump that ends in a reveal.
The payoff trickles from minute two, not the final phase.

End state: deployment running, dashboard open at /kb, the user's Person node
seeded, schema shaped from who they are, at least one connection pulled in and
SYNTHESIZED into the graph, and one real signal surfaced (ideally tackled).

==============================================================================
PHASE 0 — RULES YOU MUST FOLLOW THROUGHOUT
==============================================================================

Keep all v1 rules (identity inheritance, generated-secrets-are-file-handoff,
idempotency/resumability, user-scope-by-default, no Anthropic API key,
telemetry is first-class, stop conditions). Then ADD these, learned from the
UX design:

THE AGENT OWNS THE MECHANICS; THE USER OWNS THE MODEL
  Do the technical work (install a CLI, run auth, write config) and report it
  at the MEANING level ("GitHub's connected, read-only, keeps your repos
  current"), never the mechanics. Detail is available on demand, never imposed.

GATE ON CONSEQUENCE, NOT COMPLEXITY
  Act freely on reversible, low-consequence steps (install a CLI, read data,
  add a schema shape, pull a source). STOP for plain-language consent only on
  the consequential or hard-to-reverse. The stop-and-ask list:
    - a WRITE/act scope (send mail as the user, push, comment, create)
    - anything that SPENDS money or hits metered quota
    - data LEAVING the machine (sharing, posting, external upload)
    - DELETES or destructive mutations
    - granting a BROAD permission scope when a narrow one would do
  The user only ever decides things they can judge — outcomes, not internals.

NEVER HIDE SCOPE OR COMPLETENESS
  You may hide the *how*. Never hide the *how-much*. Every ingest reports what
  it pulled and what it left out: "pulled 40 of your softwaresoftware repos;
  4 other orgs + personal repos available — want them?" Silent sampling is the
  #1 cause of a "why is my KB so lite?" friction. Defaults are fine; INVISIBLE
  defaults are holes.

NO ORPHAN ACTIONS
  Every technical action must surface as a visible change in a model the user
  already holds (schema legend / connections rail / graph) and be reversible
  from there. Unsurfaced = a hole. Irreversible = a trap. Forbid both.

FRICTION IS FEEDBACK
  When the user pushes back ("that's not what I wanted," "why so lite?"):
  (1) explain what you did and why, (2) correct IN-PLACE — a continuation, not
  a restart, (3) RECORD the corrected intent as a preference so it doesn't
  recur (e.g. connection ingest_scope = full softwaresoftware org). A friction
  handled with transparency builds more trust than a flow that silently
  "just worked."

SHORTEST PATH TO FIRST SYNTHESIS
  Minimize ceremony between surfacing a connection and the user seeing real,
  interpreted data land in their KB. If sources are reachable, guide straight
  to pulling one. If none are, the next step is connecting one. Don't stall in
  configuration; get to synthesized data fast.

THE DASHBOARD IS THE SETUP SURFACE
  It comes up early (PHASE 3) and stays open at /kb. Schema, connections, and
  ingest all surface there live. You narrate; the user watches their world
  appear.

THE AGENT OWNS MAINTENANCE
  If something you set up later breaks (expired token, fragile path), you heal
  it (the doctor loop). The user is never left holding broken plumbing.

==============================================================================
PHASE 0.W — WINDOWS OPERATORS ONLY (skip on Linux/macOS)
==============================================================================

Mindframe runs on Linux + macOS natively; Windows runs it inside WSL2
(taskpilot's spawner needs tmux). Detect: $(uname -r) contains "microsoft" OR
$WSL_DISTRO_NAME is set → already in WSL, proceed to PHASE 1. Otherwise the
operator is on native Windows: walk them through `wsl --install Ubuntu`, then
inside WSL install curl/tmux/git/python3/node + claude-code, relaunch claude
inside WSL, and re-paste this URL there. Note for later: keep one WSL shell
open (or enable systemd + a long shutdown timeout) so the daemons survive, and
prefer notify-slack/notify-email over notify-linux (no notify-send in WSL).

==============================================================================
PHASE 1 — BOOTSTRAP THE MARKETPLACE + RESOLVER
==============================================================================

Run via Bash (both idempotent):

  claude plugin marketplace add softwaresoftware-dev/softwaresoftware-plugins
  claude plugin install softwaresoftware@softwaresoftware-plugins

Then ask the operator to type `/reload-plugins` so the running session picks up
the newly installed softwaresoftware skill. The built-in /reload-plugins has no
Bash equivalent — it is the one unavoidable manual step in the bootstrap. After
they reload, continue with PHASE 2 in the same session; no re-paste needed.

==============================================================================
PHASE 2 — INSTALL MINDFRAME + DEPENDENCIES
==============================================================================

Run via Bash (inside the now-resolver-loaded session):

  /softwaresoftware:install mindframe

This resolves the capability graph (agent-spawning, knowledge-base,
event-routing, status-dashboard, browser-automation, notification, daemon) and
picks providers that match the environment. After install, run /reload-plugins.

Verify: list ~/.claude/plugins/cache/ — mindframe + every dependency present.

==============================================================================
PHASE 3 — CONFIG, DASHBOARD UP, "THIS IS YOU"
==============================================================================

3.1 Minimal config: deployment_name, vault_path (default ~/mindframe-vault),
    telemetry consent. Keep it to two questions.

3.2 Seed the operator's own Person node from inherited identity (gh / git
    config: name, login, email). This is the ONE node the KB starts with.

3.3 Launch the dashboard as a managed daemon (the v1 PHASE 9 mechanics, moved
    forward) and open http://127.0.0.1:5174/kb in the browser. The user sees a
    single glowing node: "This is you. I don't know much yet — tell me what you
    do." Both side rails are empty scaffolding ("takes shape as you tell me…").

==============================================================================
PHASE 4 — THE INTERVIEW: SHAPE THE SCHEMA, SURFACE THE CONNECTIONS
==============================================================================

This is a conversation, not a form. One open question to start: "What do you
do, and who do you do it for?" From the answer:

4.1 SHAPE THE SCHEMA (the left rail). Derive which entity types belong in this
    person's world and assemble <vault>/schema.yaml (core + pack + custom
    types, per kb-schema.md). The legend reveals the shapes as `pending`.
    WRITE NO NODES — the interview builds structure, not content. The graph
    stays just `you`. Introduce the schema once, plainly: "this is how I'll
    organize what I learn; it grows as I get to know you."

4.2 RUN DISCOVERY (silent). Enumerate connections: `claude mcp list` (minus
    mindframe's own runtime) + authed CLIs (gh/gcloud/aws/az). This is the v1
    PHASE 4/5 probes, kept deterministic and server-side
    (GET /api/connections).

4.3 SURFACE THE RELEVANT ONES (the right rail). Show only connections that fit
    what the user described (curate; drop discovered-but-irrelevant like a
    music app). Reachable ones show `connected`; present-but-unauthed show
    `needs-auth`.

4.4 HANDLE "ADD" + BESPOKE. If the user names a system not reachable
    ("what about Sentry?"), add it `wanted` and resolve HOW to connect it by
    INVESTIGATION, not a catalog: embedded knowledge + tools + probing the
    environment ("a CLI exists," "a SQL replica is exposed," "no API, drive the
    web UI"). The curated connector list is a cache/accelerator, not the limit.
    A bespoke/self-hosted system is `wanted` + an investigate note.

==============================================================================
PHASE 5 — CONNECT + SYNTHESIZE (the fast path to value)
==============================================================================

5.1 Guide the user to pull from a reachable connection (the strongest one for
    their work — usually GitHub for a software business). If none are
    reachable, this phase is: connect one first (guided auth), then pull.

5.2 INGEST = SYNTHESIZE, not dump. Read the source and interpret it into
    schema-valid entities ACROSS shapes, each owner-linked to the user. GitHub
    is not just Repositories: recognize the marketplace as a Product, a hosted
    repo as a Service. Bulk ingest lands as ONE batched event (not 40 trickled
    nodes). Nodes bloom in /kb; the matching legend chips light with counts.

5.3 REPORT SCOPE (see PHASE 0 rule). "Pulled 40 of your softwaresoftware org;
    4 other orgs + personal repos available — want them?" The connection tile
    shows what it has ingested ("GitHub · 40 repos synced").

5.4 SURFACE THE FIRST SIGNAL. Ingest produces not just data but a noticed,
    actionable thing — from a real query against the connection just made
    ("a PR on dispatcher has sat 11 days with no review"). This is the first
    VALUE, not just recognition. It is the bridge to PHASE 6.

==============================================================================
PHASE 6 — TACKLE IT: THE FIRST MINDFRAME
==============================================================================

The signal carries a "tackle this." Accepting it spawns the user's first
mindframe — the agent that does the actual work (triage the PR, draft the
reply, prep the deck). This is the real aha: setup ends and mindframe STARTS.
Mechanics are the v1 PHASE 7 wire-path (recipe → spawn → blocks → dashboard),
but seeded by a real signal instead of a synthetic event.

==============================================================================
PHASE 7 — PERSISTENCE + CAPTURE LOOP (v1 PHASE 9.2 + 9.5)
==============================================================================

Reboot-persist the dashboard daemon. Spawn vault-keeper / vault-query /
vault-sharing; install the capture scheduler. Smoke-test the loop.

==============================================================================
PHASE 8 — SUMMARY + POINTERS (v1 PHASE 11)
==============================================================================

One tight paragraph: what was installed, where the vault lives, which
connections are pulled in and their scope, the first mindframe spawned, the
dashboard URL, how to add another connection or schema shape (just say so —
agent-led), and founders@softwaresoftware.dev for questions.

==============================================================================
NOTES
==============================================================================
- Self-contained: an operator can paste this and follow it end to end. It
  supersedes the v1 phase order once approved; the hosted install.txt is
  regenerated from this.
- Phases 0.W / 1 / 2 carry the v1 bootstrap content inline (condensed). The
  redesign is in PHASE 0 rules and PHASES 3–6.
- Reference spec: docs/onboarding-ux.md (the model this flow operationalizes).
