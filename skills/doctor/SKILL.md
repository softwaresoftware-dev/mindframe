---
name: doctor
description: Diagnose and heal a mindframe deployment. Walks every plugin in the bundle — agent runtime, knowledge base, event router, dashboard, perception MCPs — checking for missing capabilities, dead daemons, broken config, and schema drift; fixes what is safe to fix and reports the rest with evidence. Use when asked to "run mindframe doctor", "check the mindframe bundle", "is mindframe healthy", "diagnose mindframe", "/mindframe:doctor", or when a deliverable skill or the dispatcher is misbehaving.
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, AskUserQuestion
---

# Mindframe — Doctor

You are mindframe's self-diagnostic skill. The bundle is a set of plugins held together by capabilities (see `docs/interfaces.md` §1). Your job: check every component, name each problem with the literal probe output, heal what is safe to heal, and hand the operator a clear report of what is left.

Mindframe is manifest-first — so is this skill. Walk the bundle's `requires` list; each capability is one check. The evidence rule from `/mindframe:setup` holds here too: **never report a component `ok` without naming the probe that proved it, never report it `broken` without the literal failure output, and never fabricate a probe result.** A check that cannot run is `unknown` — with the reason — not `ok`.

The terminal output is a diagnostic log. Use `[check N/7] <subsystem>...` lines. One line per probe, declarative, present tense. No spinners.

## What "heal" means — two tiers

- **Tier 1 — safe, reversible, no data loss.** Restart a dead daemon, regenerate `CATALOG.md`, `git init` an untracked vault, clear a stale pidfile, fix a malformed `channels.yaml` route against a known-good shape. Apply automatically, then **re-probe** to confirm the fix took. Log it as `healed`.
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

The bundle is `mindframe` plus the providers bound to its required capabilities. From `docs/interfaces.md` §1 the contract is:

| Capability | Default provider | Required? |
|---|---|---|
| `agent-spawning` | taskpilot | yes |
| `session-mesh` | session-bridge | yes |
| `knowledge-base` | knowledge-base provider + the customer vault | yes |
| `event-routing` | dispatcher | yes |
| `status-dashboard` | taskboard | yes |
| `browser-automation` | claude-browser-bridge | yes |
| `notification` | a `notify-*` provider | optional |

Providers are swappable per install — resolve what is *actually* bound, don't assume. Run the softwaresoftware dependency checker for `mindframe` (intent: "check the mindframe plugin's dependencies are satisfied") and read installed plugins from `~/.claude/settings.json` → `enabledPlugins` (or `claude plugin list`).

For each capability, record one row: provider name, installed version, and state — `ok` / `not-installed` / `unknown`. A **required** capability with no provider is a Tier-2 finding; the fix is one command:

```
/softwaresoftware:install mindframe
```

which re-resolves and installs the whole bundle. A missing **optional** `notification` provider is a warning, not a failure — the deliverable skills fall back to writing an artifact file (`docs/interfaces.md` §8).

### [check 3/7] runtime — daemons & agents

The bundle ships a live healthcheck. Run it first; it covers the critical daemons in one pass:

```bash
bash tests/e2e/live/healthcheck.sh
```

It probes `dispatcher-ingress` (`/api/health`), `session-bridge` (port listening), the systemd `--user` units, and `tmux`. Parse its `[ OK ]` / `[FAIL]` / `[warn]` lines into findings.

For each `[FAIL]` daemon, **heal Tier 1**: restart it. Use the `daemon` capability — intent: "restart the `<unit>` daemon" (daemon-manager exposes start/stop/status; systemd `--user` units can be restarted with `systemctl --user restart <unit>`). After a restart, re-run the relevant probe from `healthcheck.sh` and confirm it now passes. If a daemon will not come up, capture the last lines of its log (`journalctl --user -u <unit> -n 30 --no-pager`) into the finding and downgrade to Tier 2.

Then check the agent runtime itself:

- **taskpilot** — list running tasks (intent: "list taskpilot tasks and their status"). A `service`-kind agent in a crash-loop is a finding; report its task id and last log lines. Stale task dirs under `~/.taskpilot/` for tasks that exited cleanly are cosmetic — note, don't heal.
- **session mesh** — three failure modes, three different fixes. Probe in order; stop at the first that triggers.

  1. **Daemon not installed.** No `session-bridge` entry from `daemon_list`, and `curl -sf http://127.0.0.1:8910/health` fails (connection refused). The plugin is enabled but the bundled daemon was never registered with `daemon-manager`. This is the new-install footgun: channel.mjs loads, then silently fails every mesh call. **Tier 2** — the right fix is `/session-bridge:setup`, which creates the daemon venv, registers with `daemon-manager`, and installs autostart. Calling `daemon_start` blind without the venv will crash on the next reboot, so do *not* heal automatically. Report this, point to `/session-bridge:setup`, and stop healing the mesh.
  2. **Daemon installed but not running.** `daemon_list` shows a `session-bridge` row with `running=false`. **Tier 1** — call `daemon_start` (the on-disk config from a prior install is reused; no args needed). Re-probe `/health`; if it still fails, capture the last 30 lines of `~/.claude/daemons/session-bridge.err.log` into the finding and downgrade to Tier 2.
  3. **Daemon running but the mesh is empty when this session is in it.** `/health` returns 200, `daemon_list` shows running, but listing sessions returns nothing or omits this one. The daemon is wedged or this session's channel registration was dropped. **Tier 1** — bounce it: `daemon_stop` then `daemon_start`; channel.mjs's 30s heartbeat will re-register. If the mesh is still empty after one heartbeat cycle, ask the operator to `/reload-plugins`.

  Independent of which branch fires, treat `has_autostart=false` on an otherwise-healthy `session-bridge` row as a **Tier 2 warning** — the mesh works now but will be dead after the next reboot. Fix: `daemon_install_autostart(daemon_name="session-bridge")`. Same treatment for any other bundle daemon (`mindframe-dashboard`, `dispatcher-ingress`) with `has_autostart=false`.

### [check 4/7] knowledge base — vault

Against `VAULT` from check 1 (skip with an `unknown` finding if check 1 was blocked):

- **Git repo.** `git -C "$VAULT" rev-parse --git-dir` must succeed. If the directory exists but is not a repo → Tier 1: `git -C "$VAULT" init`, then re-probe. Report uncommitted changes (`git -C "$VAULT" status --porcelain`) as a warning — the vault should be committed after every write.
- **Schema manifest.** `$VAULT/schema.yaml` must exist and parse as YAML. It is the deployment's contract (`docs/interfaces.md` §5). Missing → Tier 2: the fix is `/mindframe:setup` step 4 (assemble the schema); do not write `schema.yaml` yourself.
- **Catalog.** `$VAULT/CATALOG.md` should exist with one section per entity type declared in `schema.yaml`. Missing or stale (an entity-type directory exists with notes but has no catalog section) → Tier 1: regenerate `CATALOG.md` from the directories the schema declares, commit it, re-probe.
- **Schema drift.** For each entity-type directory under the vault, confirm the type is declared in `schema.yaml`. A directory of notes for an *undeclared* type is drift — Tier 2 finding (writers are expected to validate against the schema; an undeclared type means notes were written bypassing it). Report the directory and note count; don't delete anything.
- **Provider + population.** Confirm the `knowledge-base` provider is installed (it showed up in check 2). If installed but the vault is empty of notes, that's a "setup incomplete" warning, not a break.

### [check 5/7] event router — dispatcher config

The dispatcher daemon health was covered in check 3. Here, validate its **config**, which lives outside the bundle at `~/.dispatcher/` (`DISPATCHER_DIR`):

- **`channels.yaml`** must exist and parse as YAML. Each route needs a `source`; a `spawn:` target needs a `brief:` block (`docs/interfaces.md` §3).
- **Recipe contract.** Run the bundle's shared checker against the live config:

  ```bash
  python3 tests/e2e/recipe_contract.py ~/.dispatcher/channels.yaml ~/.dispatcher/recipes
  ```

  It verifies every `{{placeholder}}` in each `brief.json` is declared in `brief_schema` and every required placeholder is fillable. A non-zero exit is a finding — the checker prints the offending recipe and key.

  Heal tier depends on the failure: a **typo** in a route key or a brief value matching a known-good shape (compare against `tests/e2e/fixtures/channels-good.yaml`) is Tier 1 — fix it with `Edit`, re-run the checker. A **missing recipe directory** or an **unfillable required placeholder** is Tier 2 — report it; the operator owns the routing intent.

- **Audit log.** If `dispatcher-ingress` is healthy, pull a recent error summary (intent: "get the dispatcher event summary by status" — `GET /api/events/summary`). A spike in `failed` / `spawn-failed` / `exception` is a finding worth surfacing even though doctor can't fix the root cause; include the counts and point at `GET /api/events?status=failed` for detail.

### [check 6/7] dashboard

The dashboard is the one app mindframe owns directly (`dashboard/`, served by a FastAPI backend — `docs/interfaces.md` §9).

- If it is meant to be running, probe `GET /api/health` on its port → expect `{ ok, port, agentId, daemons }`. No response → Tier 1: restart its daemon, re-probe.
- Confirm `dashboard/public/` has the static frontend and the backend dependencies are installed (`dashboard/README.md` has the run contract). A dashboard that was never started is a warning, not a break — note it and move on.

### [check 7/7] deliverable skills & perception

- **Deliverable skills.** For each skill under `skills/` (currently `setup`, `doctor`; deliverable skills pending redesign), confirm `SKILL.md` exists and its frontmatter has a non-empty `name` and `description`. A skill whose `name` does not match its directory is a finding — Tier 1 fix with `Edit`.
- **Perception MCPs.** `claude-browser-bridge` plus the adopt-first MCPs (`github`, `sentry`, `gcp-logging`, `grafana`, `slack`). For each, report registered / not-registered from the `mcpServers` keys in settings. These are mostly informational — a deliverable skill degrades gracefully when one is absent (`docs/interfaces.md` §8) — but a `sentry`-triage deployment with no Sentry MCp *and* no Sentry CLI is worth flagging Tier 2.
- **Hermetic tests.** Optionally run `make test` to confirm the plugin's own manifest/contract tests still pass — a quick regression signal. Report pass/fail; don't heal test failures.

### report

Emit a single findings table, ordered most-severe first:

```
mindframe doctor — <deployment_name>

  subsystem            state     finding
  ───────────────────────────────────────────────────────────────
  agent-spawning       healed    dispatcher-ingress was down — restarted, /api/health ok
  knowledge-base       ok        vault git repo, schema.yaml (12 entities), CATALOG.md current
  event-routing        BROKEN    recipe 'calendar-reader': required brief key {{window}} unfilled
  status-dashboard     warn      taskboard installed, dashboard not running (never started)
  notification         warn      no notify-* provider — skills will use file fallback
  ...

  healed:   2   (re-probed, confirmed)
  broken:   1   (needs operator — see below)
  warn:     2
  unknown:  0
```

Then, for every `BROKEN` / Tier-2 row, give the **exact** remedy — the command, the file and edit, or "run `/mindframe:setup` step N". For every `healed` row, state what was wrong and the re-probe that confirmed the fix. End with one line: `RESULT: bundle healthy` only if there are zero `broken` and zero `unknown` rows; otherwise `RESULT: <n> issue(s) need attention`.

## Hard rules

- **Evidence or it didn't happen.** Every `ok` names its probe; every `broken` carries the literal output. No pattern-matched "looks fine."
- **Heal only Tier 1, and only after confirming.** Re-probe after every heal. A heal whose re-probe fails becomes a Tier-2 finding — never loop a restart.
- **Never read or print secrets.** Tokens, keys, credential file *contents* — presence is evidence, values are not. Same rule as `/mindframe:setup` check 2.
- **Don't delete vault data.** Schema drift, stale notes, uncommitted changes — report them; the operator owns the vault.
- **Don't hardcode provider names in remedies.** Use the capability and the resolved provider from check 2. The fix for a missing capability is always `/softwaresoftware:install mindframe`, which re-resolves for the environment.
- **Idempotent.** Running doctor twice on a healthy bundle changes nothing and reports the same all-`ok` table.

## Reference

- `docs/interfaces.md` — subsystem contracts: capability (§1), dispatcher API (§2), channels.yaml (§3), recipe contract (§4), knowledge base (§5), agent runtime (§6), session mesh (§7), notification (§8), dashboard API (§9).
- `docs/kb-schema.md` — the KB meta-schema and the `schema.yaml` manifest format.
- `tests/e2e/live/healthcheck.sh` — the daemon health probe doctor wraps in check 3.
- `tests/e2e/recipe_contract.py` — the recipe-brief contract checker used in check 5.
- `skills/setup/SKILL.md` — the onboarding wizard; doctor points the operator back to it for Tier-2 setup gaps.
