---
name: open
description: Open the mindframe portal (or a specific workspace) in the operator's browser. Brings up the single dashboard daemon if it is not running, then navigates the browser. Use when asked to "open up mindframe", "open mindframe", "open the mindframe home", "launch mindframe", "show me mindframe", "go to my mindframe", or "/mindframe:open". Accepts an optional workspace name (e.g. "/mindframe:open crestborne"). For creating or listing workspaces use /mindframe:workspace.
---

# Mindframe — Open

Open mindframe in the operator's browser. One multi-tenant dashboard (the Surface
layer) serves everything on **port 5174**: the workspace **portal** at `/`, each
workspace's home at `/w/<id>/`, and each mindframe at `/w/<id>/m/<frame>`. There
are **no per-workspace daemons or ports** — the dashboard is always
`mindframe-dashboard` on 5174. This skill makes "open up mindframe" a reliable
one-liner: ensure the dashboard is up, then point the browser at it.

If a workspace name was passed (e.g. `/mindframe:open crestborne`), open that
workspace's home; otherwise open the portal that lists every workspace.

Do the steps in order; don't skip the health probe.

## Step 1 — Target URL

```bash
NAME="${1:-}"                       # optional workspace id
if [ -z "$NAME" ]; then URL="http://127.0.0.1:5174/"
else URL="http://127.0.0.1:5174/w/$NAME/"; fi
echo "mindframe url: $URL"
```

## Step 2 — Is it already up?

```bash
curl -fsS -m 3 "http://127.0.0.1:5174/api/health"
```

If that returns `{"ok": true, ...}`, the dashboard is running — skip to **Step 4**.

## Step 3 — Bring the dashboard up

Only if Step 2 failed.

- **If the `mindframe-dashboard` daemon is registered:** start it through an
  available daemon-management tool — starting an already-running daemon is a
  no-op, so this is safe. Then re-probe until it answers:

  ```bash
  for i in $(seq 1 10); do curl -fsS -m 2 "http://127.0.0.1:5174/api/health" && break; sleep 1; done
  ```

- **If it is not registered** (first install): tell the operator to run
  `/mindframe:setup`. Don't improvise a permanent daemon here.

If it never comes up, stop and report the last error plus the daemon log
(`~/.claude/daemons/mindframe-dashboard.stderr.log`) — don't open a dead URL.

## Step 4 — Open the browser

Open `$URL` using an available browser-automation tool (navigate the active tab,
or open a new one). Fall back to the OS opener, then to printing the URL:

```bash
if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
elif command -v open >/dev/null 2>&1; then open "$URL"          # macOS
elif command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$URL"  # WSL
else echo "Open this in your browser: $URL"
fi
```

## What the operator sees

Without a workspace name: the **portal** — a calm list of workspaces, each with a
frame count and an auth badge (`✓ ready` / `sign-in needed`). Click one to enter.
Inside a workspace (`/w/<id>/`): the calm launcher ("What should we work on?")
plus that workspace's frames, agents, runs, knowledge, and connections, and a
`← workspaces` link back to the portal.

Close with one line: the URL and that it is open. Nothing more unless something
failed.
