"""Tier-3 fresh-install invariants.

Guards the regressions found in the 2026-06-09 clean-room run (shipped in
mindframe 1.0.1), so a fresh `paste install.txt -> it just works` install can't
silently rot again. Hermetic: no network, no auth, no daemons, no LLM. Each test
pins one thing the install flow assembles.

The end-to-end install (resolver -> 7 plugins -> dashboard -> spawn) needs a real
Claude subscription and tmux, so it lives in the manual clean-room harness, not
here. These tests cover the deterministic pieces CI can prove on every push.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # mindframe repo root


# --------------------------- PHASE 3.4: the setup brief ---------------------------

def test_setup_brief_sed_is_clean(tmp_path):
    """install.txt fills the brief's one placeholder (__FRAME_DIR__) and the
    result must carry no dev-note, no stale sandbox model, and no unsubstituted
    placeholder. Regression: the `NEEDS FIXING` HTML comment used to be sed'd
    verbatim into the spawned setup agent's standing brief."""
    brief = (ROOT / "setup" / "brief.md").read_text()
    frame_dir = str(tmp_path / "frame")
    filled = brief.replace("__FRAME_DIR__", frame_dir)  # the only substitution install.txt now does

    assert "NEEDS FIXING" not in filled, "a dev-note leaked into the setup agent's brief"
    assert "__OPERATOR_HOME__" not in filled, "stale __OPERATOR_HOME__ placeholder (dropped in 1.0.1)"
    assert "__FRAME_DIR__" not in filled, "unsubstituted __FRAME_DIR__ placeholder"
    assert "you run in a sandbox" not in filled.lower(), "stale sandbox framing (agent runs AS the operator)"
    assert frame_dir in filled, "__FRAME_DIR__ was never present to substitute"


# --------------------------- PHASE 1/2: install.txt contract ---------------------------
#
# setup/install.txt is the repo source of truth for the hosted
# https://mindframe.softwaresoftware.dev/install.txt (deployed verbatim).

def test_install_txt_documents_uv_prereq():
    """install.txt must carry the uv prerequisite — without uv the resolver's
    MCP silently fails to connect and PHASE 2 dead-ends. Regression: the hard
    blocker that made fresh installs fail at the resolver."""
    out = (ROOT / "setup" / "install.txt").read_text()
    assert "astral.sh/uv" in out, "uv install command not documented in install.txt"
    assert re.search(r"\buv\b", out), "uv prerequisite not mentioned"


def test_install_txt_capability_list_drops_notification():
    """notification was retired as a bundle capability (its provider repos were
    private and never cloned on a fresh box). PHASE 2's capability list must
    not include it."""
    out = (ROOT / "setup" / "install.txt").read_text()
    m = re.search(r"resolves the capability graph \(([^)]*)\)", out)
    assert m, "couldn't find the PHASE 2 capability list in install.txt"
    assert "notification" not in m.group(1).lower(), "notification is back in the capability list"


def test_install_txt_never_runs_skills_via_bash():
    """Skills are invoked in-session, not shelled out. Regression: PHASE 2 used
    to say 'Run via Bash: /softwaresoftware:install mindframe', which a
    literal-minded install agent executes as a shell command and fails."""
    out = (ROOT / "setup" / "install.txt").read_text()
    for line_no, line in enumerate(out.splitlines(), 1):
        if "Run via Bash" in line:
            window = "\n".join(out.splitlines()[line_no - 1:line_no + 4])
            assert "/softwaresoftware:" not in window, \
                f"install.txt line {line_no}: a slash-command is labelled 'Run via Bash'"


def test_doctor_does_not_require_notification():
    """doctor's required-capability table must not list notification."""
    doc = (ROOT / "skills" / "doctor" / "SKILL.md").read_text()
    assert "| `notification` |" not in doc, "notification is back in doctor's capability table"


# --------------------------- Surface boots ---------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def test_dashboard_boots_and_serves(tmp_path):
    """Boot the Surface server (the one piece mindframe owns) with its declared
    deps and prove /api/health is ok and /api/frames serves. Catches dep/boot
    regressions. Requires the dashboard requirements installed (CI does this)."""
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
        import uvicorn  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("dashboard deps not installed (run: pip install -r dashboard/server/requirements.txt)")

    server_py = ROOT / "dashboard" / "server" / "server.py"
    port = _free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "HOME": str(tmp_path),  # isolate frames/vault under the tmp home
        "MINDFRAME_FRAMES_ROOT": str(tmp_path / "frames"),
        # Changing HOME drops pip --user site-packages from the child's import
        # path; propagate this interpreter's sys.path so the server finds its
        # deps wherever they're installed (user-site, venv, or system).
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }
    proc = subprocess.Popen(
        [sys.executable, str(server_py)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        health = None
        while time.time() < deadline:
            try:
                health = _get_json(base + "/api/health")
                break
            except Exception:
                time.sleep(0.3)
        assert health is not None, "dashboard never answered /api/health"
        assert health.get("ok") is True, f"/api/health not ok: {health}"
        assert health.get("port") == port
        frames = _get_json(base + "/api/frames")
        assert "frames" in frames and isinstance(frames["frames"], list)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
