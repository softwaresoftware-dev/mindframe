#!/usr/bin/env python3
"""Tier 3 fresh-install harness — pure Python, no Docker.

Boots a clean workspace at $WORKDIR (default: a fresh tmpdir), clones
mindframe + dispatcher into it, builds a Python venv, installs deps, then
runs the Tier 1 wire suite against the just-cloned tree with HOME pointed
at the workspace so plugin/state lookups can't reach the real machine.

Runs on Linux, macOS, and Windows. The Docker harness is gone — a native
script is faster, easier to debug, and works inside any GitHub Actions
runner without a docker daemon.

What this tests:
  - git clone of both repos at the given refs
  - pip install of dispatcher + dashboard + mindframe deps into a fresh venv
  - dispatcher's resolver finding mindframe-spawn via the cache lookup
    (because Tier 1 conftest passes MINDFRAME_SPAWN_CLI explicitly, the
    cache fallback isn't exercised here — that's covered by the dispatcher
    unit tests in test_mindframe_resolver.py)
  - dispatcher + dashboard subprocess startup against the fresh checkout
  - All 13 Tier 1 wire tests (event → frame → blocks → SSE → resumption)

What this does NOT test:
  - claude binary install (no Claude session)
  - /softwaresoftware:install resolver flow (handled by softwaresoftware
    plugin's own tests)
  - Recipe authoring / vault bootstrap (conversational, manual check)

See README.md for the manual checks needed to declare the full install
flow ready.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_MINDFRAME_REPO = os.environ.get(
    "MINDFRAME_REPO", "https://github.com/softwaresoftware-dev/mindframe.git")
DEFAULT_DISPATCHER_REPO = os.environ.get(
    "DISPATCHER_REPO", "https://github.com/softwaresoftware-dev/dispatcher.git")


# ---------- output helpers ----------


def step(phase: str, msg: str) -> None:
    print(f"[{phase}] {msg}", flush=True)


def fail(report: dict, phase: str, msg: str) -> int:
    report["phases"][phase] = {"ok": False, "error": msg}
    report["ok"] = False
    print(f"\n[{phase}] FAIL: {msg}", flush=True)
    return 1


def ok_phase(report: dict, phase: str, msg: str, **extra: Any) -> None:
    report["phases"][phase] = {"ok": True, "note": msg, **extra}
    print(f"[{phase}] ok — {msg}", flush=True)


# ---------- subprocess wrappers ----------


def run(cmd: list[str], *, env: dict[str, str] | None = None,
        cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          env=env, cwd=cwd, check=check)


def venv_python(venv: Path) -> str:
    """Cross-platform: venv's python lives in Scripts/ on Windows, bin/ elsewhere."""
    candidate = venv / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    if not candidate.exists():
        # uv-style venvs sometimes use python3 instead
        candidate = candidate.with_name("python3" + (".exe" if os.name == "nt" else ""))
    return str(candidate)


def venv_pip(venv: Path) -> list[str]:
    """Invoke pip via python -m pip — avoids platform differences in
    where pip.exe / pip live."""
    return [venv_python(venv), "-m", "pip"]


# ---------- harness phases ----------


def phase_acquire_source(report: dict, workdir: Path, args) -> bool:
    """Get mindframe + dispatcher source into the workdir. Either copy from
    pre-checked-out local paths (CI uses this — actions/checkout already
    pulled the right ref) or git clone from GitHub (manual / from-scratch
    runs use this)."""
    if args.mindframe_path and args.dispatcher_path:
        step("source", f"copying from local paths ({args.mindframe_path}, {args.dispatcher_path})")
        try:
            shutil.copytree(args.mindframe_path, workdir / "mindframe",
                            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache",
                                                          "venv", "node_modules"))
            shutil.copytree(args.dispatcher_path, workdir / "dispatcher",
                            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache",
                                                          "venv", "node_modules"))
        except (OSError, shutil.Error) as e:
            fail(report, "source", f"local-path copy failed: {e}")
            return False
        ok_phase(report, "source", "copied from local checkouts",
                 mindframe_source=args.mindframe_path,
                 dispatcher_source=args.dispatcher_path)
        return True

    step("source", f"git clone mindframe@{args.mindframe_ref} + dispatcher@{args.dispatcher_ref}")
    try:
        run(["git", "clone", "--depth", "1", "--branch", args.mindframe_ref,
             args.mindframe_repo, str(workdir / "mindframe")])
        run(["git", "clone", "--depth", "1", "--branch", args.dispatcher_ref,
             args.dispatcher_repo, str(workdir / "dispatcher")])
    except subprocess.CalledProcessError as e:
        fail(report, "source", f"git clone failed: {e.stderr or e}")
        return False
    ok_phase(report, "source", "both repos cloned",
             mindframe=str(workdir / "mindframe"),
             dispatcher=str(workdir / "dispatcher"))
    return True


def phase_venv(report: dict, workdir: Path) -> bool:
    step("venv", "creating venv + installing deps")
    venv = workdir / "venv"
    try:
        run([sys.executable, "-m", "venv", str(venv)])
        run(venv_pip(venv) + ["install", "--upgrade", "--quiet", "pip"])
        run(venv_pip(venv) + ["install", "--quiet",
            "-r", str(workdir / "dispatcher" / "requirements.txt"),
            "-r", str(workdir / "mindframe" / "dashboard" / "server" / "requirements.txt"),
            "pyyaml", "httpx", "mcp", "pytest"])
    except subprocess.CalledProcessError as e:
        fail(report, "venv", f"pip install failed: {e.stderr or e}")
        return False
    ok_phase(report, "venv", "venv populated", venv=str(venv))
    return True


def phase_state(report: dict, workdir: Path) -> bool:
    """Materialize ~/.dispatcher and ~/.mindframe/secrets the way the
    install agent (PHASE 7) would. The wire tests pin their own bearer
    via env, so this state mostly proves the file layout works on disk."""
    step("state", "writing dispatcher + mindframe state in fresh HOME")
    home = workdir / "home"
    dispatcher_state = home / ".dispatcher" / "recipes"
    dispatcher_state.mkdir(parents=True, exist_ok=True)
    mindframe_secrets = home / ".mindframe" / "secrets"
    mindframe_secrets.mkdir(parents=True, exist_ok=True)
    # chmod 700 is POSIX-only; harmless to attempt on Windows (it's a no-op).
    try:
        mindframe_secrets.chmod(0o700)
    except (OSError, NotImplementedError):
        pass

    try:
        result = run(["openssl", "rand", "-hex", "32"])
        bearer = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Windows without openssl — fall back to Python's secrets.
        import secrets as _s
        bearer = _s.token_hex(32)
    bearer_file = mindframe_secrets / "dispatcher-bearer.token"
    bearer_file.write_text(bearer)
    try:
        bearer_file.chmod(0o600)
    except (OSError, NotImplementedError):
        pass

    (home / ".dispatcher" / "channels.yaml").write_text("routes: []\n")
    ok_phase(report, "state", "state laid down under fresh HOME",
             home=str(home), bearer_file=str(bearer_file))
    return True


def phase_wire_tests(report: dict, workdir: Path) -> bool:
    step("wire-tests", "running Tier 1 wire suite against fresh tree")
    venv = workdir / "venv"
    home = workdir / "home"
    env = os.environ.copy()
    env["HOME"] = str(home)
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
    env["MINDFRAME_FRAMES_ROOT"] = str(home / ".mindframe" / "frames")

    pytest_cmd = [venv_python(venv), "-m", "pytest", "-v",
                  str(workdir / "mindframe" / "tests" / "e2e_wire")]
    try:
        result = subprocess.run(pytest_cmd, capture_output=True, text=True, env=env)
    except FileNotFoundError as e:
        fail(report, "wire-tests", f"pytest invocation failed: {e}")
        return False
    passed = result.stdout.count(" PASSED")
    failed = result.stdout.count(" FAILED")
    if result.returncode != 0 or failed > 0:
        tail = (result.stdout or "")[-2500:] + "\n" + (result.stderr or "")[-500:]
        fail(report, "wire-tests",
             f"{passed} passed, {failed} failed; tail:\n{tail}")
        return False
    ok_phase(report, "wire-tests", f"{passed} tier-1 tests passed", passed=passed)
    return True


# ---------- main ----------


def main() -> int:
    p = argparse.ArgumentParser(description="Tier 3 fresh-install dry-run (native)")
    p.add_argument("--workdir", help="Workdir (default: fresh tmpdir, removed unless --keep)")
    p.add_argument("--keep", action="store_true", help="Keep workdir after run for inspection")
    p.add_argument("--mindframe-ref", default=os.environ.get("MINDFRAME_REF", "main"))
    p.add_argument("--dispatcher-ref", default=os.environ.get("DISPATCHER_REF", "main"))
    p.add_argument("--mindframe-repo", default=DEFAULT_MINDFRAME_REPO)
    p.add_argument("--dispatcher-repo", default=DEFAULT_DISPATCHER_REPO)
    p.add_argument("--mindframe-path",
                   help="Use a local mindframe checkout instead of cloning (overrides --mindframe-ref)")
    p.add_argument("--dispatcher-path",
                   help="Use a local dispatcher checkout instead of cloning (overrides --dispatcher-ref)")
    p.add_argument("--report",
                   default=os.environ.get("REPORT_PATH", str(Path(tempfile.gettempdir()) / "mf-tier3-report.json")))
    args = p.parse_args()

    print("Tier 3 — fresh-install dry-run (native)")
    print(f"  platform: {sys.platform}, python: {sys.version.split()[0]}")
    print(f"  mindframe@{args.mindframe_ref}")
    print(f"  dispatcher@{args.dispatcher_ref}")
    print()

    if args.workdir:
        workdir = Path(args.workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        workdir = Path(tempfile.mkdtemp(prefix="mf-tier3-"))
        cleanup = not args.keep

    started = int(time.time())
    report: dict[str, Any] = {
        "started_at": started,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "workdir": str(workdir),
        "mindframe_ref": args.mindframe_ref,
        "dispatcher_ref": args.dispatcher_ref,
        "phases": {},
        "ok": True,
    }
    rc = 0
    try:
        ok = phase_acquire_source(report, workdir, args) \
            and phase_venv(report, workdir) \
            and phase_state(report, workdir) \
            and phase_wire_tests(report, workdir)
        rc = 0 if ok else 1
    finally:
        report["finished_at"] = int(time.time())
        report["duration_s"] = report["finished_at"] - started
        Path(args.report).write_text(json.dumps(report, indent=2))
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)

    print()
    if rc == 0:
        print(f"Tier 3 PASS — {report['duration_s']}s")
    else:
        print(f"Tier 3 FAIL — {report['duration_s']}s")
    print(f"Report: {args.report}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
