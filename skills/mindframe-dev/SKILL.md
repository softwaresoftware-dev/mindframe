---
name: mindframe-dev
description: Boot an ephemeral mindframe stack from the LOCAL working-tree source (not the installed plugin) for development. Runs all four daemons (session-bridge, taskpilot, dispatcher, dashboard) against a throwaway, isolated data home on a private port block, with plain background processes and zero contact with the real ~/.mindframe. Use when asked to "run mindframe from source", "start a dev mindframe", "boot the dev stack", "mindframe-dev up/down/status", "test mindframe changes locally", or "map mindframe to a hostname".
---

# mindframe-dev

A development harness for the mindframe stack. It runs the **working-tree
source** of all four daemons — not the `~/.claude/plugins` copies — so edits to
the local repos take effect on the next `up` with no reinstall. Everything
lands in a throwaway `$HOME` (default `~/.mindframe-dev/home`) so it never
touches the operator's real `~/.mindframe`, real vault, or systemd.

The dev home plays the role of **one isolated workspace**: the dashboard's
`MINDFRAME_HOME` and taskpilot's `TASKPILOT_AGENT_HOME` both point at it, so the
full pipeline (and any spawned agent's `$HOME`) resolves inside it. This makes
the harness the natural substrate for the single-infra / workspace-partition
POC: one shared stack, one partition today, N partitions later.

## Running it

The harness is a stdlib-only Python controller. Run it with system `python3`
from this skill's directory:

```bash
python3 "$(dirname SKILL.md)/mindframe_dev.py" <command>
```

(When developing in the repo, that is
`plugins/frameworks/mindframe/skills/mindframe-dev/mindframe_dev.py`.)

### Commands

| Command | What it does |
|---|---|
| `up [--fresh]` | Start the stack. First run builds a cached venv. `--fresh` wipes the dev home first. Idempotent: skips daemons already healthy. |
| `status` | Per-daemon pid + process + health. |
| `logs [name] [--tail N]` | Tail a daemon log (or all). Names: `session-bridge`, `taskpilot`, `dispatcher`, `dispatcher-poller`, `dashboard`. |
| `open` | Print (and try to open) the dashboard URL. |
| `host [name] [--port P] [--remove]` | Map a bare hostname on port 80 to the dashboard via an nginx reverse proxy. Default `mindframe-dev.localhost`. |
| `down [--wipe]` | Stop all daemons. Ports are retained so the next `up` reuses them. `--wipe` also deletes the dev home + logs. |

## What comes up

Four daemons (dispatcher runs as two processes: ingress + poller), from local
source, on a stable private port block:

| Daemon | Default dev port | Source |
|---|---|---|
| session-bridge | 8910 (shared host bridge) | `providers/session-bridge/daemon` |
| dispatcher | 7911 | `providers/dispatcher` |
| taskpilot | 7912 | `providers/taskpilot` |
| dashboard | 7174 | `frameworks/mindframe/dashboard` |

Ports are chosen once and persisted in `~/.mindframe-dev/run/state.json`, so
`down`/`up` cycles keep the same ports (probe uses `SO_REUSEADDR` so a
just-killed port in `TIME_WAIT` still reads as free).

## The hostname (no port to remember)

`host` gives you `http://mindframe-dev.localhost/` with no port:

- `*.localhost` resolves to loopback automatically (systemd-resolved), so there
  is **no `/etc/hosts` edit** and no extra DNS.
- It installs an nginx vhost (`server_name mindframe.localhost`) reverse-proxying
  to the dashboard port, using the standard sites-available/enabled + reload
  flow. On this machine those steps are covered by the nginx NOPASSWD sudo rules.
- `mindframe.localhost` (no `-dev`) is left free for a real / prod deployment.
- For an even shorter bare `http://mindframe-dev/`, add one line to `/etc/hosts`
  (`127.0.0.1 mindframe-dev`) yourself (needs a sudo password) and run
  `host mindframe-dev`.

Re-run `host` after a port change; remove the mapping with `host --remove`.

## Paths + overrides

| Env | Default | Purpose |
|---|---|---|
| `MINDFRAME_DEV_ROOT` | `~/.mindframe-dev` | controller state, cached venv, dev home, logs |
| `MINDFRAME_DEV_PLUGINS_ROOT` | auto (sibling of the mindframe repo) | the `plugins/` dir holding `providers/` |
| `MINDFRAME_DEV_PORT_BASE` | `7910` | base for the sb/disp/tp block |
| `MINDFRAME_DEV_DASH_PORT` | `7174` | dashboard preferred port |

## Notes

- The dev home shares **subscription auth** and **installed plugin code** with
  the real home by symlink (`~/.claude/.credentials.json`, `~/.claude/plugins`,
  `~/.claude/settings.json`), but keeps MCPs, connectors, and the vault local —
  the same isolation model named workspaces use. Spawned agents run with
  `$HOME` = the dev home, so they cannot pollute the real vault.
- This harness is Linux/macOS dev tooling (taskpilot needs `tmux`). It is not a
  production install path — for that, use `/mindframe:setup`.
