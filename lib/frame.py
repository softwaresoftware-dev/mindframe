"""Core mindframe storage operations — shared between the MCP, the spawn
helper, the dashboard, and any other caller that needs to touch frames.

A frame is a directory at <FRAMES_ROOT>/<id>/ containing:
  meta.json     — id, title, status, spawned_by, agent_session, timestamps, tags
  blocks.jsonl  — append-only block stream (one JSON object per line)
  custom/       — optional sibling files (custom-html sources, images, etc.)

All writes use exclusive file locks (POSIX flock / Windows msvcrt.locking)
so multiple writers (MCP from the agent, spawn helper at create time, the
dashboard server on user actions) can safely append concurrently.

This module never imports MCP or HTTP machinery — it's pure stdlib so any
process (CLI, daemon, test) can use it.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX
    _LOCK_KIND = "posix"
except ImportError:
    import msvcrt  # Windows
    _LOCK_KIND = "windows"


# --------------------------- paths ---------------------------


def frames_root() -> Path:
    """The root of every mindframe — `$MINDFRAME_FRAMES_ROOT` or
    `~/.mindframe/frames`. Resolved at call time so test fixtures can swap
    `$HOME` and have the change take effect."""
    override = os.environ.get("MINDFRAME_FRAMES_ROOT")
    return Path(override) if override else Path.home() / ".mindframe" / "frames"


def frame_dir(mindframe_id: str) -> Path:
    return frames_root() / mindframe_id


def public_url_base() -> str:
    """Base URL the dashboard serves on. Used to build mindframe_url returned
    from create_frame. Defaults to localhost when unset."""
    return os.environ.get("MINDFRAME_PUBLIC_URL", "http://127.0.0.1:5174").rstrip("/")


# --------------------------- ids ---------------------------


# Lowercase-only base36 alphabet. Lowercase is load-bearing: the frame_id
# threads through taskpilot's slugify (which lowercases) as the task name,
# and session-bridge mesh routing on button-click events keys off that
# name. Mixed-case ids cause the frame dir name to drift from the mesh
# address, breaking the "continue" path. 36^10 ≈ 3.7 quadrillion, plenty
# of collision headroom for any single deployment.
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def mint_id(length: int = 10) -> str:
    """A short URL-safe id for a mindframe. Lowercase base36 so the id
    survives slugify unchanged.

    NOT chronological — `mint_id()` calls in sequence produce unrelated ids.
    Block ids inside the frame use UUIDv7 for chronological sort; the frame
    id itself just needs to be unique, URL-friendly, and slugify-stable.
    """
    return "".join(_BASE36[b % 36] for b in secrets.token_bytes(length))


import threading

_uuid7_state = {"last_ms": 0, "counter": 0}
_uuid7_lock = threading.Lock()


def uuid7() -> str:
    """RFC 9562 UUIDv7 with per-process monotonicity within the same
    millisecond. Uses stdlib `uuid.uuid7()` on Python 3.14+; otherwise a
    polyfill that uses the 12-bit rand_a field as a sub-ms counter.

    Why monotonicity matters: block ids drive the SSE `?since=<id>` cursor
    and the on-disk JSONL ordering. If two writes land in the same ms and
    sort backwards, `?since=<id>` returns the wrong tail and the SSE stream
    drops blocks. This bit us on macOS CI where the polling loop in
    stub_spawner.py wrote 3 blocks in <1ms.
    """
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    with _uuid7_lock:
        ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
        if ms <= _uuid7_state["last_ms"]:
            # Same-ms or clock-stall: keep the timestamp, bump the counter.
            # 12 bits of rand_a give 4096 unique ids per ms — far more than
            # any realistic single-process write rate.
            ms = _uuid7_state["last_ms"]
            _uuid7_state["counter"] += 1
            if _uuid7_state["counter"] > 0xFFF:
                # Counter overflowed — bump ms forward and reset.
                ms += 1
                _uuid7_state["counter"] = 0
        else:
            _uuid7_state["counter"] = 0
        _uuid7_state["last_ms"] = ms
        rand_a = _uuid7_state["counter"] & 0xFFF      # 12 bits, monotonic
        rand_b = secrets.randbits(62)                 # 62 bits random
        int_uuid = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
        return str(uuid.UUID(int=int_uuid))


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------- file lock ---------------------------


def _lock_exclusive(fh) -> bool:
    """Acquire an exclusive lock on the file. Returns True if held.

    POSIX flock is reliable. Windows msvcrt.locking raises OSError when
    locking a byte range past EOF (which is what happens on a newly
    appended-to-empty file: position is 0, EOF is 0). For mindframe's
    actual concurrency model — one agent writes to one frame at a time —
    skipping the lock on Windows when it fails is safe: appends of a
    single JSON line (< PIPE_BUF bytes) are atomic at the OS level.

    Multi-writer correctness on Windows is not a property we promise; the
    block-stream API spec calls out one writer per mindframe by design.
    """
    if _LOCK_KIND == "posix":
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return True
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        return True
    except OSError:
        return False


def _unlock(fh, locked: bool) -> None:
    if not locked:
        return
    if _LOCK_KIND == "posix":
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    else:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


# --------------------------- block schema ---------------------------


KNOWN_BLOCK_TYPES = {
    "text", "code", "image", "url-card", "table", "button-row", "input",
    "summary", "divider", "custom-html", "user-action", "supersedes",
    "redact", "close",
}


def validate_block(block: object) -> str | None:
    """Returns None if the block looks well-formed, otherwise a human-readable
    error string. Only checks the envelope — type-specific field validation
    is up to the renderer (lenient parsing on read)."""
    if not isinstance(block, dict):
        return "block must be an object"
    btype = block.get("type")
    if not isinstance(btype, str):
        return "block.type must be a string"
    if btype not in KNOWN_BLOCK_TYPES:
        return f"unknown block type: {btype!r}"
    return None


# --------------------------- block append ---------------------------


def append_block(fdir: Path, block: dict, *, author: str = "agent") -> dict:
    """Append one block to <fdir>/blocks.jsonl, filling in id/ts/author.

    Strips any caller-supplied id/ts/author from the input. Returns the
    fully-populated block dict. Raises FileNotFoundError if the frame
    directory doesn't exist (caller should mkdir + meta.json first via
    create_frame).
    """
    if not fdir.is_dir():
        raise FileNotFoundError(f"frame directory not found: {fdir}")

    record: dict[str, Any] = {
        "id": uuid7(),
        "ts": now_ms(),
        "author": author,
        **{k: v for k, v in block.items() if k not in ("id", "ts", "author")},
        "type": block["type"],
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"

    blocks_path = fdir / "blocks.jsonl"
    with open(blocks_path, "ab") as fh:
        locked = _lock_exclusive(fh)
        try:
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            _unlock(fh, locked)

    _touch_meta_last_block(fdir, record["ts"])
    return record


# --------------------------- meta.json ---------------------------


def read_meta(fdir: Path) -> dict[str, Any]:
    meta_path = fdir / "meta.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def write_meta(fdir: Path, meta: dict[str, Any]) -> None:
    """Atomic-ish meta write under flock. Callers should read_meta, mutate,
    then write_meta to avoid clobbering concurrent updates."""
    meta_path = fdir / "meta.json"
    with open(meta_path, "wb") as fh:
        locked = _lock_exclusive(fh)
        try:
            fh.write(json.dumps(meta, indent=2).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            _unlock(fh, locked)


def update_meta(fdir: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Read-modify-write meta.json under flock. Returns the merged result."""
    meta_path = fdir / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"meta.json not found at {meta_path}")
    with open(meta_path, "r+b") as fh:
        locked = _lock_exclusive(fh)
        try:
            fh.seek(0)
            meta = json.loads(fh.read() or b"{}")
            meta.update(patch)
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(meta, indent=2).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            _unlock(fh, locked)
    return meta


def set_title(fdir: Path, title: str) -> str:
    """Update the mindframe's title. Truncated to 200 chars."""
    title = (title or "").strip()
    if not title:
        raise ValueError("title must be non-empty")
    title = title[:200]
    update_meta(fdir, {"title": title})
    return title


def _touch_meta_last_block(fdir: Path, ts: int) -> None:
    """Best-effort update of meta.json.last_block_at — silent on failure.
    The dashboard's frames listing reads file mtime as a fallback, so a
    missed update isn't fatal."""
    meta_path = fdir / "meta.json"
    if not meta_path.is_file():
        return
    try:
        update_meta(fdir, {"last_block_at": ts})
    except Exception:
        pass


# --------------------------- create_frame ---------------------------


DEFAULT_SEED_BLOCK = {
    "type": "summary",
    "tone": "info",
    "title": "Starting up",
    "body": "The agent is loading context and will write blocks as it works.",
}


def create_frame(
    title: str,
    *,
    seed_block: dict | None = None,
    spawned_by: dict | None = None,
    tags: list[str] | None = None,
    mindframe_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Synchronously create a new mindframe — directory, meta.json, seed block.

    Returns:
        {
          "id": "<10-char base62>",
          "frame_dir": "/abs/path",
          "url": "<MINDFRAME_PUBLIC_URL>/m/<id>",
          "meta": <the meta.json contents>,
          "seed_block_id": "<uuid7>",
        }

    Why synchronously? Two reasons callers always need:
      (1) The spawned agent's `write_block` calls would fail if the frame
          directory didn't exist yet. mkdir-before-launch removes the race.
      (2) Agent startup is ~16s. The seed block guarantees the operator's
          first /m/<id> page-load shows something coherent rather than blank.

    `mindframe_id` is optional — when None, mint_id() generates one. Passing
    one is useful for tests and for deterministic re-creation.

    `root` overrides $MINDFRAME_FRAMES_ROOT for tests.
    """
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")

    mid = mindframe_id or mint_id()
    base = root or frames_root()
    fdir = base / mid
    if fdir.exists():
        raise FileExistsError(f"frame directory already exists: {fdir}")

    # 700 on the parent + the frame — mindframes can contain sensitive content
    # (logs, names, customer refs). Match the existing chmod on ~/.mindframe/secrets/.
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    fdir.mkdir(mode=0o700)
    (fdir / "custom").mkdir(mode=0o755)

    now = now_ms()
    meta: dict[str, Any] = {
        "id": mid,
        "title": title[:200],
        "status": "active",
        "agent_session": mid,            # convention: task name == mindframe id
        "created_at": now,
        "last_block_at": now,
        "spawned_by": spawned_by or {"kind": "manual"},
        "tags": list(tags or []),
        "pinned": False,
    }
    write_meta(fdir, meta)

    # Seed block — flock'd append, same path as agent writes.
    (fdir / "blocks.jsonl").touch()
    seed = dict(seed_block or DEFAULT_SEED_BLOCK)
    seed_err = validate_block(seed)
    if seed_err:
        raise ValueError(f"seed_block invalid: {seed_err}")
    written = append_block(fdir, seed, author="system")

    return {
        "id": mid,
        "frame_dir": str(fdir),
        "url": f"{public_url_base()}/m/{mid}",
        "meta": meta,
        "seed_block_id": written["id"],
    }
