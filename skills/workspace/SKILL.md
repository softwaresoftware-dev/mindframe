---
name: workspace
description: Manage named mindframe workspaces — isolated deployments each with their own vault, MCPs, agents, and daemon stack. Use when asked to "create a new mindframe workspace", "list workspaces", "open workspace <name>", "switch to workspace <name>", "delete workspace <name>", or "/mindframe:workspace".
---

# Mindframe — Workspace

A **workspace** is a fully isolated mindframe deployment: its own vault,
frames, secrets, daemon stack (dashboard + taskpilot + dispatcher), and MCP
set. Each workspace runs on a distinct port block so multiple instances can
coexist on the same machine.

The registry lives at `~/.mindframe/workspaces.yaml`. The "default" workspace
is always the original installation at `~/.mindframe/` — no migration needed.

---

## `create <name>`

Create a new named workspace and start its daemon stack. `<name>` must be
lowercase alphanumeric + hyphens, e.g. `work`, `client-acme`.

### Step 1 — read the registry

```bash
REGISTRY="$HOME/.mindframe/workspaces.yaml"
python3 - <<'EOF'
import os, yaml, sys
path = os.path.expanduser("~/.mindframe/workspaces.yaml")
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = yaml.safe_load(f) or {}
ws = data.get("workspaces", {})

# Find next free port block (each workspace uses 3 consecutive ports starting
# at an offset of +10 from the previous highest block)
used_dashboard = [v.get("dashboard_port", 0) for v in ws.values()]
next_base = 5174 + (max((p - 5174 for p in used_dashboard if p >= 5174), default=-10) + 10 + 10) // 10 * 10
# Simpler: just find next slot of 10
import math
slots = sorted(set((p - 5174) // 10 for p in used_dashboard if p >= 5174), default=-1)
next_slot = (max(slots, default=-1) + 1) if slots else 0
dashboard_port = 5174 + next_slot * 10
taskpilot_port = 8912 + next_slot * 10
dispatcher_port = 8911 + next_slot * 10

print(f"dashboard_port={dashboard_port}")
print(f"taskpilot_port={taskpilot_port}")
print(f"dispatcher_port={dispatcher_port}")
EOF
```

Parse the printed values into shell variables:
```bash
eval "$(python3 - <<'EOF'
import os, sys

try:
    import yaml
except ImportError:
    import subprocess; subprocess.run(["pip3","install","pyyaml","--quiet"])
    import yaml

path = os.path.expanduser("~/.mindframe/workspaces.yaml")
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = yaml.safe_load(f) or {}
ws = data.get("workspaces", {})

used_dashboard = sorted([v.get("dashboard_port", 0) for v in ws.values() if v.get("dashboard_port",0) >= 5174])
next_slot = len(used_dashboard)
print(f"DASHBOARD_PORT={5174 + next_slot * 10}")
print(f"TASKPILOT_PORT={8912 + next_slot * 10}")
print(f"DISPATCHER_PORT={8911 + next_slot * 10}")
EOF
)"
```

### Step 2 — validate name

The workspace name must match `^[a-z0-9][a-z0-9-]{0,30}$`. Check that it is
not already in the registry. If it is, report "workspace <name> already exists"
and stop.

### Step 3 — create directory structure

```bash
WS_DIR="$HOME/.mindframe/workspaces/$NAME"
mkdir -p "$WS_DIR/vault"
mkdir -p "$WS_DIR/frames"
mkdir -p "$WS_DIR/secrets"
mkdir -p "$WS_DIR/taskpilot"
mkdir -p "$WS_DIR/dispatcher/recipes"
chmod 700 "$WS_DIR/secrets"

# Workspace-scoped MCP config — agents running in this workspace pick this up
# as project-scoped settings (frame dirs get a .claude symlink at spawn time).
mkdir -p "$WS_DIR/.claude"
cat > "$WS_DIR/.claude/settings.json" <<'SETTINGS'
{
  "mcpServers": {}
}
SETTINGS

# Empty channels.yaml so the dispatcher starts cleanly
cat > "$WS_DIR/dispatcher/channels.yaml" <<'YAML'
# Workspace dispatcher routing — add event sources and channels here
routes: []
YAML
```

### Step 4 — generate dispatcher bearer token

```bash
TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "$TOKEN" > "$WS_DIR/secrets/dispatcher-bearer.token"
chmod 600 "$WS_DIR/secrets/dispatcher-bearer.token"
```

### Step 5 — register and start daemons

Locate plugin cache paths (same pattern as install.txt):

```bash
TP_ROOT="$(ls -d "$HOME/.claude/plugins/cache/softwaresoftware-plugins/taskpilot/"*"/" 2>/dev/null | sort -V | tail -1)"
DISP_ROOT="$(ls -d "$HOME/.claude/plugins/cache/softwaresoftware-plugins/dispatcher/"*"/" 2>/dev/null | sort -V | tail -1)"
MF_ROOT="$(ls -d "$HOME/.claude/plugins/cache/softwaresoftware-plugins/mindframe/"*"/" 2>/dev/null | sort -V | tail -1)"
```

**Register `taskpilot-$NAME`** via an available daemon-management tool:
```
name:    taskpilot-$NAME
command: uv
args:    ["run", "--directory", "<TP_ROOT>", "python", "daemon.py"]
cwd:     <TP_ROOT>
env:
  TASKPILOT_DAEMON_PORT: "<TASKPILOT_PORT>"
  TASKPILOT_DATA_DIR: "<WS_DIR>/taskpilot"
  SESSION_BRIDGE_URL: "http://127.0.0.1:8910"
kill_mode: process
after: ["session-bridge.service"]
wants: ["session-bridge.service"]
```

**Register `dispatcher-$NAME`** via an available daemon-management tool:
```
name:    dispatcher-$NAME
command: <DISP_ROOT>/.venv/bin/uvicorn  (or ~/.dispatcher/venv/bin/uvicorn if that exists)
args:    ["app.main:app", "--host", "127.0.0.1", "--port", "<DISPATCHER_PORT>"]
cwd:     <DISP_ROOT>
env:
  DISPATCHER_DATA_DIR:        "<WS_DIR>/dispatcher"
  DISPATCHER_INGEST_TOKEN_FILE: "<WS_DIR>/secrets/dispatcher-bearer.token"
  DISPATCHER_CHANNELS_FILE:   "<WS_DIR>/dispatcher/channels.yaml"
  DISPATCHER_RECIPES_DIR:     "<WS_DIR>/dispatcher/recipes"
  SESSION_BRIDGE_URL:         "http://127.0.0.1:8910"
```

To find the right venv for dispatcher, check `<DISP_ROOT>/.venv/bin/uvicorn`
first, then `~/.dispatcher/venv/bin/uvicorn`.

**Register `mindframe-dashboard-$NAME`** via an available daemon-management tool:
```
name:    mindframe-dashboard-$NAME
command: <dashboard_venv>/bin/python3
args:    ["<MF_ROOT>/dashboard/server/server.py"]
cwd:     <MF_ROOT>/dashboard
env:
  PORT:                          "<DASHBOARD_PORT>"
  MINDFRAME_HOME:                "<WS_DIR>"
  MINDFRAME_FRAMES_ROOT:         "<WS_DIR>/frames"
  MINDFRAME_VAULT_DIR:           "<WS_DIR>/vault"
  MINDFRAME_TASKPILOT_DAEMON:    "http://127.0.0.1:<TASKPILOT_PORT>"
  MINDFRAME_DISPATCHER_URL:      "http://127.0.0.1:<DISPATCHER_PORT>"
  MINDFRAME_DISPATCHER_BEARER_FILE: "<WS_DIR>/secrets/dispatcher-bearer.token"
```

For the dashboard venv: check `~/.mindframe/dashboard-venv/bin/python3` —
if the workspace needs its own venv (different Python version, etc.), create
one at `<WS_DIR>/dashboard-venv`. Normally the existing
`~/.mindframe/dashboard-venv` works across all workspaces.

**Start all three daemons** (taskpilot, dispatcher, dashboard) via the daemon
tool. Wait for the dashboard health probe before continuing:

```bash
for i in $(seq 1 15); do
  curl -fsS -m 2 "http://127.0.0.1:$DASHBOARD_PORT/api/health" && break
  sleep 1
done
```

### Step 6 — write registry entry

```bash
python3 - <<EOF
import os, yaml
path = os.path.expanduser("~/.mindframe/workspaces.yaml")
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = yaml.safe_load(f) or {}
ws = data.setdefault("workspaces", {})
ws["$NAME"] = {
    "home": "$WS_DIR",
    "dashboard_port": $DASHBOARD_PORT,
    "taskpilot_port": $TASKPILOT_PORT,
    "dispatcher_port": $DISPATCHER_PORT,
}
with open(path, "w") as f:
    yaml.dump(data, f, default_flow_style=False)
print("registry updated")
EOF
```

### Step 7 — spawn setup mindframe in the new workspace

Optionally open a setup mindframe to guide knowledge-base initialization:

```bash
FRAME_DIR="$WS_DIR/frames/mindframe-setup"
mkdir -p "$FRAME_DIR"
printf '{"id":"mindframe-setup","title":"Setup","task_id":"mindframe-setup","status":"active"}\n' \
  > "$FRAME_DIR/meta.json"

# Link workspace .claude so the setup agent picks up workspace MCPs
ln -sfn "$WS_DIR/.claude" "$FRAME_DIR/.claude"

# Fill in the setup brief
sed -e "s#__FRAME_DIR__#$FRAME_DIR#g" \
    "$MF_ROOT/setup/brief.md" > "$FRAME_DIR/brief.txt"
```

Spawn a long-running agent via an available agent-spawning tool:
```
name:        mindframe-setup
working dir: $FRAME_DIR
brief:       <contents of $FRAME_DIR/brief.txt>
taskpilot:   http://127.0.0.1:<TASKPILOT_PORT>   # use this workspace's daemon
```

Wait for `$FRAME_DIR/index.html` to appear, then open the browser to
`http://127.0.0.1:$DASHBOARD_PORT/m/mindframe-setup`.

---

## `list`

Show all workspaces and their status.

```bash
python3 - <<'EOF'
import os, yaml, urllib.request, urllib.error

path = os.path.expanduser("~/.mindframe/workspaces.yaml")
workspaces = {}
if os.path.exists(path):
    with open(path) as f:
        workspaces = (yaml.safe_load(f) or {}).get("workspaces", {})

rows = []
# Always show default
try:
    r = urllib.request.urlopen("http://127.0.0.1:5174/api/health", timeout=2)
    default_ok = r.status == 200
except Exception:
    default_ok = False
rows.append(("default", 5174, "~/.mindframe", "up" if default_ok else "down"))

for name, cfg in workspaces.items():
    port = cfg.get("dashboard_port", "?")
    home = cfg.get("home", "?")
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
        status = "up"
    except Exception:
        status = "down"
    rows.append((name, port, home, status))

print(f"{'WORKSPACE':<20} {'PORT':<6} {'STATUS':<6} HOME")
print("-" * 70)
for name, port, home, status in rows:
    print(f"{name:<20} {port:<6} {status:<6} {home}")
EOF
```

---

## `open [name]`

Open a workspace's dashboard in the browser. Defaults to `default`.

```bash
NAME="${1:-default}"

if [ "$NAME" = "default" ]; then
  PORT=5174
  # (use /mindframe:open for the default workspace)
else
  PORT=$(python3 -c "
import os, yaml
path = os.path.expanduser('~/.mindframe/workspaces.yaml')
data = yaml.safe_load(open(path)) if os.path.exists(path) else {}
ws = (data or {}).get('workspaces', {})
cfg = ws.get('$NAME')
if cfg: print(cfg['dashboard_port'])
else: print('NOT_FOUND')
")
fi

if [ "$PORT" = "NOT_FOUND" ]; then
  echo "Workspace '$NAME' not found. Run /mindframe:workspace list to see workspaces."
  exit 1
fi

# Health check — bring up if down
curl -fsS -m 3 "http://127.0.0.1:$PORT/api/health" || {
  # Try starting the dashboard daemon
  DAEMON_NAME="mindframe-dashboard"
  [ "$NAME" != "default" ] && DAEMON_NAME="mindframe-dashboard-$NAME"
  # Use available daemon-management tool to start $DAEMON_NAME
  echo "starting dashboard daemon $DAEMON_NAME"
  for i in $(seq 1 10); do
    curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" && break
    sleep 1
  done
}

URL="http://127.0.0.1:$PORT/"
```

Open `$URL` using an available browser-automation tool, or fall back to
`xdg-open "$URL"`.

---

## `delete <name>`

Stop the workspace's daemon stack. Does not remove the vault or frames unless
`--wipe` is passed — data is precious.

```bash
NAME="$1"
# Cannot delete "default"
[ "$NAME" = "default" ] && echo "Cannot delete the default workspace." && exit 1
```

1. Stop daemons (via daemon-management tool): `mindframe-dashboard-$NAME`,
   `taskpilot-$NAME`, `dispatcher-$NAME`.
2. Uninstall autostart for all three.
3. Remove registry entry:
   ```bash
   python3 - <<EOF
   import os, yaml
   path = os.path.expanduser("~/.mindframe/workspaces.yaml")
   data = yaml.safe_load(open(path)) if os.path.exists(path) else {}
   ws = (data or {}).get("workspaces", {})
   ws.pop("$NAME", None)
   with open(path, "w") as f:
       yaml.dump(data, f, default_flow_style=False)
   print("removed from registry")
   EOF
   ```
4. If `--wipe` was passed, also `rm -rf ~/.mindframe/workspaces/$NAME`.
   Otherwise print: "Workspace deleted (data at ~/.mindframe/workspaces/$NAME
   is preserved — rm -rf it yourself if you want it gone)."

---

## Configuring workspace MCPs

Each workspace has a `.claude/settings.json` at `<workspace_home>/.claude/settings.json`.
This file follows the standard Claude Code settings format. Add MCPs here:

```json
{
  "mcpServers": {
    "my-mcp": {
      "command": "npx",
      "args": ["-y", "@my/mcp-server"],
      "env": {}
    }
  }
}
```

Agents spawned in this workspace inherit these MCPs automatically (the frame
dir gets a `.claude` symlink at spawn time). Existing agents pick up changes on
their next restart.

To edit the workspace MCP config directly:

```bash
$EDITOR ~/.mindframe/workspaces/$NAME/.claude/settings.json
```

Or instruct the operator to edit it and restart the affected agents.
