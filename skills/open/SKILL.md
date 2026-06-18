---
name: open
description: Open a mindframe workspace home in the operator's browser. Brings up the dashboard daemon if it is not already running, then navigates the browser to the hub. Use when asked to "open up mindframe", "open mindframe", "open the mindframe home", "launch mindframe", "show me mindframe", "go to my mindframe", or "/mindframe:open". Accepts an optional workspace name to open a named workspace (e.g. "/mindframe:open work"). For creating or listing workspaces use /mindframe:workspace.
---

# Mindframe — Open

Open the mindframe **home** — the hub graph the operator works from — in their
browser. The home is served by the dashboard (the Surface layer): one local
FastAPI server that also hosts every mindframe at `/m/<id>`. This skill makes
"open up mindframe" a reliable one-liner: ensure the dashboard is up, then point
the browser at it.

If a workspace name was passed (e.g. `/mindframe:open work`), open that
workspace instead of the default. Named workspaces run on different ports; the
port is in the workspace registry at `~/.mindframe/workspaces.yaml` and in the
daemon config `~/.claude/daemons/mindframe-dashboard-<name>.json`.

Do the four steps in order. Each is small; do not skip the health probe.

## Step 1 — Find the dashboard port

If no workspace name was given (default workspace), the daemon is named
`mindframe-dashboard`. If a name was given, the daemon is named
`mindframe-dashboard-<name>`.

```bash
NAME="${1:-}"   # workspace name if provided, empty string for default
if [ -z "$NAME" ]; then
  DAEMON_NAME="mindframe-dashboard"
  PORT=5174
else
  DAEMON_NAME="mindframe-dashboard-$NAME"
  # Try the workspace registry first
  PORT=$(python3 - <<'EOF'
import os, sys
name = os.environ.get("WS_NAME", "")
try:
    import yaml
    path = os.path.expanduser("~/.mindframe/workspaces.yaml")
    data = yaml.safe_load(open(path)) if os.path.exists(path) else {}
    ws = (data or {}).get("workspaces", {})
    cfg = ws.get(name)
    if cfg:
        print(cfg.get("dashboard_port", "NOT_FOUND"))
    else:
        print("NOT_FOUND")
except Exception as e:
    print("NOT_FOUND")
EOF
  )
  if [ "$PORT" = "NOT_FOUND" ]; then
    echo "Workspace '$NAME' not found. Run /mindframe:workspace list to see available workspaces."
    exit 1
  fi
fi

# Also try the daemon config as a fallback for the port
CFG="$HOME/.claude/daemons/$DAEMON_NAME.json"
if [ -f "$CFG" ]; then
  P=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('env',{}).get('PORT','') or '')" "$CFG" 2>/dev/null)
  [ -n "$P" ] && PORT="$P"
fi
echo "mindframe dashboard port: $PORT (workspace: ${NAME:-default})"
```

Use `http://127.0.0.1:$PORT/` as the home URL for the rest of this skill.

## Step 2 — Is it already up?

```bash
curl -fsS -m 3 "http://127.0.0.1:$PORT/api/health"
```

If that returns `{"ok": true, ...}`, the dashboard is already running — skip
straight to **Step 4** and open the browser.

## Step 3 — Bring the dashboard up

Only if Step 2 failed.

- **If the daemon is registered** (the config file from Step 1 exists): start it
  through an available daemon-management tool — start the daemon named
  `$DAEMON_NAME`. Starting an already-running daemon is a no-op, so this
  is always safe.
- **If the daemon is *not* registered** (no config file):
  - Default workspace: tell the operator to run `/mindframe:setup`.
  - Named workspace: tell the operator to run `/mindframe:workspace create <name>`.
  Do **not** improvise a permanent daemon here.

Then re-probe health until it answers (give it a few seconds to bind):

```bash
for i in $(seq 1 10); do
  curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" && break
  sleep 1
done
```

If it never comes up, stop and report the last error plus the daemon's log
(`~/.claude/daemons/$DAEMON_NAME.stderr.log`) — do not open a dead URL.

## Step 4 — Open the browser

Open `http://127.0.0.1:$PORT/` in the operator's browser using an available
browser-automation tool (navigate the active tab, or open a new one). The home
is a single-page app; it loads the hub graph on its own once the tab points at
the root URL.

If no browser-automation tool is available, fall back to the OS opener, then to
just printing the URL:

```bash
URL="http://127.0.0.1:$PORT/"
if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
elif command -v open >/dev/null 2>&1; then open "$URL"          # macOS
elif command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$URL"  # WSL
else echo "Open this in your browser: $URL"
fi
```

## What the operator sees

The home is a **node graph**: a central **New** node ringed by five
satellites — Mindframes, Knowledge base, Agents, Connections, and Events.
Mindframes and Knowledge base open a drawer; Agents, Connections, and Events
spawn a domain mindframe; clicking the center starts a new mindframe. Each
satellite shows a live count.

Close with one line: the home URL and that it is open — e.g. "Mindframe is open
at http://127.0.0.1:5174/." Nothing more unless something failed.
