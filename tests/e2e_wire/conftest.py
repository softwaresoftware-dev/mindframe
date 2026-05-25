"""Fixtures for Tier 1 wire tests.

Spawns a real dispatcher + dashboard on test ports, against tmpdir-rooted
state. No tmux, no claude — the stub spawner stands in for taskpilot.

Test ports: dispatcher 18911, dashboard 15174. High enough that prod
ports (8911 / 5174) shouldn't clash; if a developer happens to be running
something on the test ports, the fixture will fail fast.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DISPATCHER_ROOT = PLUGIN_ROOT.parent.parent / "providers" / "dispatcher"

STUB_SPAWNER = Path(__file__).resolve().parent / "stub_spawner.py"
MINDFRAME_SPAWN_CLI = PLUGIN_ROOT / "lib" / "spawn.py"


@dataclass
class WireEnv:
    """Everything a test needs to talk to the test dispatcher + dashboard."""
    tmpdir: Path
    frames_root: Path
    dispatcher_port: int
    dashboard_port: int
    bearer: str
    bearer_file: Path
    recipes_dir: Path
    channels_file: Path
    dispatcher_proc: subprocess.Popen
    dashboard_proc: subprocess.Popen

    @property
    def dispatcher_url(self) -> str:
        return f"http://127.0.0.1:{self.dispatcher_port}"

    @property
    def dashboard_url(self) -> str:
        return f"http://127.0.0.1:{self.dashboard_port}"


def _pick_free_port() -> int:
    """Ask the OS for an unused port. There's a slim race between this and
    the service binding it, but in test environments it's reliable."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout_s: float = 5.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"service not responding within {timeout_s}s at {url}")


def _write_test_recipe(recipes_dir: Path) -> None:
    """A minimal mindframe-shaped recipe pointing at the stub spawner."""
    d = recipes_dir / "wire-test"
    d.mkdir(parents=True, exist_ok=True)
    (d / "recipe.yaml").write_text(dedent("""\
        task_id_pattern: "wire-{event_id}"
        task_name: wire-test
        kind: task
        model: haiku

        brief_schema:
          required: []
          optional: []

        plugins:
          base: []
          optional_pool: []
        mcps:
          base: []
          optional_pool: []

        frame:
          title: "Wire test"
          seed_block:
            type: summary
            tone: info
            title: "Wire test starting"
            body: "Seed block written synchronously by lib.frame.create_frame."
          tags: [wire, test]

        starter_prompt: |
          Wire test agent. task_id={task_id}, event_id={event_id}.
    """))
    (d / "brief.json").write_text("{}")


def _write_channels(channels_file: Path) -> None:
    channels_file.write_text(dedent("""\
        routes:
          - source: test
            event_type: wire-fire
            target: spawn:wire-test
    """))


@pytest.fixture
def wire_env(tmp_path: Path) -> WireEnv:
    """Hermetic dispatcher + dashboard pair against tmpdir state."""
    # OS-assigned ports per test → no collisions between successive tests
    # waiting on TIME_WAIT, and no chance of clashing with the dev box's
    # real dispatcher (8911) or dashboard (5174).
    dispatcher_port = _pick_free_port()
    dashboard_port = _pick_free_port()

    frames_root = tmp_path / "mindframe-frames"
    frames_root.mkdir()
    recipes_dir = tmp_path / "dispatcher-recipes"
    recipes_dir.mkdir()
    channels_file = tmp_path / "channels.yaml"
    bearer = secrets.token_urlsafe(24)
    bearer_file = tmp_path / "dispatcher-bearer.token"
    bearer_file.write_text(bearer)
    bearer_file.chmod(0o600)
    audit_db = tmp_path / "events.db"

    _write_test_recipe(recipes_dir)
    _write_channels(channels_file)

    # Shared env for both services.
    base_env = os.environ.copy()
    base_env["MINDFRAME_FRAMES_ROOT"] = str(frames_root)

    # --- dispatcher ---
    dispatcher_env = dict(base_env)
    dispatcher_env.update({
        "DISPATCHER_INGEST_TOKEN": bearer,
        "DISPATCHER_RECIPES_DIR": str(recipes_dir),
        "DISPATCHER_CHANNELS_FILE": str(channels_file),
        "DISPATCHER_DB_PATH": str(audit_db),
        "TASKPILOT_SPAWNER_CLI": str(STUB_SPAWNER),
        "MINDFRAME_SPAWN_CLI": str(MINDFRAME_SPAWN_CLI),
        # Avoid the dispatcher trying to reach the real session-bridge.
        "SESSION_BRIDGE_URL": "http://127.0.0.1:1",  # blackholed
    })
    dispatcher_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(dispatcher_port), "--log-level", "warning"],
        cwd=str(DISPATCHER_ROOT),
        env=dispatcher_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # --- dashboard ---
    dashboard_env = dict(base_env)
    dashboard_env.update({
        "PORT": str(dashboard_port),
        "MINDFRAME_DISPATCHER_URL": f"http://127.0.0.1:{dispatcher_port}",
        "MINDFRAME_DISPATCHER_BEARER_FILE": str(bearer_file),
    })
    dashboard_proc = subprocess.Popen(
        [sys.executable, "server/server.py"],
        cwd=str(PLUGIN_ROOT / "dashboard"),
        env=dashboard_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    env = WireEnv(
        tmpdir=tmp_path,
        frames_root=frames_root,
        dispatcher_port=dispatcher_port,
        dashboard_port=dashboard_port,
        bearer=bearer,
        bearer_file=bearer_file,
        recipes_dir=recipes_dir,
        channels_file=channels_file,
        dispatcher_proc=dispatcher_proc,
        dashboard_proc=dashboard_proc,
    )

    try:
        _wait_for_http(f"{env.dispatcher_url}/api/health", timeout_s=8)
        _wait_for_http(f"{env.dashboard_url}/api/health", timeout_s=8)
    except Exception:
        dispatcher_proc.terminate()
        dashboard_proc.terminate()
        raise

    yield env

    for proc in (dispatcher_proc, dashboard_proc):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
