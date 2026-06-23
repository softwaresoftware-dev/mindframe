---
name: workspace
description: Manage mindframe workspaces — each a data partition (its own vault, frames, connections, MCPs, skills) served by the one shared stack, NOT its own daemons. Use when asked to "create a mindframe workspace", "list workspaces", "open workspace <name>", "switch to workspace <name>", "delete workspace <name>", or "/mindframe:workspace".
---

# Mindframe — Workspace

A **workspace** is a *data partition* under `~/.mindframe/workspaces/<id>/` — its
own vault, frames, connections, MCPs, and connector skills, with the operator's
Claude subscription login seeded in. It is **not** a separate deployment: the one
shared stack (session-bridge `:8910`, taskpilot `:8912`, dispatcher `:8911`,
dashboard `:5174`) serves every workspace. There are **no per-workspace daemons
and no per-workspace ports**. The dashboard serves each at `/w/<id>/`.

Registry: `~/.mindframe/workspaces.yaml` → `{workspaces: {<id>: {label}}}`.

> This skill encodes the partition layout + auth seeding of the single-stack
> contract. If you change either, keep `setup/install.txt` §3.1,
> `skills/mindframe-dev/mindframe_dev.py` (`seed_workspace`), and the dashboard's
> `_auth_status` in sync — see `docs/single-stack-contract.md`.

---

## `create <name>`

`<name>` must match `^[a-z0-9][a-z0-9-]{0,30}$` (e.g. `personal`, `client-acme`).
If it is already in the registry, report "workspace <name> already exists" and
stop. Otherwise build the partition exactly as the canonical onboarding does
(`setup/install.txt` §3.1):

```bash
NAME="<name>"
WS="$HOME/.mindframe/workspaces/$NAME"

# partition tree
mkdir -p "$WS/.mindframe"/{vault,frames,connections,secrets} \
         "$WS/.mindframe/dispatcher"/{recipes,event-sources} \
         "$WS/.claude/skills"
chmod 700 "$WS/.mindframe/secrets"
printf 'routes: []\npaused_routes: []\n' > "$WS/.mindframe/dispatcher/channels.yaml"

# share subscription auth + plugin code; keep MCPs / skills / vault local
ln -sfn "$HOME/.claude/.credentials.json" "$WS/.claude/.credentials.json"
ln -sfn "$HOME/.claude/plugins"           "$WS/.claude/plugins"

# share OS-level CLI identity (one machine identity) so gh / gcloud / aws / git
# keep working under HOME=$WS; per-workspace difference is MCPs / skills / vault
for id in .gitconfig .npmrc .ssh .config .aws .azure .gnupg; do
  [ -e "$HOME/$id" ] && ln -sfn "$HOME/$id" "$WS/$id"
done

# per-workspace settings.json: carry enablement / hooks but EMPTY mcpServers
# (global MCPs must not leak into the workspace)
python3 - "$HOME/.claude/settings.json" "$WS/.claude/settings.json" <<'PY'
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
d["mcpServers"]={}
json.dump(d,open(sys.argv[2],"w"),indent=2)
PY

# .claude.json: empty mcpServers (isolated) + the auth/onboarding state so a
# spawned agent here is already signed in on the subscription (no per-workspace
# OAuth — hasCompletedOnboarding is the key that skips first-run sign-in)
python3 - "$HOME/.claude.json" "$WS/.claude.json" <<'PY'
import json,sys
try: r=json.load(open(sys.argv[1]))
except Exception: r={}
seed={"mcpServers":{}}
for k in ("enabledPlugins","oauthAccount","userID","hasCompletedOnboarding","lastOnboardingVersion","numStartups"):
    if k in r: seed[k]=r[k]
json.dump(seed,open(sys.argv[2],"w"),indent=2)
PY

# registry row
python3 - "$NAME" <<'PY'
import os,sys
try: import yaml
except ImportError:
    import subprocess; subprocess.run(["pip3","install","--quiet","pyyaml"]); import yaml
p=os.path.expanduser("~/.mindframe/workspaces.yaml")
d=yaml.safe_load(open(p)) if os.path.exists(p) else {}
d=d or {}; d.setdefault("workspaces",{})[sys.argv[1]]={"label":sys.argv[1].replace("-"," ").title()}
yaml.dump(d,open(p,"w"),default_flow_style=False)
PY
```

No daemon registration and no port allocation — the running stack already serves
it. Tell the operator the workspace is live at
`http://127.0.0.1:5174/w/$NAME/` (and listed on the portal at
`http://127.0.0.1:5174/`).

Optionally seed a setup mindframe to guide knowledge-base initialization: create
`$WS/.mindframe/frames/mindframe-setup/` with a `meta.json`, fill the brief
(`sed -e "s#__FRAME_DIR__#...#g" "$MF_ROOT/setup/brief.md"`), and spawn a
long-running agent with **HOME = `$WS`** (per-task home) via an available
agent-spawning tool — then open `/w/$NAME/m/mindframe-setup`.

---

## `list`

Read the registry + partition dirs; show each workspace's label, frame count, and
auth status (`ready` when `.credentials.json` + `oauthAccount` +
`hasCompletedOnboarding` are present — i.e. an agent there can run on the
subscription).

```bash
python3 - <<'PY'
import os, json, glob
try: import yaml
except ImportError:
    import subprocess; subprocess.run(["pip3","install","--quiet","pyyaml"]); import yaml
root=os.path.expanduser("~/.mindframe/workspaces")
reg=os.path.expanduser("~/.mindframe/workspaces.yaml")
labels={k:(v or {}).get("label") for k,v in ((yaml.safe_load(open(reg)) or {}).get("workspaces",{}) if os.path.exists(reg) else {}).items()}
print(f"{'WORKSPACE':<20} {'FRAMES':<7} AUTH")
print("-"*44)
for d in sorted(glob.glob(os.path.join(root,"*"))):
    if not os.path.isdir(d): continue
    wid=os.path.basename(d)
    frames=len([f for f in glob.glob(os.path.join(d,".mindframe/frames/*")) if os.path.isfile(os.path.join(f,"index.html"))])
    auth="no-login"
    if os.path.exists(os.path.join(d,".claude/.credentials.json")):
        try:
            cj=json.load(open(os.path.join(d,".claude.json")))
            auth="ready" if cj.get("oauthAccount") and cj.get("hasCompletedOnboarding") else "no-login"
        except Exception: auth="no-login"
    print(f"{labels.get(wid) or wid:<20} {frames:<7} {auth}")
PY
```

---

## `open [name]`

The dashboard is ONE daemon on port `5174` serving every workspace. Bring it up
if down (via an available daemon-management tool — the daemon is
`mindframe-dashboard`), then open the browser:

- no name → the portal: `http://127.0.0.1:5174/`
- a name → that workspace: `http://127.0.0.1:5174/w/<name>/`

```bash
NAME="${1:-}"
curl -fsS -m 3 http://127.0.0.1:5174/api/health >/dev/null || {
  echo "starting the dashboard daemon (mindframe-dashboard)"   # use the daemon tool
  for i in $(seq 1 10); do curl -fsS -m 2 http://127.0.0.1:5174/api/health >/dev/null && break; sleep 1; done
}
URL="http://127.0.0.1:5174/"; [ -n "$NAME" ] && URL="http://127.0.0.1:5174/w/$NAME/"
```

Open `$URL` with an available browser-automation tool, or fall back to
`xdg-open "$URL"`. (`/mindframe:open [name]` does the same.)

---

## `delete <name>`

Remove the partition and its registry row. **This permanently deletes that
workspace's vault, frames, and connections** — confirm with the operator first.
There are no daemons to stop.

```bash
NAME="$1"
WS="$HOME/.mindframe/workspaces/$NAME"
# after confirmation:
rm -rf "$WS"
python3 - "$NAME" <<'PY'
import os,sys,yaml
p=os.path.expanduser("~/.mindframe/workspaces.yaml")
d=yaml.safe_load(open(p)) if os.path.exists(p) else {}
(d or {}).get("workspaces",{}).pop(sys.argv[1],None)
yaml.dump(d,open(p,"w"),default_flow_style=False)
PY
```

Any agents that were running in that workspace are ordinary taskpilot tasks on
the shared daemon; delete them via the agent-spawning tool if they are still
live.

---

## The isolation model

A workspace is isolated by giving each spawned agent **`HOME` = the partition**
(taskpilot's per-task home). That single override makes every `~` convention
resolve inside the workspace, and the dashboard reads the same paths per request:

| Agent writes to | Resolves to | Read by |
|---|---|---|
| `~/.claude/skills/` | `<WS>/.claude/skills/` | dashboard connections panel |
| `~/.claude.json` `mcpServers` (`claude mcp add`) | `<WS>/.claude.json` | the agent at launch + connections panel |
| `~/.mindframe/connections/` | `<WS>/.mindframe/connections/` | connector skills |
| `~/.mindframe/vault/` | `<WS>/.mindframe/vault/` | dashboard (`/w/<id>/api/vault`) |

**Shared** (symlinks into the real home): subscription auth, plugin code, and
OS-level CLI identity (`.gitconfig`, `.ssh`, `.config`, `.aws`, …) — the agent
acts as the operator via their existing `gh`/`gcloud`/`aws` logins. **Isolated**
(workspace-local): MCP/connection config (`.claude.json` `mcpServers`,
`settings.json`), connector skills, connection tokens, and the vault. A fresh
workspace starts with **zero** connection MCPs.

Not isolated: the mesh (one shared session-bridge on `:8910`; sessions are keyed
by unique task id) and OS-level CLI auth (a workspace can't use a different `gh`
account without a separate login).

## Configuring workspace MCPs

Workspace MCPs live in `<WS>/.claude.json` `mcpServers`. Because an agent's `HOME`
is `<WS>`, the standard tools target it automatically:

```bash
HOME="$WS" claude mcp add my-mcp -- npx -y @my/mcp-server
```

`/mindframe:connect`, run inside a workspace agent, does the same — the MCP
registration lands in `<WS>/.claude.json` and the connector skill + tokens land
under `<WS>/.claude/skills` and `<WS>/.mindframe/connections`, no global leak. The
dashboard's connections panel reads the union of `<WS>/.claude.json` and
`<WS>/.claude/settings.json` `mcpServers`. Agents pick up changes on restart.
