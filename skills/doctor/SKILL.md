---
name: doctor
description: Diagnose and heal a mindframe deployment. Walks every plugin in the bundle — agent runtime, knowledge base, event router, dashboard, perception MCPs — checking for missing capabilities, dead daemons, broken config, and schema drift; fixes what is safe to fix and reports the rest with evidence. Use when asked to "run mindframe doctor", "check the mindframe bundle", "is mindframe healthy", "diagnose mindframe", "/mindframe:doctor", or when a mindframe agent or the dispatcher is misbehaving.
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, AskUserQuestion
---

# Mindframe — Doctor

You are mindframe's self-diagnostic skill. The bundle is a set of plugins held together by capabilities (see `docs/interfaces.md`). Your job: check every component, name each problem with the literal probe output, heal what is safe to heal, and hand the operator a clear report of what is left.

Mindframe is manifest-first — so is this skill. Walk the bundle's `requires` list; each capability is one check. The evidence rule from `/mindframe:setup` holds here too: **never report a component `ok` without naming the probe that proved it, never report it `broken` without the literal failure output, and never fabricate a probe result.** A check that cannot run is `unknown` — with the reason — not `ok`.

The terminal output is a diagnostic log. Use `[check N/7] <subsystem>...` lines. One line per probe, declarative, present tense. No spinners.

## What "heal" means — two tiers

- **Tier 1 — safe, reversible, no data loss.** Restart a dead daemon, regenerate `CATALOG.md`, create the vault directory if missing, clear a stale pidfile, fix a malformed `channels.yaml` route against a known-good shape. Apply automatically, then **re-probe** to confirm the fix took. Log it as `healed`.
- **Tier 2 — installs, credentials, destructive edits, or judgment calls.** Installing a missing plugin, re-running setup, rewriting `schema.yaml`, anything touching a token or secret. Do **not** apply. Report the finding with the exact command or steps, and let the operator decide. For a Tier-2 finding that blocks everything downstream (missing bundle config), use `AskUserQuestion` to offer the fix inline.

If a Tier-1 heal fails or its re-probe still fails, stop healing that subsystem and downgrade the finding to Tier 2 — don't loop.

## Inputs

- No required arguments. `/mindframe:doctor` runs the full sweep.
- Optional: a subsystem name (`runtime`, `vault`, `dispatcher`, `dashboard`, `skills`) to scope the run to one check.

## Flow

### [check 1/7] locate the deployment

Read bundle config from `~/.claude/settings.json` → `pluginConfigs.mindframe.options`:

- `deployment_name` — must be a non-empty string. If missing, that's a Tier 2 finding: `/mindframe:setup` is the proper path on a fresh install.

The vault is **not configurable** — it always lives at `~/.mindframe/vault`. Resolve `VAULT` to that path for the rest of the run. If the directory does not exist, this is the **first finding** and it is blocking — every check that reads the vault will be `unknown` until it is created. That's a fresh-install signal, not a misconfiguration: tell the operator to run `/mindframe:setup`. Do not create or invent a vault elsewhere.

### [check 2/7] inventory the bundle

The bundle is `mindframe` plus the providers bound to its required capabilities. From `docs/interfaces.md` the contract is:

| Capability | Default provider | Required? |
|---|---|---|
| `agent-spawning` | taskpilot | yes |
| `session-mesh` | session-bridge | yes |
| `event-routing` | dispatcher | yes |
| `browser-automation` | claude-browser-bridge | yes |
| `daemon` | daemon-manager | yes |

Two layers mindframe owns directly are **not** resolved capabilities but still
need checking: the **Surface** (the dashboard daemon) and the **Knowledge** vault
at `~/.mindframe/vault`.

Providers are swappable per install — resolve what is *actually* bound, don't assume. Run the softwaresoftware dependency checker for `mindframe` (intent: "check the mindframe plugin's dependencies are satisfied") and read installed plugins from `~/.claude/settings.json` → `enabledPlugins` (or `claude plugin list`).

For each capability, record one row: provider name, installed version, and state — `ok` / `not-installed` / `unknown`. A **required** capability with no provider is a Tier-2 finding; the fix is one command:

```
/softwaresoftware:install mindframe
```

which re-resolves and installs the whole bundle. Notification is **not** a bundle capability — when an agent wants to notify a human and no notification tool is available, it falls back to writing an artifact file (`docs/interfaces.md`); that is the expected path, never a finding.

### [check 3/7] runtime — daemons & agents

Probe the bundle's daemons directly, one HTTP health check each:

```bash
curl -sf -m 5 http://127.0.0.1:8911/api/health   # dispatcher (event-routing)
curl -sf -m 5 http://127.0.0.1:8910/health       # session-bridge (session-mesh)
curl -sf -m 5 http://127.0.0.1:8912/health       # taskpilot (agent-spawning)
```

Also confirm `tmux` is on PATH (`command -v tmux`) — the agent runtime is tmux-backed — and cross-check the daemon registry with the `daemon` capability (intent: "list managed daemons and their status"; daemon-manager exposes list/start/stop/status). A daemon the registry says is running but whose health probe fails is the finding; record the literal curl exit/output.

For each failed probe, **heal Tier 1**: restart it via the `daemon` capability — intent: "restart the `<name>` daemon". After a restart, re-run the same curl and confirm it now passes. If a daemon will not come up, capture the last lines of its stderr log (`~/.claude/daemons/<name>.stderr.log`; on Linux with systemd `--user` units, `journalctl --user -u <unit> -n 30 --no-pager` also works) into the finding and downgrade to Tier 2.

Then check the agent runtime itself:

- **taskpilot** — list running tasks (intent: "list taskpilot tasks and their status"). Every task is one-shot — there are no service-kind agents. A task stuck in `running` with a dead tmux session, or a `crashed` task that backs a live mindframe, is a finding; report its task id and last log lines. Stale task dirs under `~/.taskpilot/` for tasks that exited cleanly are cosmetic — note, don't heal.
- **session mesh** — three failure modes, three different fixes. Probe in order; stop at the first that triggers.

  1. **Daemon not installed.** No `session-bridge` entry from `daemon_list`, and `curl -sf http://127.0.0.1:8910/health` fails (connection refused). The plugin is enabled but the bundled daemon was never registered with `daemon-manager`. This is the new-install footgun: channel.mjs loads, then silently fails every mesh call. **Tier 2** — the right fix is `/session-bridge:setup`, which creates the daemon venv, registers with `daemon-manager`, and installs autostart. Calling `daemon_start` blind without the venv will crash on the next reboot, so do *not* heal automatically. Report this, point to `/session-bridge:setup`, and stop healing the mesh.
  2. **Daemon installed but not running.** `daemon_list` shows a `session-bridge` row with `running=false`. **Tier 1** — call `daemon_start` (the on-disk config from a prior install is reused; no args needed). Re-probe `/health`; if it still fails, capture the last 30 lines of `~/.claude/daemons/session-bridge.stderr.log` into the finding and downgrade to Tier 2.
  3. **Daemon running but the mesh is empty when this session is in it.** `/health` returns 200, `daemon_list` shows running, but listing sessions returns nothing or omits this one. The daemon is wedged or this session's channel registration was dropped. **Tier 1** — bounce it: `daemon_stop` then `daemon_start`; channel.mjs's 30s heartbeat will re-register. If the mesh is still empty after one heartbeat cycle, ask the operator to `/reload-plugins`.

  Independent of which branch fires, treat `has_autostart=false` on an otherwise-healthy `session-bridge` row as a **Tier 2 warning** — the mesh works now but will be dead after the next reboot. Fix: `daemon_install_autostart(daemon_name="session-bridge")`. Same treatment for any other bundle daemon (`mindframe-dashboard`, `dispatcher-ingress`) with `has_autostart=false`.

### [check 4/7] knowledge base — vault

Against `VAULT` from check 1 (skip with an `unknown` finding if check 1 was blocked):

- **Directory.** `$VAULT` must exist as a directory. The vault is a plain local directory (not a git repo) — there are no commit/clean checks. Missing → Tier 2: the fix is `/mindframe:setup`, which creates it.
- **Schema manifest.** `$VAULT/schema.yaml` must exist and parse as YAML. It is the deployment's contract (`docs/interfaces.md`). Missing → Tier 2: the fix is `/mindframe:setup` step 4 (assemble the schema); do not write `schema.yaml` yourself.
- **Catalog.** `$VAULT/CATALOG.md` should exist with one section per entity type declared in `schema.yaml`. Missing or stale (an entity-type directory exists with notes but has no catalog section) → Tier 1: regenerate `CATALOG.md` from the directories the schema declares, then re-probe.
- **Schema drift.** For each entity-type directory under the vault, confirm the type is declared in `schema.yaml`. A directory of notes for an *undeclared* type is drift — Tier 2 finding (writers are expected to validate against the schema; an undeclared type means notes were written bypassing it). Report the directory and note count; don't delete anything.
- **Population.** The vault is owned directly by mindframe (plain files, no external provider). If the directory exists but is empty of notes, that's a "setup incomplete" warning, not a break.

### [check 5/7] event router — dispatcher config

The dispatcher daemon health was covered in check 3. Here, validate its **config**, which lives outside the bundle at `~/.dispatcher/` (`DISPATCHER_DIR`):

- **`channels.yaml`** must exist and parse as YAML (verify with `python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" ~/.dispatcher/channels.yaml`). Each route needs a `source` and a `target`.
- **Recipe contract.** Check it inline against the live config — no shipped checker:
  1. For every route whose `target` is `spawn:<name>`, the directory `~/.dispatcher/recipes/<name>/` must exist and contain a parseable `recipe.yaml`.
  2. For each such pair, every `{{placeholder}}` appearing in the recipe's brief template must be fillable from the route's `brief:` block (or the event payload, per the recipe's own declarations). An undeclared required placeholder is a finding; name the recipe and the key.

  Heal tier depends on the failure: an obvious **typo** in a route key (a `target` naming a recipe that exists under a near-identical name, a misspelled `source`) is Tier 1 — fix it with `Edit`, re-parse to confirm. A **missing recipe directory** or an **unfillable required placeholder** is Tier 2 — report it; the operator owns the routing intent.

- **Audit log.** If `dispatcher-ingress` is healthy, pull a recent error summary (intent: "get the dispatcher event summary by status" — `GET /api/events/summary`). A spike in `failed` / `spawn-failed` / `exception` is a finding worth surfacing even though doctor can't fix the root cause; include the counts and point at `GET /api/events?status=failed` for detail.

### [check 6/7] dashboard

The dashboard is the one app mindframe owns directly (`dashboard/`, served by a FastAPI backend — `docs/interfaces.md`).

- Find its port: the `mindframe-dashboard` daemon's registration (daemon capability), the `PORT` env in its config, or the default `5174`. Probe `GET /api/health` → expect `{ ok, port, dispatcher_url, dispatcher_bearer_present }`. No response → Tier 1: restart the `mindframe-dashboard` daemon, re-probe.
- `dispatcher_bearer_present: false` is a **Tier 2 warning**: agent-page action buttons that go through `/api/dashboard-event` will 503 until a bearer exists at `~/.mindframe/secrets/dispatcher-bearer.token`. Don't generate one yourself — that's a setup step.
- Confirm `dashboard/public/` has the static frontend and the backend dependencies are installed (`dashboard/README.md` has the run contract). A dashboard that was never started is a warning, not a break — note it and move on.

### [check 7/7] skills & perception

- **Skills.** For each skill under `skills/` (currently `setup`, `doctor`, `open`, `connect`), confirm `SKILL.md` exists and its frontmatter has a non-empty `name` and `description`. A skill whose `name` does not match its directory is a finding — Tier 1 fix with `Edit`.
- **Perception.** `claude-browser-bridge` plus whatever MCPs and connector skills the operator has adopted. Report what `/api/connections` (or `claude mcp list` plus a scan of `~/.claude/skills/*/SKILL.md` for `connection:` fingerprints) discovers. These are informational — an agent degrades gracefully when one is absent — but a deployment whose wired event routes depend on a tool that is no longer reachable is worth flagging Tier 2.
- **Hermetic tests.** If this is a dev checkout with pytest available, optionally run `python3 -m pytest dashboard/tests/ tests/e2e_fresh/ -q` as a regression signal. Report pass/fail; don't heal test failures. Skip silently on an installed deployment without pytest.

### report

Emit a single findings table, ordered most-severe first:

```
mindframe doctor — <deployment_name>

  subsystem            state     finding
  ───────────────────────────────────────────────────────────────
  agent-spawning       healed    dispatcher-ingress was down — restarted, /api/health ok
  knowledge (vault)    ok        vault dir, schema.yaml (12 entities), CATALOG.md current
  event-routing        BROKEN    recipe 'calendar-reader': required brief key {{window}} unfilled
  surface (dashboard)  warn      daemon installed, dashboard not running (never started)
  ...

  healed:   2   (re-probed, confirmed)
  broken:   1   (needs operator — see below)
  warn:     1
  unknown:  0
```

Then, for every `BROKEN` / Tier-2 row, give the **exact** remedy — the command, the file and edit, or "run `/mindframe:setup` step N". For every `healed` row, state what was wrong and the re-probe that confirmed the fix. End with one line: `RESULT: bundle healthy` only if there are zero `broken` and zero `unknown` rows; otherwise `RESULT: <n> issue(s) need attention`.

## Hard rules

- **Evidence or it didn't happen.** Every `ok` names its probe; every `broken` carries the literal output. No pattern-matched "looks fine."
- **Heal only Tier 1, and only after confirming.** Re-probe after every heal. A heal whose re-probe fails becomes a Tier-2 finding — never loop a restart.
- **Never read or print secrets.** Tokens, keys, credential file *contents* — presence is evidence, values are not. Same rule as `/mindframe:setup` check 2.
- **Don't delete vault data.** Schema drift, stale notes — report them; the operator owns the vault.
- **Don't hardcode provider names in remedies.** Use the capability and the resolved provider from check 2. The fix for a missing capability is always `/softwaresoftware:install mindframe`, which re-resolves for the environment.
- **Idempotent.** Running doctor twice on a healthy bundle changes nothing and reports the same all-`ok` table.

## Reference

- `docs/interfaces.md` — the contracts between layers: dispatcher API, channels.yaml, the recipe contract, the agent-runtime spawn interface, the mesh tools, and the dashboard API.
- `docs/kb-schema.md` — the KB meta-schema and the `schema.yaml` manifest format.
- `skills/setup/SKILL.md` — the onboarding flow; doctor points the operator back to it for Tier-2 setup gaps.
