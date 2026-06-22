#!/usr/bin/env python3
"""mindframe-dev — boot an ephemeral mindframe stack from local source.

Runs the full four-daemon stack (session-bridge, taskpilot, dispatcher,
dashboard) directly from the working-tree repos — NOT the installed
~/.claude/plugins copies — against a throwaway data home on a private port
block. Plain background processes, no systemd, no contact with the real
~/.mindframe. Teardown is a kill.

The dev home plays the role of one isolated workspace: the dashboard's
MINDFRAME_HOME and taskpilot's TASKPILOT_AGENT_HOME both point at it, so the
whole pipeline runs against an isolated vault/frames/connections tree and any
spawned agent's $HOME resolves there too (never the real home).

Controller uses only the Python stdlib — run it with system python3. It
bootstraps a cached venv for the daemons on first `up`.

Usage:
    python3 mindframe_dev.py up [--fresh]
    python3 mindframe_dev.py status
    python3 mindframe_dev.py logs [name] [--tail N]
    python3 mindframe_dev.py open
    python3 mindframe_dev.py down [--wipe]

Path overrides (env):
    MINDFRAME_DEV_ROOT          controller state/venv/home (default ~/.mindframe-dev)
    MINDFRAME_DEV_PLUGINS_ROOT  the plugins/ dir holding providers/ (default: auto)
    MINDFRAME_DEV_PORT_BASE     base for the sb/disp/tp block (default 7910)
    MINDFRAME_DEV_DASH_PORT     dashboard preferred port (default 7174)
"""

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --- Repo discovery (relative to this file; no hardcoded user paths) ---------

SCRIPT = Path(__file__).resolve()
MINDFRAME_ROOT = SCRIPT.parents[2]                 # skills/mindframe-dev/x -> mindframe/
PLUGINS_ROOT = Path(
    os.environ.get("MINDFRAME_DEV_PLUGINS_ROOT", str(MINDFRAME_ROOT.parents[1]))
)                                                  # mindframe -> frameworks -> plugins/
PROVIDERS = PLUGINS_ROOT / "providers"

DASH_DIR = MINDFRAME_ROOT / "dashboard"
TP_DIR = PROVIDERS / "taskpilot"
DISP_DIR = PROVIDERS / "dispatcher"
SB_DIR = PROVIDERS / "session-bridge" / "daemon"

# --- Controller state -------------------------------------------------------

DEV_ROOT = Path(os.environ.get("MINDFRAME_DEV_ROOT", str(Path.home() / ".mindframe-dev")))
VENV = DEV_ROOT / "venv"
HOME_DIR = DEV_ROOT / "home"          # MINDFRAME_HOME root (holds workspaces/)
WORKSPACES = HOME_DIR / "workspaces"  # one partition per workspace lives here
RUN = DEV_ROOT / "run"
LOGS = RUN / "logs"
STATE = RUN / "state.json"
BEARER = RUN / "dispatcher-bearer.token"

PY = str(VENV / "bin" / "python")
DEPS = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "httpx>=0.27", "pydantic>=2", "PyYAML>=6.0"]

PORT_BASE = int(os.environ.get("MINDFRAME_DEV_PORT_BASE", "7910"))
DASH_PORT_PREF = int(os.environ.get("MINDFRAME_DEV_DASH_PORT", "7174"))


def log(msg):
    print(msg, flush=True)


def die(msg):
    print(f"error: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# --- Small helpers ----------------------------------------------------------

def free_port(pref, taken=()):
    for p in [pref] + list(range(pref + 1, pref + 80)):
        if p in taken:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # SO_REUSEADDR so a port in TIME_WAIT (just-killed daemon) reads as
            # free -> ports stay stable across down/up instead of drifting.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    die(f"no free port near {pref}")


def http_ok(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def wait_health(url, name, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if http_ok(url):
            return True
        time.sleep(0.4)
    return False


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    RUN.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


# --- venv + dev home --------------------------------------------------------

def ensure_repos():
    for label, p in [("dashboard", DASH_DIR), ("taskpilot", TP_DIR),
                     ("dispatcher", DISP_DIR), ("session-bridge", SB_DIR)]:
        if not p.exists():
            die(f"{label} source not found at {p}\n"
                f"       set MINDFRAME_DEV_PLUGINS_ROOT to your local plugins/ dir")


def ensure_venv():
    marker = VENV / ".deps"
    want = hashlib.sha256("\n".join(DEPS).encode()).hexdigest()
    if marker.exists() and marker.read_text() == want and Path(PY).exists():
        return
    if not Path(PY).exists():
        log("creating dev venv ...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    log("installing daemon deps (first run only) ...")
    subprocess.run([PY, "-m", "pip", "install", "-q", "-U", "pip"], check=True)
    subprocess.run([PY, "-m", "pip", "install", "-q", *DEPS], check=True)
    marker.write_text(want)


# dev workspaces seeded by `up`. Each is a fully isolated partition under
# home/workspaces/<id>/. Personal is just another workspace (no special default).
DEFAULT_WORKSPACES = [
    ("personal", "Personal"),
    ("crestborne", "Crestborne"),
    ("pulsiv", "Pulsiv"),
    ("arctype", "Arctype"),
]


def _seed_dummy_frame(ws_dir, fid, title, body):
    fdir = ws_dir / ".mindframe" / "frames" / fid
    if (fdir / "index.html").exists():
        return
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "index.html").write_text(
        f'<!doctype html>\n<meta name="mf-patch" content="safe">\n'
        f"<title>{title}</title>\n<h1>{title}</h1>\n<p>{body}</p>\n")
    (fdir / "meta.json").write_text(json.dumps(
        {"id": fid, "title": title, "task_id": fid, "status": "running", "kind": "created"}))


def seed_workspace(ws_id):
    """Build one isolated workspace partition: its own .mindframe tree + a
    minimal .claude that shares subscription auth + plugin code with the real
    home, while keeping MCPs/connectors/vault local. Mirrors the named-workspace
    isolation model, minus the per-workspace daemons."""
    ws = WORKSPACES / ws_id
    for sub in ("vault", "frames", "connections", "secrets"):
        (ws / ".mindframe" / sub).mkdir(parents=True, exist_ok=True)
    (ws / ".mindframe" / "secrets").chmod(0o700)

    claude = ws / ".claude"
    (claude / "skills").mkdir(parents=True, exist_ok=True)
    real_claude = Path.home() / ".claude"
    # share subscription auth + plugin code; everything else stays workspace-local
    for name in (".credentials.json", "plugins"):
        link, target = claude / name, real_claude / name
        if target.exists() and not link.exists():
            try:
                link.symlink_to(target)
            except OSError:
                pass

    # per-workspace settings.json: carry plugin enablement / hooks / permissions
    # but NOT global mcpServers — symlinking the real file would leak every
    # global MCP into every workspace. mcpServers starts empty and grows per ws.
    settings = claude / "settings.json"
    if not settings.exists():
        base = {}
        rs = real_claude / "settings.json"
        if rs.exists():
            try:
                base = json.loads(rs.read_text())
            except Exception:
                base = {}
        base["mcpServers"] = {}
        settings.write_text(json.dumps(base, indent=2))

    cj = ws / ".claude.json"
    if not cj.exists():
        enabled = {}
        real_cj = Path.home() / ".claude.json"
        if real_cj.exists():
            try:
                enabled = json.loads(real_cj.read_text()).get("enabledPlugins", {})
            except Exception:
                enabled = {}
        cj.write_text(json.dumps({"mcpServers": {}, "enabledPlugins": enabled}, indent=2))

    gc = Path.home() / ".gitconfig"
    if gc.exists() and not (ws / ".gitconfig").exists():
        try:
            (ws / ".gitconfig").symlink_to(gc)
        except OSError:
            pass
    return ws


def seed_home(fresh):
    """MINDFRAME_HOME root that holds workspaces/ + a workspaces.yaml registry.
    Seeds the default workspace set with a little dummy content so the portal
    and per-workspace homes have something to show."""
    if fresh and HOME_DIR.exists():
        import shutil
        shutil.rmtree(HOME_DIR)
    WORKSPACES.mkdir(parents=True, exist_ok=True)

    for ws_id, _label in DEFAULT_WORKSPACES:
        seed_workspace(ws_id)

    # registry (hand-written YAML — the controller is stdlib-only, no PyYAML)
    reg = ["workspaces:"]
    for ws_id, label in DEFAULT_WORKSPACES:
        reg += [f"  {ws_id}:", f"    label: {label}"]
    (HOME_DIR / "workspaces.yaml").write_text("\n".join(reg) + "\n")

    # a touch of seed content in two workspaces
    _seed_dummy_frame(WORKSPACES / "personal", "welcome", "Welcome",
                      "Your personal workspace.")
    _seed_dummy_frame(WORKSPACES / "crestborne", "deal-memo", "Deal Memo",
                      "Crestborne workspace.")
    for ws_id, note, text in (("personal", "hello.md", "# Hello\nPersonal vault.\n"),
                              ("crestborne", "deal.md", "# Deal\nCrestborne vault.\n")):
        p = WORKSPACES / ws_id / ".mindframe" / "vault" / note
        if not p.exists():
            p.write_text(text)


# --- daemon specs + start ---------------------------------------------------

def daemon_specs(ports):
    sb, tp, disp, dash = ports["sb"], ports["tp"], ports["disp"], ports["dash"]
    sb_url = f"http://127.0.0.1:{sb}"
    tp_url = f"http://127.0.0.1:{tp}"
    disp_url = f"http://127.0.0.1:{disp}"

    base = os.environ.copy()
    base["PYTHONUNBUFFERED"] = "1"

    disp_data = HOME_DIR / "dispatcher"  # shared for now; per-workspace channels lands in slice C
    (disp_data / "recipes").mkdir(parents=True, exist_ok=True)
    (disp_data / "event-sources").mkdir(parents=True, exist_ok=True)
    channels = disp_data / "channels.yaml"
    if not channels.exists():
        channels.write_text("routes: []\npaused_routes: []\n")
    if not BEARER.exists():
        BEARER.parent.mkdir(parents=True, exist_ok=True)
        BEARER.write_text("dev-" + hashlib.sha256(str(DEV_ROOT).encode()).hexdigest()[:24])

    disp_env = {
        "DISPATCHER_DATA_DIR": str(disp_data),
        "DISPATCHER_DB_PATH": str(disp_data / "events.db"),
        "DISPATCHER_CHANNELS_FILE": str(channels),
        "DISPATCHER_RECIPES_DIR": str(disp_data / "recipes"),
        "DISPATCHER_EVENT_SOURCES_DIR": str(disp_data / "event-sources"),
        "DISPATCHER_CURSORS_DB": str(disp_data / "cursors.db"),
        "DISPATCHER_INGEST_TOKEN_FILE": str(BEARER),
        "SESSION_BRIDGE_URL": sb_url,
        "TASKPILOT_DAEMON_URL": tp_url,
    }

    return [
        {
            "name": "session-bridge",
            "cwd": SB_DIR,
            "args": [PY, "run.py"],
            "env": {**base, "SESSION_BRIDGE_PORT": str(sb), "SESSION_BRIDGE_BIND": "127.0.0.1"},
            "health": f"{sb_url}/health",
        },
        {
            "name": "taskpilot",
            "cwd": TP_DIR,
            "args": [PY, "daemon.py"],
            "env": {**base,
                    "TASKPILOT_DAEMON_PORT": str(tp),
                    "TASKPILOT_DATA_DIR": str(HOME_DIR / "taskpilot"),
                    # per-task HOME is the real target (slice B); until then any
                    # stray spawn lands in an isolated partition, not the real home
                    "TASKPILOT_AGENT_HOME": str(WORKSPACES / "personal"),
                    "SESSION_BRIDGE_URL": sb_url},
            "health": f"{tp_url}/health",
        },
        {
            "name": "dispatcher",
            "cwd": DISP_DIR,
            "args": [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(disp)],
            "env": {**base, **disp_env},
            "health": f"{disp_url}/api/health",
        },
        {
            "name": "dispatcher-poller",
            "cwd": DISP_DIR,
            "args": [PY, "-m", "app.poller"],
            "env": {**base, **disp_env},
            "health": None,  # background loop, no listen port
        },
        {
            "name": "dashboard",
            "cwd": DASH_DIR,
            "args": [PY, "server/server.py"],
            "env": {**base,
                    "PORT": str(dash),
                    # server derives frames/vault per-workspace from MINDFRAME_HOME/workspaces/<id>
                    "MINDFRAME_HOME": str(HOME_DIR),
                    "MINDFRAME_DISPATCHER_URL": disp_url,
                    "MINDFRAME_DISPATCHER_BEARER_FILE": str(BEARER),
                    "MINDFRAME_TASKPILOT_DAEMON": tp_url,
                    "MINDFRAME_TASKPILOT_HOME": str(HOME_DIR / "taskpilot"),
                    "MINDFRAME_TASKPILOT_DB": str(HOME_DIR / "taskpilot" / "taskpilot.db"),
                    "MINDFRAME_DISPATCHER_HOME": str(disp_data)},
            "health": f"http://127.0.0.1:{dash}/api/health",
        },
    ]


def start_one(spec):
    LOGS.mkdir(parents=True, exist_ok=True)
    logf = open(LOGS / f"{spec['name']}.log", "ab")
    try:
        proc = subprocess.Popen(
            spec["args"], cwd=str(spec["cwd"]), env=spec["env"],
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,  # detach: survives this short-lived controller
        )
    finally:
        logf.close()
    return proc.pid


# --- commands ---------------------------------------------------------------

def cmd_up(args):
    ensure_repos()
    ensure_venv()
    seed_home(args.fresh)

    state = load_state()
    ports = state.get("ports")
    if not ports:
        taken = set()
        ports = {}
        for key, pref in (("sb", PORT_BASE), ("disp", PORT_BASE + 1),
                          ("tp", PORT_BASE + 2), ("dash", DASH_PORT_PREF)):
            ports[key] = free_port(pref, taken)
            taken.add(ports[key])
    pids = state.get("pids", {})

    log(f"dev home : {HOME_DIR}")
    log(f"source   : {PLUGINS_ROOT}")
    log("")

    for spec in daemon_specs(ports):
        name = spec["name"]
        if spec["health"] and http_ok(spec["health"]):
            log(f"  {name:18s} already up ({spec['health']})")
            continue
        if not spec["health"] and pids.get(name) and pid_alive(pids[name]):
            log(f"  {name:18s} already up (pid {pids[name]})")
            continue
        pid = start_one(spec)
        pids[name] = pid
        if spec["health"]:
            ok = wait_health(spec["health"], name)
            log(f"  {name:18s} {'up' if ok else 'FAILED'}  pid {pid}  {spec['health']}")
            if not ok:
                log(f"     -> see {LOGS / (name + '.log')}")
        else:
            time.sleep(0.6)
            ok = pid_alive(pid)
            log(f"  {name:18s} {'up' if ok else 'FAILED'}  pid {pid}  (no health port)")

    state.update({"ports": ports, "pids": pids, "home": str(HOME_DIR),
                  "plugins_root": str(PLUGINS_ROOT)})
    save_state(state)

    log("")
    log(f"  dashboard -> http://127.0.0.1:{ports['dash']}/")
    log("  stop with: mindframe_dev.py down   (add --wipe to clear the dev home)")


def cmd_status(args):
    state = load_state()
    if not state:
        log("no dev stack recorded (run `up`)")
        return
    ports = state.get("ports", {})
    pids = state.get("pids", {})
    log(f"dev home : {state.get('home')}")
    log(f"source   : {state.get('plugins_root')}")
    log("")
    health = {
        "session-bridge": f"http://127.0.0.1:{ports.get('sb')}/health",
        "taskpilot": f"http://127.0.0.1:{ports.get('tp')}/health",
        "dispatcher": f"http://127.0.0.1:{ports.get('disp')}/api/health",
        "dispatcher-poller": None,
        "dashboard": f"http://127.0.0.1:{ports.get('dash')}/api/health",
    }
    for name, url in health.items():
        pid = pids.get(name)
        alive = pid_alive(pid) if pid else False
        if url:
            hz = "ok" if http_ok(url) else "down"
            log(f"  {name:18s} pid {str(pid or '-'):>7}  proc {'alive' if alive else 'dead ':5}  health {hz}")
        else:
            log(f"  {name:18s} pid {str(pid or '-'):>7}  proc {'alive' if alive else 'dead ':5}  (loop)")
    log("")
    log(f"  dashboard -> http://127.0.0.1:{ports.get('dash')}/")


def cmd_logs(args):
    targets = [args.name] if args.name else [
        "session-bridge", "taskpilot", "dispatcher", "dispatcher-poller", "dashboard"]
    for name in targets:
        f = LOGS / f"{name}.log"
        log(f"==== {name} ({f}) ====")
        if not f.exists():
            log("  (no log)")
            continue
        lines = f.read_text(errors="replace").splitlines()
        for line in lines[-args.tail:]:
            log("  " + line)


def cmd_open(args):
    state = load_state()
    port = (state.get("ports") or {}).get("dash", DASH_PORT_PREF)
    url = f"http://127.0.0.1:{port}/"
    log(url)
    for opener in ("xdg-open", "open"):
        try:
            subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue


NGINX_TMP = "/tmp/mindframe-dev.nginx"


def cmd_host(args):
    """Map a bare hostname to the dev dashboard via an nginx reverse proxy on
    port 80, so you reach it at http://<name>/ with no port. Uses *.localhost
    (auto-resolves to loopback, no /etc/hosts edit). nginx steps run under the
    NOPASSWD sudo rules for sites-available/enabled + reload."""
    name = args.name
    avail = f"/etc/nginx/sites-available/{name}"
    enabled = f"/etc/nginx/sites-enabled/{name}"

    if args.remove:
        # no flags: must match the NOPASSWD rule `rm /etc/nginx/sites-enabled/*`
        subprocess.run(["sudo", "-n", "rm", enabled], check=False)
        subprocess.run(["sudo", "-n", "nginx", "-t"], check=False)
        subprocess.run(["sudo", "-n", "systemctl", "reload", "nginx"], check=False)
        log(f"removed {name} (ran `nginx -t` + reload)")
        return

    state = load_state()
    port = args.port or (state.get("ports") or {}).get("dash", DASH_PORT_PREF)
    conf = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {name};

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_buffering off;
    }}
}}
"""
    Path(NGINX_TMP).write_text(conf)
    steps = [
        ["sudo", "-n", "cp", NGINX_TMP, avail],
        ["sudo", "-n", "ln", "-sf", avail, enabled],
        ["sudo", "-n", "nginx", "-t"],
        ["sudo", "-n", "systemctl", "reload", "nginx"],
    ]
    for step in steps:
        r = subprocess.run(step, capture_output=True, text=True)
        if r.returncode != 0:
            log(f"step failed: {' '.join(step)}")
            log((r.stderr or r.stdout).strip())
            die("could not install the nginx vhost (is the sudo NOPASSWD rule present?)")
    log(f"mapped http://{name}/  ->  127.0.0.1:{port}")
    log(f"open it: http://{name}/")


def cmd_down(args):
    state = load_state()
    pids = state.get("pids", {})
    if not pids:
        log("nothing recorded to stop")
    for name, pid in pids.items():
        if not pid or not pid_alive(pid):
            log(f"  {name:18s} not running")
            continue
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        log(f"  {name:18s} stopped (pid {pid})")
    # give them a moment, then SIGKILL stragglers
    time.sleep(1.0)
    for name, pid in pids.items():
        if pid and pid_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    if args.wipe:
        import shutil
        for d in (HOME_DIR, LOGS):
            if d.exists():
                shutil.rmtree(d)
        if STATE.exists():
            STATE.unlink()
        log("  wiped dev home + logs")
    else:
        # keep ports + home so the next `up` reuses the same ports
        state["pids"] = {}
        save_state(state)


def main():
    ap = argparse.ArgumentParser(prog="mindframe_dev.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_up = sub.add_parser("up", help="start the ephemeral stack")
    p_up.add_argument("--fresh", action="store_true", help="wipe the dev home first")
    sub.add_parser("status", help="show daemon health")
    p_logs = sub.add_parser("logs", help="tail daemon logs")
    p_logs.add_argument("name", nargs="?", help="one daemon, or all")
    p_logs.add_argument("--tail", type=int, default=40)
    sub.add_parser("open", help="print/open the dashboard url")
    p_host = sub.add_parser("host", help="map a bare hostname (port 80) to the dashboard via nginx")
    p_host.add_argument("name", nargs="?", default="mindframe-dev.localhost",
                        help="hostname (default mindframe-dev.localhost — auto-resolves to loopback)")
    p_host.add_argument("--port", type=int, help="dashboard port (default: from state)")
    p_host.add_argument("--remove", action="store_true", help="remove the mapping")
    p_down = sub.add_parser("down", help="stop the stack")
    p_down.add_argument("--wipe", action="store_true", help="also delete the dev home + logs")
    args = ap.parse_args()

    {"up": cmd_up, "status": cmd_status, "logs": cmd_logs,
     "open": cmd_open, "host": cmd_host, "down": cmd_down}[args.cmd](args)


if __name__ == "__main__":
    main()
