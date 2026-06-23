# Single-stack runtime contract

Mindframe runs as **one shared stack** that serves **many workspaces**, where a
workspace is a *data partition*, not a deployment. The runtime "contract" — the
daemons + ports, the daemon env, the workspace partition layout, auth seeding,
the `/w/<id>/` URL scheme, per-task `$HOME`, and dispatcher workspace-derivation —
is **replicated across three separate repos** (`mindframe`, `taskpilot`,
`dispatcher`). Change one encoding of any part and you **must** update the
others, or the system breaks *silently* (agents spawn in the wrong home, events
route to the wrong workspace, the dashboard 404s, auth prompts appear).

This doc is the source of truth for those couplings. Each repo also carries a
glob-scoped rule (`.claude/rules/single-stack-contract.md`) that points here when
you touch a contract-bearing file.

**Executable reference:** `skills/mindframe-dev/mindframe_dev.py` boots the whole
stack from working-tree source and is the runnable spec for ports, daemon env,
and partition seeding. `setup/install.txt` is the canonical production onboarding.

## The contract

- **One daemon each, all reading `~/.mindframe`:** session-bridge `:8910`
  (shared host registry — `channel.mjs` hardcodes 8910), taskpilot `:8912`,
  dispatcher `:8911` (ingress + poller), mindframe-dashboard `:5174`. **No
  per-workspace daemons, no per-workspace ports.**
- **Workspace = partition** at `~/.mindframe/workspaces/<id>/`:
  - `.mindframe/{vault,frames,connections,secrets,dispatcher/{channels.yaml,recipes,event-sources}}`
  - `.claude/{skills, settings.json, .credentials.json, plugins}` + `.claude.json`
  - OS-level identity symlinks (`.gitconfig .npmrc .ssh .config .aws .azure .gnupg`)
  - Registry: `~/.mindframe/workspaces.yaml` → `{workspaces: {<id>: {label}}}`
- **Auth is the operator's one subscription, seeded per partition** so agents run
  with no per-workspace OAuth: symlink `.credentials.json`, and seed `.claude.json`
  with `oauthAccount`, `userID`, `hasCompletedOnboarding`, `lastOnboardingVersion`
  (the **`hasCompletedOnboarding` key is what skips first-run sign-in**) +
  `mcpServers: {}` (isolated) + `enabledPlugins`. `settings.json` carries
  enablement/hooks but **empty `mcpServers`** (global MCPs must not leak in).
- **No `ANTHROPIC_API_KEY`** in any daemon env or spawned agent — it overrides the
  subscription login and breaks agents. Scrubbed at the daemon env *and*
  per-spawn.
- **Dashboard is multi-tenant:** portal at `/`, workspace home at `/w/<id>/`,
  surface at `/w/<id>/m/<frame>`. It derives frames/vault per request from
  `MINDFRAME_HOME/workspaces/<id>/.mindframe/` — never set
  `MINDFRAME_FRAMES_ROOT`/`MINDFRAME_VAULT_DIR`.
- **Per-task `$HOME`:** a taskpilot task carries `home` = its workspace partition;
  the spawner exports that as the agent's `$HOME`. This is what lets one taskpilot
  serve every workspace.
- **Dispatcher derives the workspace from the event source:** the poller
  aggregates event-sources across all partitions (tagging each with its
  workspace), routes via that workspace's `channels.yaml`/recipes, and spawns with
  that workspace's home. Driven by `DISPATCHER_WORKSPACES_ROOT`.

## Replication map — change one, update all

| Aspect | Files that encode it (across repos) |
|---|---|
| **Daemons + ports** (one each) | mindframe `setup/install.txt` §3.0/3.6 · `skills/mindframe-dev/mindframe_dev.py` (`daemon_specs`, port alloc — note sb is the shared 8910) · `skills/open`, `skills/doctor` · docs `CLAUDE.md`, `docs/architecture.md` |
| **Partition layout** | mindframe `setup/install.txt` §3.1 · `skills/workspace` (create) · `skills/mindframe-dev/mindframe_dev.py` (`seed_workspace`) · `dashboard/server/server.py` (`ws_home`/`frames_root`/`vault_dir`/`list_workspaces`) |
| **Auth seeding** | mindframe `setup/install.txt` §3.1 · `skills/workspace` · `mindframe_dev.py` (`seed_workspace`) · `dashboard/server/server.py` (`_auth_status` probe reads the same files) |
| **`ANTHROPIC_API_KEY` scrub** | taskpilot `spawner.py` (per-spawn `unset`) · mindframe `mindframe_dev.py` (daemon env) + `setup/install.txt` §3.6 · `dashboard/server/server.py` (`_auth_status` api-key-conflict) |
| **`/w/<id>/` URL scheme** | mindframe `dashboard/server/server.py` (`WorkspaceMiddleware`, routes, create-url) · `dashboard/public/{main.js,surface.html,portal.html}` (BASE prefixing) · `skills/open` · `setup/install.txt` §3.7 |
| **Per-task `$HOME`** | taskpilot `daemon.py` (`TaskDefinition.home`, `_upsert`, `_start`) + `store.py` (column + migration) + `spawner.py` (`export HOME`) · mindframe `dashboard/server/server.py` (create passes `home=fdir.parents[2]`) · dispatcher `spawn_helper.py` (`home=`) + `core.py` (`_ws_paths`) |
| **Dispatcher workspace-derivation** | dispatcher `event_sources.py` (`load_all_sources`, `workspace` tag) + `poller.py` (cursor key, `workspace=`) + `core.py` (`route_event(workspace=)`, `_ws_paths`) + `channels.py` (`channels_file=`) + `db.py` (`workspace` column) · `DISPATCHER_WORKSPACES_ROOT` set in mindframe `setup/install.txt` §3.6a + `mindframe_dev.py` (`disp_env`) · partition's `.mindframe/dispatcher/` dir |

## Sync checklist

When you change one of these, walk the matching row above and update every file:

- Touch the **partition layout** (a new dir, a renamed path)? Update install.txt,
  the workspace skill, `seed_workspace`, and the dashboard path resolvers — and
  `_auth_status` if you moved an auth file.
- Change **how a workspace authenticates**? Update the seed in all three places
  (install.txt, workspace skill, harness) *and* the dashboard `_auth_status`
  probe that reads it.
- Add/rename a **daemon or env var**? Update install.txt, the harness
  `daemon_specs`/`disp_env`, and `skills/doctor`'s health checks.
- Change the **URL scheme**? The server middleware/routes and *all* of
  `public/` must agree (the middleware strips `/w/<id>`; the frontend must send
  it). Update `skills/open` and install.txt's open step too.
- Change **per-task home** or **dispatcher routing**? These cross repos —
  taskpilot's `home` field, the dashboard/dispatcher callers that pass it, and the
  dispatcher's workspace plumbing must move together.

After any change: `skills/mindframe-dev` `up --fresh` should boot the stack green,
and `/mindframe:doctor` should pass. The three repos ship from `origin/main`
independently, so land coupled changes together (or guard for version skew).
