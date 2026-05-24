"""mindframe MCP — agents author block-stream conversations through these tools.

Two tools:

  write_block(block, mindframe_id=None)  -> append one block to <frame>/blocks.jsonl
  set_title(title, mindframe_id=None)    -> update meta.json title

mindframe_id resolution order: explicit arg, $MINDFRAME_ID env, cwd under
$MINDFRAME_FRAMES_ROOT (default ~/.mindframe/frames/). The spawn convention
is cwd=frame dir, so the agent calls write_block({"type": "text", ...}) with
no id and it just works.

stdio transport: stdout is the MCP channel — all logging goes to stderr.

All file-writes go through lib.frame so the MCP, the spawn helper, and the
dashboard share one append code path.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Make the sibling lib/ importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import frame  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
log = logging.getLogger("mindframe-mcp")

mcp = FastMCP("mindframe")


def _resolve_id(explicit: str | None) -> tuple[str | None, str | None]:
    """Returns (mindframe_id, error). Exactly one is None.

    Order: explicit arg → $MINDFRAME_ID env → cwd inside frames_root.
    """
    if explicit:
        return explicit, None
    env_id = os.environ.get("MINDFRAME_ID")
    if env_id:
        return env_id, None
    cwd = Path.cwd().resolve()
    try:
        rel = cwd.relative_to(frame.frames_root().resolve())
        first = rel.parts[0] if rel.parts else None
        if first:
            return first, None
    except ValueError:
        pass
    return None, (
        "Cannot resolve mindframe_id. Pass mindframe_id explicitly, set "
        "$MINDFRAME_ID, or run from within a frame directory."
    )


@mcp.tool()
def write_block(block: dict, mindframe_id: str | None = None) -> dict:
    """Append one block to the mindframe's block stream.

    Pass the type-specific fields only; the server fills in id (UUIDv7),
    ts (epoch ms), and author ('agent'). See docs/mindframe-block-stream-api.md
    for the block schema (text, code, table, button-row, summary, divider,
    custom-html, supersedes, redact, close, etc.).

    Examples:
      write_block({"type": "text", "markdown": "**Hello**"})
      write_block({"type": "summary", "tone": "ok", "title": "Done", "body": "..."})
      write_block({"type": "table", "headers": ["a","b"], "rows": [["1","2"]]})

    Resolves mindframe_id from the argument, $MINDFRAME_ID env, or cwd.
    Returns {ok, id, ts} on success, {ok: false, error} on failure.
    """
    err = frame.validate_block(block)
    if err:
        return {"ok": False, "error": err}

    mid, id_err = _resolve_id(mindframe_id)
    if id_err:
        return {"ok": False, "error": id_err}

    fdir = frame.frame_dir(mid)
    if not fdir.is_dir():
        return {"ok": False, "error": f"mindframe directory not found: {fdir}"}

    try:
        record = frame.append_block(fdir, block, author="agent")
    except (OSError, FileNotFoundError) as e:
        return {"ok": False, "error": f"write failed: {e}"}

    return {"ok": True, "id": record["id"], "ts": record["ts"]}


@mcp.tool()
def set_title(title: str, mindframe_id: str | None = None) -> dict:
    """Update the mindframe's title in meta.json.

    Truncated to 200 chars server-side. Resolves mindframe_id the same way
    as write_block.
    """
    if not isinstance(title, str) or not title.strip():
        return {"ok": False, "error": "title must be a non-empty string"}

    mid, id_err = _resolve_id(mindframe_id)
    if id_err:
        return {"ok": False, "error": id_err}

    fdir = frame.frame_dir(mid)
    if not fdir.is_dir():
        return {"ok": False, "error": f"mindframe directory not found: {fdir}"}

    try:
        new_title = frame.set_title(fdir, title)
    except (OSError, ValueError, FileNotFoundError) as e:
        return {"ok": False, "error": f"set_title failed: {e}"}

    return {"ok": True, "title": new_title}


# Back-compat shims for the existing mcp test suite — those tests call
# `server._uuid7`, `server._resolve_id`, `server._validate_block`, etc.
# Keep them as thin re-exports so old tests continue to assert on the same
# surface.
_uuid7 = frame.uuid7
_validate_block = frame.validate_block
KNOWN_BLOCK_TYPES = frame.KNOWN_BLOCK_TYPES
FRAMES_ROOT = frame.frames_root()


if __name__ == "__main__":
    mcp.run()
