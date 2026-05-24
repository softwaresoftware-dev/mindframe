"""Tests for the spawn CLI — what dispatcher/scripts shell out to."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SPAWN_CLI = Path(__file__).resolve().parent.parent / "spawn.py"


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    root = tmp_path / ".mindframe" / "frames"
    root.mkdir(parents=True)
    monkeypatch.setenv("MINDFRAME_FRAMES_ROOT", str(root))
    return root


def _run(*args, env_extra=None) -> tuple[int, dict, str]:
    """Invoke the spawn CLI as a subprocess. Returns (rc, stdout_json, stderr)."""
    import os
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    cp = subprocess.run(
        [sys.executable, str(SPAWN_CLI), *args],
        capture_output=True, text=True, env=env,
    )
    try:
        out = json.loads(cp.stdout)
    except json.JSONDecodeError:
        out = {"_raw_stdout": cp.stdout}
    return cp.returncode, out, cp.stderr


def test_cli_minimal_success(fake_root):
    rc, out, err = _run("--title", "From CLI")
    assert rc == 0
    assert out["ok"]
    assert "id" in out and len(out["id"]) == 10
    assert Path(out["frame_dir"]).is_dir()
    # Seed block defaulted
    blocks = (Path(out["frame_dir"]) / "blocks.jsonl").read_text().splitlines()
    assert len(blocks) == 1


def test_cli_with_seed_block(fake_root):
    seed = {"type": "summary", "tone": "ok", "title": "From CLI", "body": "hi"}
    rc, out, _ = _run("--title", "X", "--seed-block-json", json.dumps(seed))
    assert rc == 0
    assert out["ok"]
    block = json.loads((Path(out["frame_dir"]) / "blocks.jsonl").read_text().splitlines()[0])
    assert block["tone"] == "ok"
    assert block["body"] == "hi"


def test_cli_with_spawned_by(fake_root):
    sb = {"kind": "dispatcher-event", "source": "sentry", "event_id": "e-99"}
    rc, out, _ = _run("--title", "X", "--spawned-by-json", json.dumps(sb))
    assert rc == 0
    meta = json.loads((Path(out["frame_dir"]) / "meta.json").read_text())
    assert meta["spawned_by"] == sb


def test_cli_with_tags(fake_root):
    rc, out, _ = _run("--title", "X", "--tags", "incident,payments,p1")
    assert rc == 0
    meta = json.loads((Path(out["frame_dir"]) / "meta.json").read_text())
    assert meta["tags"] == ["incident", "payments", "p1"]


def test_cli_with_explicit_id(fake_root):
    rc, out, _ = _run("--title", "X", "--id", "explicit-cli-id")
    assert rc == 0
    assert out["id"] == "explicit-cli-id"


def test_cli_bad_seed_block_json(fake_root):
    rc, out, _ = _run("--title", "X", "--seed-block-json", "not json{")
    assert rc == 1
    assert not out["ok"]
    assert "parse error" in out["error"]


def test_cli_seed_block_not_object(fake_root):
    rc, out, _ = _run("--title", "X", "--seed-block-json", '"a string"')
    assert rc == 1
    assert "must be a JSON object" in out["error"]


def test_cli_seed_block_unknown_type(fake_root):
    rc, out, _ = _run("--title", "X", "--seed-block-json", '{"type":"nonsense"}')
    assert rc == 1
    assert "unknown block type" in out["error"]


def test_cli_rejects_empty_title(fake_root):
    rc, out, _ = _run("--title", "   ")
    assert rc == 1
    assert "title" in out["error"]


def test_cli_duplicate_id_errors(fake_root):
    rc1, out1, _ = _run("--title", "X", "--id", "dup-id")
    assert rc1 == 0
    rc2, out2, _ = _run("--title", "Y", "--id", "dup-id")
    assert rc2 == 1
    assert "already exists" in out2["error"]


def test_cli_emits_only_json_on_stdout(fake_root):
    """Dispatcher parses stdout — anything that isn't the JSON envelope is
    a bug."""
    rc, out, err = _run("--title", "Clean output")
    assert rc == 0
    # out is already a parsed dict from _run; just confirm parse succeeded.
    assert out["ok"]
    # Stderr is allowed to carry diagnostics but the JSON envelope must be
    # the only thing on stdout.
