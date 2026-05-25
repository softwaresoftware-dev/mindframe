"""Hermetic tests for the mindframe MCP. No daemons, no MCP transport — calls
the tool functions directly. The MCP wrapper is thin; this tests the logic
under it (id resolution, validation, file writes, locking)."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

# server.py lives one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    # Path.home() reads HOME on POSIX, USERPROFILE on Windows — set both
    # so test redirection works cross-platform. Also point
    # $MINDFRAME_FRAMES_ROOT directly at the tmpdir so lib.frame.frames_root()
    # resolves there without depending on Path.home() at all.
    frames = tmp_path / ".mindframe" / "frames"
    frames.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("MINDFRAME_FRAMES_ROOT", str(frames))
    monkeypatch.delenv("MINDFRAME_ID", raising=False)
    # Force a re-import so any module-level captures pick up the new env.
    if "server" in sys.modules:
        del sys.modules["server"]
    import server  # noqa: E402
    server.FRAMES_ROOT = frames
    return server, frames


def _make_frame(frames: Path, mid: str) -> Path:
    fdir = frames / mid
    fdir.mkdir()
    (fdir / "blocks.jsonl").touch()
    (fdir / "meta.json").write_text(json.dumps({"id": mid, "title": "untitled"}))
    return fdir


# ---------- uuid7 ----------
def test_uuid7_is_valid_uuid_with_version_7(fake_home):
    server, _ = fake_home
    raw = server._uuid7()
    parsed = uuid.UUID(raw)
    assert parsed.version == 7


def test_uuid7_sorts_chronologically(fake_home):
    server, _ = fake_home
    ids = []
    for _ in range(10):
        ids.append(server._uuid7())
        time.sleep(0.002)
    assert ids == sorted(ids), "UUIDv7s must sort chronologically as strings"


# ---------- id resolution ----------
def test_resolve_id_explicit_wins(fake_home, monkeypatch):
    server, _ = fake_home
    monkeypatch.setenv("MINDFRAME_ID", "from-env")
    mid, err = server._resolve_id("from-arg")
    assert err is None
    assert mid == "from-arg"


def test_resolve_id_env_fallback(fake_home, monkeypatch):
    server, _ = fake_home
    monkeypatch.setenv("MINDFRAME_ID", "from-env")
    mid, err = server._resolve_id(None)
    assert err is None
    assert mid == "from-env"


def test_resolve_id_from_cwd(fake_home, monkeypatch):
    server, frames = fake_home
    _make_frame(frames, "cwd-id")
    monkeypatch.chdir(frames / "cwd-id")
    mid, err = server._resolve_id(None)
    assert err is None
    assert mid == "cwd-id"


def test_resolve_id_unresolvable_errors(fake_home, tmp_path, monkeypatch):
    server, _ = fake_home
    monkeypatch.chdir(tmp_path)
    mid, err = server._resolve_id(None)
    assert mid is None
    assert err and "Cannot resolve mindframe_id" in err


# ---------- write_block ----------
def test_write_block_appends_one_line(fake_home):
    server, frames = fake_home
    fdir = _make_frame(frames, "frame1")
    res = server.write_block({"type": "text", "markdown": "hello"}, mindframe_id="frame1")
    assert res["ok"]
    assert "id" in res and "ts" in res

    lines = (fdir / "blocks.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["type"] == "text"
    assert rec["markdown"] == "hello"
    assert rec["author"] == "agent"
    assert rec["id"] == res["id"]
    assert uuid.UUID(rec["id"]).version == 7


def test_write_block_multiple_appends_in_order(fake_home):
    server, frames = fake_home
    fdir = _make_frame(frames, "frame1")
    for i in range(5):
        res = server.write_block({"type": "text", "markdown": f"msg-{i}"}, mindframe_id="frame1")
        assert res["ok"]
    lines = (fdir / "blocks.jsonl").read_text().splitlines()
    assert len(lines) == 5
    recs = [json.loads(l) for l in lines]
    assert [r["markdown"] for r in recs] == [f"msg-{i}" for i in range(5)]
    # Ids must sort chronologically.
    ids = [r["id"] for r in recs]
    assert ids == sorted(ids)


def test_write_block_rejects_unknown_type(fake_home):
    server, frames = fake_home
    _make_frame(frames, "frame1")
    res = server.write_block({"type": "made-up", "x": 1}, mindframe_id="frame1")
    assert not res["ok"]
    assert "unknown block type" in res["error"]


def test_write_block_rejects_non_dict(fake_home):
    server, frames = fake_home
    _make_frame(frames, "frame1")
    res = server.write_block("not a dict", mindframe_id="frame1")  # type: ignore[arg-type]
    assert not res["ok"]
    assert "object" in res["error"]


def test_write_block_rejects_missing_type(fake_home):
    server, frames = fake_home
    _make_frame(frames, "frame1")
    res = server.write_block({"markdown": "x"}, mindframe_id="frame1")
    assert not res["ok"]


def test_write_block_errors_on_missing_frame(fake_home):
    server, _ = fake_home
    res = server.write_block({"type": "text", "markdown": "x"}, mindframe_id="ghost")
    assert not res["ok"]
    assert "not found" in res["error"]


def test_write_block_strips_caller_supplied_id_and_author(fake_home):
    server, frames = fake_home
    fdir = _make_frame(frames, "frame1")
    res = server.write_block(
        {"type": "text", "markdown": "x", "id": "fake-id", "author": "user"},
        mindframe_id="frame1",
    )
    assert res["ok"]
    rec = json.loads((fdir / "blocks.jsonl").read_text().splitlines()[0])
    assert rec["id"] != "fake-id"
    assert rec["author"] == "agent"


def test_write_block_updates_meta_last_block_at(fake_home):
    server, frames = fake_home
    fdir = _make_frame(frames, "frame1")
    res = server.write_block({"type": "text", "markdown": "x"}, mindframe_id="frame1")
    assert res["ok"]
    meta = json.loads((fdir / "meta.json").read_text())
    assert meta["last_block_at"] == res["ts"]


def test_write_block_resolves_id_from_cwd(fake_home, monkeypatch):
    server, frames = fake_home
    fdir = _make_frame(frames, "cwd-frame")
    monkeypatch.chdir(fdir)
    res = server.write_block({"type": "text", "markdown": "hi"})
    assert res["ok"]
    lines = (fdir / "blocks.jsonl").read_text().splitlines()
    assert len(lines) == 1


# ---------- set_title ----------
def test_set_title_updates_meta(fake_home):
    server, frames = fake_home
    fdir = _make_frame(frames, "frame1")
    res = server.set_title("New title", mindframe_id="frame1")
    assert res["ok"]
    assert res["title"] == "New title"
    meta = json.loads((fdir / "meta.json").read_text())
    assert meta["title"] == "New title"
    assert meta["id"] == "frame1"  # preserves other fields


def test_set_title_truncates_to_200(fake_home):
    server, frames = fake_home
    _make_frame(frames, "frame1")
    res = server.set_title("x" * 500, mindframe_id="frame1")
    assert res["ok"]
    assert len(res["title"]) == 200


def test_set_title_rejects_empty(fake_home):
    server, frames = fake_home
    _make_frame(frames, "frame1")
    res = server.set_title("   ", mindframe_id="frame1")
    assert not res["ok"]


def test_set_title_errors_on_missing_meta(fake_home):
    server, frames = fake_home
    fdir = frames / "no-meta"
    fdir.mkdir()
    res = server.set_title("x", mindframe_id="no-meta")
    assert not res["ok"]
    assert "meta.json not found" in res["error"]


# ---------- block schema sanity (every known type accepted by validator) ----------
@pytest.mark.parametrize("btype", [
    "text", "code", "image", "url-card", "table", "button-row", "input",
    "summary", "divider", "custom-html", "user-action", "supersedes",
    "redact", "close",
])
def test_validator_accepts_all_known_types(fake_home, btype):
    server, _ = fake_home
    assert server._validate_block({"type": btype}) is None
