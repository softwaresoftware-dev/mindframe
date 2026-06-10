---
name: open
description: Open the mindframe home in the operator's browser. Brings up the dashboard daemon if it is not already running, then navigates the browser to the hub. Use when asked to "open up mindframe", "open mindframe", "open the mindframe home", "launch mindframe", "show me mindframe", "go to my mindframe", or "/mindframe:open".
---

# Mindframe — Open

Open the mindframe **home** — the hub graph the operator works from — in their
browser. The home is served by the dashboard (the Surface layer): one local
FastAPI server that also hosts every mindframe at `/m/<id>`. This skill makes
"open up mindframe" a reliable one-liner: ensure the dashboard is up, then point
the browser at it.

Do the four steps in order. Each is small; do not skip the health probe.

## Step 1 — Find the dashboard port

The dashboard runs under the `daemon` capability as the daemon named
`mindframe-dashboard`. Its port lives in that daemon's saved config. Read it,
defaulting to `5174`:

```bash
CFG="$HOME/.claude/daemons/mindframe-dashboard.json"
PORT=5174
if [ -f "$CFG" ]; then
  P=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('env',{}).get('PORT','') or '')" "$CFG" 2>/dev/null)
  [ -n "$P" ] && PORT="$P"
fi
echo "mindframe dashboard port: $PORT"
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
  `mindframe-dashboard`. Starting an already-running daemon is a no-op, so this
  is always safe.
- **If the daemon is *not* registered** (no config file — mindframe was never
  set up on this machine): the home does not exist yet. Tell the operator to run
  `/mindframe:setup` first, which installs the bundle and registers the
  dashboard. Do **not** improvise a permanent daemon here. (If they only want a
  quick look, you may start the server in the foreground from
  `${CLAUDE_PLUGIN_ROOT}/dashboard/server/server.py` with `PORT` set, but say
  plainly that it will not survive a reboot until setup registers it.)

Then re-probe health until it answers (give it a few seconds to bind):

```bash
for i in $(seq 1 10); do
  curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" && break
  sleep 1
done
```

If it never comes up, stop and report the last error plus the daemon's log
(`~/.claude/daemons/mindframe-dashboard.stderr.log`) — do not open a dead URL.

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
