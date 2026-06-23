---
description: Keep the single-stack runtime contract in sync across files and repos
globs: ["dashboard/server/*.py", "dashboard/public/*", "setup/install.txt", "skills/**/*.md", "skills/mindframe-dev/*.py"]
---

# Single-stack runtime contract (mindframe side)

This file helps encode mindframe's **single-stack** model: ONE shared stack
(session-bridge `:8910` / taskpilot `:8912` / dispatcher `:8911` / dashboard
`:5174`) serving many workspaces, each a *partition* under
`~/.mindframe/workspaces/<id>/`. That contract is **replicated across this repo,
taskpilot, and dispatcher** — change one piece and the others break silently
(agents in the wrong home, events routed wrong, the dashboard 404s, auth prompts).

Before changing any of: the **workspace partition layout**, **auth seeding**,
**daemon env/ports**, the **`/w/<id>/` URL scheme**, **per-task `$HOME`**, or
**dispatcher routing** — read the replication map + sync checklist in
**`docs/single-stack-contract.md`** and update every coupled file.

Within this repo these must move together:
- **Partition layout + auth seed:** `setup/install.txt` §3.1 · `skills/workspace`
  · `skills/mindframe-dev/mindframe_dev.py` (`seed_workspace`) · the dashboard's
  `ws_home`/`frames_root`/`vault_dir`/`list_workspaces`/`_auth_status`
  (`dashboard/server/server.py`).
- **`/w/<id>/` scheme:** `dashboard/server/server.py` (middleware/routes/create-url)
  AND `dashboard/public/{main.js,surface.html,portal.html}` must agree — the
  middleware strips `/w/<id>`; the frontend must send it.
- **Cross-repo:** per-task `$HOME` lives in **taskpilot**; workspace-derived
  routing lives in **dispatcher** — coordinate changes there too.

Reference impl: `skills/mindframe-dev/mindframe_dev.py` boots the stack from
source; `mindframe_dev.py up --fresh` must stay green and `/mindframe:doctor`
must pass after changes.
