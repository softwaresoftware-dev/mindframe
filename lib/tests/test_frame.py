"""Hermetic tests for lib.frame — the core mindframe storage operations."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from lib import frame  # noqa: E402


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Redirect the frames root for the test. Yields the root Path."""
    root = tmp_path / ".mindframe" / "frames"
    root.mkdir(parents=True)
    monkeypatch.setenv("MINDFRAME_FRAMES_ROOT", str(root))
    monkeypatch.delenv("MINDFRAME_ID", raising=False)
    return root


# ---------- mint_id ----------


def test_mint_id_length():
    assert len(frame.mint_id()) == 10


def test_mint_id_length_param():
    assert len(frame.mint_id(20)) == 20


def test_mint_id_uses_base62_alphabet():
    valid = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    for _ in range(100):
        assert set(frame.mint_id()) <= valid


def test_mint_id_collisions_extremely_rare():
    """100k mint_ids should be unique. 62^10 ≈ 8e17 so collision probability
    in 100k draws is roughly 1e-8."""
    ids = {frame.mint_id() for _ in range(100_000)}
    assert len(ids) == 100_000


# ---------- uuid7 ----------


def test_uuid7_version():
    parsed = uuid.UUID(frame.uuid7())
    assert parsed.version == 7


def test_uuid7_chronological_string_sort():
    import time
    ids = []
    for _ in range(10):
        ids.append(frame.uuid7())
        time.sleep(0.002)
    assert ids == sorted(ids)


# ---------- create_frame ----------


def test_create_frame_minimal(fake_root):
    out = frame.create_frame("My first mindframe")
    assert out["id"] and len(out["id"]) == 10
    assert Path(out["frame_dir"]).is_dir()
    assert Path(out["frame_dir"]).name == out["id"]
    assert out["url"].endswith(f"/m/{out['id']}")
    meta = json.loads((Path(out["frame_dir"]) / "meta.json").read_text())
    assert meta["id"] == out["id"]
    assert meta["title"] == "My first mindframe"
    assert meta["status"] == "active"
    assert meta["agent_session"] == out["id"]
    assert meta["spawned_by"] == {"kind": "manual"}


def test_create_frame_writes_default_seed_block(fake_root):
    out = frame.create_frame("Seeded")
    blocks_path = Path(out["frame_dir"]) / "blocks.jsonl"
    lines = blocks_path.read_text().splitlines()
    assert len(lines) == 1
    seed = json.loads(lines[0])
    assert seed["type"] == "summary"
    assert seed["tone"] == "info"
    assert seed["author"] == "system"
    assert seed["id"] == out["seed_block_id"]


def test_create_frame_writes_custom_seed_block(fake_root):
    seed = {"type": "text", "markdown": "## starting investigation"}
    out = frame.create_frame("Custom seed", seed_block=seed)
    lines = (Path(out["frame_dir"]) / "blocks.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["type"] == "text"
    assert rec["markdown"] == "## starting investigation"
    assert rec["author"] == "system"


def test_create_frame_with_explicit_id(fake_root):
    out = frame.create_frame("Deterministic", mindframe_id="my-test-frame-1")
    assert out["id"] == "my-test-frame-1"
    assert (fake_root / "my-test-frame-1" / "meta.json").is_file()


def test_create_frame_rejects_existing_id(fake_root):
    frame.create_frame("Original", mindframe_id="dup")
    with pytest.raises(FileExistsError):
        frame.create_frame("Other", mindframe_id="dup")


def test_create_frame_rejects_empty_title(fake_root):
    with pytest.raises(ValueError):
        frame.create_frame("   ")


def test_create_frame_rejects_invalid_seed_block(fake_root):
    with pytest.raises(ValueError):
        frame.create_frame("Bad seed", seed_block={"type": "not-a-real-type"})


def test_create_frame_truncates_long_title(fake_root):
    out = frame.create_frame("x" * 500)
    meta = json.loads((Path(out["frame_dir"]) / "meta.json").read_text())
    assert len(meta["title"]) == 200


def test_create_frame_with_spawned_by(fake_root):
    sb = {"kind": "dispatcher-event", "source": "sentry", "event_id": "abc-123"}
    out = frame.create_frame("Triage", spawned_by=sb)
    meta = json.loads((Path(out["frame_dir"]) / "meta.json").read_text())
    assert meta["spawned_by"] == sb


def test_create_frame_with_tags(fake_root):
    out = frame.create_frame("Tagged", tags=["incident", "payments"])
    meta = json.loads((Path(out["frame_dir"]) / "meta.json").read_text())
    assert meta["tags"] == ["incident", "payments"]


def test_create_frame_url_uses_public_url_env(fake_root, monkeypatch):
    monkeypatch.setenv("MINDFRAME_PUBLIC_URL", "https://mindframe.acme.com")
    out = frame.create_frame("Public")
    assert out["url"] == f"https://mindframe.acme.com/m/{out['id']}"


def test_create_frame_directory_is_chmod_700(fake_root):
    out = frame.create_frame("Permission check")
    mode = oct(Path(out["frame_dir"]).stat().st_mode & 0o777)
    assert mode == "0o700"


def test_create_frame_with_explicit_root(tmp_path):
    """When `root` arg is passed, MINDFRAME_FRAMES_ROOT env is ignored."""
    explicit = tmp_path / "explicit-root"
    out = frame.create_frame("Explicit", root=explicit)
    assert Path(out["frame_dir"]).parent == explicit


# ---------- append_block (now in lib.frame, was in MCP) ----------


def test_append_block_via_lib(fake_root):
    out = frame.create_frame("Append test", mindframe_id="apt")
    fdir = Path(out["frame_dir"])
    rec = frame.append_block(fdir, {"type": "text", "markdown": "hello"})
    assert rec["type"] == "text"
    assert rec["author"] == "agent"
    assert uuid.UUID(rec["id"]).version == 7
    # 2 lines: seed + ours
    assert len(list(open(fdir / "blocks.jsonl"))) == 2


def test_append_block_errors_on_missing_frame(fake_root):
    with pytest.raises(FileNotFoundError):
        frame.append_block(fake_root / "ghost", {"type": "text", "markdown": "x"})


def test_set_title_via_lib(fake_root):
    out = frame.create_frame("Original title")
    fdir = Path(out["frame_dir"])
    new = frame.set_title(fdir, "Renamed")
    assert new == "Renamed"
    assert json.loads((fdir / "meta.json").read_text())["title"] == "Renamed"


def test_set_title_rejects_empty(fake_root):
    out = frame.create_frame("X")
    with pytest.raises(ValueError):
        frame.set_title(Path(out["frame_dir"]), "  ")
