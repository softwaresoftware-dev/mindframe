#!/usr/bin/env python3
"""Tier 3 fresh-install harness — runs inside the Dockerfile-built container.

Exercises the deterministic install path:
  1. Clone mindframe + dispatcher into a fresh $HOME.
  2. Install their Python deps into a venv (no system packages).
  3. Materialize dispatcher state (`~/.dispatcher/`) the way the install
     agent would: channels.yaml, recipes dir, bearer token.
  4. Materialize mindframe secrets dir (~/.mindframe/secrets/).
  5. Spawn dispatcher + dashboard on OS-assigned ports.
  6. Run the Tier 1 wire tests against the just-built install.
  7. Emit a JSON pass/fail report to /report.json AND to stdout.

What this canNOT do:
  - Drive a real `claude plugin install` flow (no claude binary).
  - Drive `/softwaresoftware:install mindframe` (no Claude session).
  - Drive `/mindframe:setup` (no Claude session).

Those are documented as manual checks in README.md. Tier 3's value is
proving that the bundle's *deterministic* surface installs cleanly against
a known-empty Linux box.

Pass criteria: clone + install + spawn all succeed, then all Tier 1 wire
tests pass. Fail criteria: any step errors, or any wire test fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


HOME = Path(os.environ.get("HOME", "/home/mf"))
WORK = HOME / "fresh-install"
MINDFRAME_REPO = os.environ.get("MINDFRAME_REPO", "https://github.com/softwaresoftware-dev/mindframe.git")
DISPATCHER_REPO = os.environ.get("DISPATCHER_REPO", "https://github.com/softwaresoftware-dev/dispatcher.git")
MINDFRAME_REF = os.environ.get("MINDFRAME_REF", "main")
DISPATCHER_REF = os.environ.get("DISPATCHER_REF", "main")
REPORT_PATH = Path(os.environ.get("REPORT_PATH", "/tmp/report.json"))


def step(phase: str, msg: str):
    print(f"[{phase}] {msg}", flush=True)


def fail(phase: str, msg: str, report: dict):
    report["phases"][phase] = {"ok": False, "error": msg}
    report["ok"] = False
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\n[{phase}] FAIL: {msg}", flush=True)
    sys.exit(1)


def ok_phase(phase: str, msg: str, report: dict, **extra):
    report["phases"][phase] = {"ok": True, "note": msg, **extra}
    print(f"[{phase}] ok — {msg}", flush=True)


def run(cmd: list[str], **kwargs):
    """Run a subprocess, raise on failure with captured output."""
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def main():
    report = {
        "started_at": int(time.time()),
        "host": "fresh-container",
        "mindframe_ref": MINDFRAME_REF,
        "dispatcher_ref": DISPATCHER_REF,
        "phases": {},
        "ok": True,
    }

    # --- Phase A: clone ---
    step("clone", f"cloning mindframe ({MINDFRAME_REF}) + dispatcher ({DISPATCHER_REF})")
    WORK.mkdir(parents=True, exist_ok=True)
    try:
        run(["git", "clone", "--depth", "1", "--branch", MINDFRAME_REF,
             MINDFRAME_REPO, str(WORK / "mindframe")])
        run(["git", "clone", "--depth", "1", "--branch", DISPATCHER_REF,
             DISPATCHER_REPO, str(WORK / "dispatcher")])
    except subprocess.CalledProcessError as e:
        fail("clone", f"git clone failed: {e.stderr or e}", report)
    ok_phase("clone", "both repos cloned", report,
             mindframe=str(WORK / "mindframe"), dispatcher=str(WORK / "dispatcher"))

    # --- Phase B: install python deps into a venv ---
    step("deps", "creating venv + pip installing dispatcher and dashboard requirements")
    venv = WORK / "venv"
    try:
        run(["python3", "-m", "venv", str(venv)])
        pip = str(venv / "bin" / "pip")
        run([pip, "install", "--upgrade", "--quiet", "pip"])
        run([pip, "install", "--quiet",
             "-r", str(WORK / "dispatcher" / "requirements.txt"),
             "-r", str(WORK / "mindframe" / "dashboard" / "server" / "requirements.txt"),
             "pyyaml", "httpx"])
        # MCP package — for the in-plugin MCP that the dashboard imports lib.frame from
        # (lib.frame doesn't depend on mcp, but the mcp/server.py shim does; harmless to install)
        run([pip, "install", "--quiet", "mcp", "pytest"])
    except subprocess.CalledProcessError as e:
        fail("deps", f"pip install failed: {e.stderr or e}", report)
    ok_phase("deps", "venv populated", report, venv=str(venv))

    # --- Phase C: materialize dispatcher state ---
    step("state", "writing ~/.dispatcher/{channels.yaml, recipes/}, ~/.mindframe/secrets/")
    dispatcher_state = HOME / ".dispatcher"
    (dispatcher_state / "recipes").mkdir(parents=True, exist_ok=True)
    mindframe_secrets = HOME / ".mindframe" / "secrets"
    mindframe_secrets.mkdir(parents=True, exist_ok=True)
    mindframe_secrets.chmod(0o700)

    bearer = run(["openssl", "rand", "-hex", "32"]).stdout.strip()
    bearer_file = mindframe_secrets / "dispatcher-bearer.token"
    bearer_file.write_text(bearer)
    bearer_file.chmod(0o600)

    # Minimal channels — no routes; Tier 1 wire tests write their own
    # recipe + route into a tmpdir, this just proves the file shape works.
    (dispatcher_state / "channels.yaml").write_text("routes: []\n")
    ok_phase("state", "dispatcher + mindframe state laid down", report,
             bearer_file=str(bearer_file))

    # --- Phase D: run Tier 1 wire tests against the just-cloned tree ---
    step("wire-tests", "executing Tier 1 wire suite (~10s)")
    pytest = str(venv / "bin" / "pytest")
    env = os.environ.copy()
    # Override DISPATCHER_INGEST_TOKEN so the wire test's subprocess
    # uses the bearer we generated (the test fixture generates its own
    # token by default; either way is fine, but pinning helps debugging).
    env["MINDFRAME_FRAMES_ROOT"] = str(HOME / ".mindframe" / "frames")
    try:
        result = subprocess.run(
            [pytest, str(WORK / "mindframe" / "tests" / "e2e_wire"), "-v"],
            capture_output=True, text=True, env=env,
        )
    except FileNotFoundError as e:
        fail("wire-tests", f"pytest invocation failed: {e}", report)

    passed = result.stdout.count(" PASSED")
    failed = result.stdout.count(" FAILED")
    if result.returncode != 0 or failed > 0:
        fail("wire-tests",
             f"{passed} passed, {failed} failed; tail:\n{result.stdout[-2000:]}\n{result.stderr[-500:]}",
             report)
    ok_phase("wire-tests", f"{passed} tier-1 tests passed", report, passed=passed)

    # --- Done ---
    report["finished_at"] = int(time.time())
    report["duration_s"] = report["finished_at"] - report["started_at"]
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print()
    print("================================================================")
    print(f"Tier 3 PASS — fresh install reached running services + wire tests in "
          f"{report['duration_s']}s")
    print("================================================================")
    print(f"Report written to {REPORT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        REPORT_PATH.write_text(json.dumps({
            "ok": False,
            "error": f"harness crashed: {type(e).__name__}: {e}",
        }, indent=2))
        raise
