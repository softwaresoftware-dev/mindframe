"""mindframe MCP — agents author block-stream conversations through these tools.

Two tools:

  write_block(block, mindframe_id=None)  -> append one block to ~/.mindframe/frames/<id>/blocks.jsonl
  set_title(title, mindframe_id=None)    -> update meta.json title

mindframe_id resolution order: explicit arg, $MINDFRAME_ID env, cwd under
~/.mindframe/frames/<id>/. The spawned-agent convention is cwd=frame dir, so
the agent calls write_block({"type": "text", ...}) with no id and it just works.

stdio transport: stdout is the MCP channel — all logging goes to stderr.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

try:
    import fcntl  # POSIX
    _LOCK_KIND = "posix"
except ImportError:
    import msvcrt  # Windows
    _LOCK_KIND = "windows"

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
log = logging.getLogger("mindframe-mcp")

FRAMES_ROOT = Path.home() / ".mindframe" / "frames"

mcp = FastMCP("mindframe")


# ---------- uuid7 (RFC 9562) ----------
# Polyfill for Python <3.14. When stdlib gains uuid.uuid7(), prefer it.
def _uuid7() -> str:
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
    rand_a = secrets.randbits(12)  # 12 bits
    rand_b = secrets.randbits(62)  # 62 bits
    # Assemble per RFC 9562 §5.7
    int_uuid = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=int_uuid))


# ---------- file lock ----------
def _lock_exclusive(fh) -> None:
    if _LOCK_KIND == "posix":
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    else:
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)


def _unlock(fh) -> None:
    if _LOCK_KIND == "posix":
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    else:
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


# ---------- id resolution ----------
def _resolve_id(explicit: str | None) -> tuple[str | None, str | None]:
    """Returns (mindframe_id, error). Exactly one is None."""
    if explicit:
        return explicit, None
    env_id = os.environ.get("MINDFRAME_ID")
    if env_id:
        return env_id, None
    cwd = Path.cwd().resolve()
    try:
        rel = cwd.relative_to(FRAMES_ROOT.resolve())
        first = rel.parts[0] if rel.parts else None
        if first:
            return first, None
    except ValueError:
        pass
    return None, (
        "Cannot resolve mindframe_id. Pass mindframe_id explicitly, set "
        "$MINDFRAME_ID, or run from ~/.mindframe/frames/<id>/."
    )


def _frame_dir(mindframe_id: str) -> Path:
    return FRAMES_ROOT / mindframe_id


# ---------- block validation ----------
KNOWN_BLOCK_TYPES = {
    "text", "code", "image", "url-card", "table", "button-row", "input",
    "summary", "divider", "custom-html", "user-action", "supersedes",
    "redact", "close",
}


def _validate_block(block: dict) -> str | None:
    if not isinstance(block, dict):
        return "block must be an object"
    btype = block.get("type")
    if not isinstance(btype, str):
        return "block.type must be a string"
    if btype not in KNOWN_BLOCK_TYPES:
        return f"unknown block type: {btype!r} (known: {sorted(KNOWN_BLOCK_TYPES)})"
    return None


# ---------- tools ----------
@mcp.tool()
def write_block(block: dict, mindframe_id: str | None = None) -> dict:
    """Append one block to the mindframe's block stream.

    Pass the type-specific fields only; the server fills in id (UUIDv7), ts
    (epoch ms), and author ('agent'). See docs/mindframe-block-stream-api.md
    for the block schema (text, code, table, button-row, summary, divider,
    custom-html, supersedes, redact, close, etc.).

    Examples:
      write_block({"type": "text", "markdown": "**Hello**"})
      write_block({"type": "summary", "tone": "ok", "title": "Done", "body": "..."})
      write_block({"type": "table", "headers": ["a","b"], "rows": [["1","2"]]})

    Resolves mindframe_id from the argument, $MINDFRAME_ID env, or cwd.
    Returns {ok, id, ts} on success, {ok: false, error} on failure.
    """
    err = _validate_block(block)
    if err:
        return {"ok": False, "error": err}

    mid, id_err = _resolve_id(mindframe_id)
    if id_err:
        return {"ok": False, "error": id_err}

    fdir = _frame_dir(mid)
    if not fdir.is_dir():
        return {"ok": False, "error": f"mindframe directory not found: {fdir}"}

    record = {
        "id": _uuid7(),
        "ts": int(time.time() * 1000),
        "author": "agent",
        **{k: v for k, v in block.items() if k not in ("id", "ts", "author")},
        "type": block["type"],  # ensure type is present even after the comprehension
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"

    blocks_path = fdir / "blocks.jsonl"
    try:
        with open(blocks_path, "ab") as fh:
            _lock_exclusive(fh)
            try:
                fh.write(line.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                _unlock(fh)
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}"}

    # Update meta.json's last_block_at (best-effort; the tailer is authoritative).
    _touch_meta_last_block(fdir, record["ts"])

    return {"ok": True, "id": record["id"], "ts": record["ts"]}


@mcp.tool()
def set_title(title: str, mindframe_id: str | None = None) -> dict:
    """Update the mindframe's title in meta.json.

    Truncated to 200 chars server-side. Resolves mindframe_id the same way
    as write_block.
    """
    if not isinstance(title, str) or not title.strip():
        return {"ok": False, "error": "title must be a non-empty string"}
    title = title[:200]

    mid, id_err = _resolve_id(mindframe_id)
    if id_err:
        return {"ok": False, "error": id_err}

    fdir = _frame_dir(mid)
    meta_path = fdir / "meta.json"
    if not meta_path.is_file():
        return {"ok": False, "error": f"meta.json not found at {meta_path}"}

    try:
        with open(meta_path, "r+b") as fh:
            _lock_exclusive(fh)
            try:
                fh.seek(0)
                meta = json.loads(fh.read() or b"{}")
                meta["title"] = title
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(meta, indent=2).encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                _unlock(fh)
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"set_title failed: {e}"}

    return {"ok": True, "title": title}


def _touch_meta_last_block(fdir: Path, ts: int) -> None:
    """Best-effort update of meta.json.last_block_at. Silent on failure —
    the tailer reconciles drift."""
    meta_path = fdir / "meta.json"
    if not meta_path.is_file():
        return
    try:
        with open(meta_path, "r+b") as fh:
            _lock_exclusive(fh)
            try:
                fh.seek(0)
                meta = json.loads(fh.read() or b"{}")
                meta["last_block_at"] = ts
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(meta, indent=2).encode("utf-8"))
                fh.flush()
            finally:
                _unlock(fh)
    except Exception:
        pass


if __name__ == "__main__":
    mcp.run()
