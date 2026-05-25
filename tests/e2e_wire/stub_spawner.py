"""Stub for taskpilot's spawner_cli.

Dispatcher subprocess-invokes whatever TASKPILOT_SPAWNER_CLI points at, with
the same CLI surface as real taskpilot. For the Tier 1 wire tests we don't
want to launch a real tmux + claude session — we just want to prove that
dispatcher minted the frame, called the spawner, and the spawner can write
blocks back into the frame.

This stub:
  - Parses --name / --cwd / --enabled-plugins / --enabled-mcps / --brief.
  - Writes 3 synthetic blocks (summary, text, close) to the frame's
    blocks.jsonl using lib.frame.append_block — same code path the real
    mindframe MCP uses.
  - Emits a JSON envelope on stdout matching taskpilot's real shape so the
    dispatcher accepts it.

Exit code 0 on success, 1 with {"ok": false, "error": "..."} on failure.

The recipe used in tests names this script as TASKPILOT_SPAWNER_CLI; nothing
else points here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# lib/ is two levels up: tests/e2e_wire → tests → mindframe
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT))
from lib import frame  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("description", nargs="?", default="")
    p.add_argument("--name", required=True)
    p.add_argument("--cwd", default="")
    p.add_argument("--enabled-plugins", default="")
    p.add_argument("--enabled-mcps", default="")
    p.add_argument("--channels", default="")
    p.add_argument("--model", default="")
    p.add_argument("--brief", default="")
    args = p.parse_args(argv)

    cwd = args.cwd or os.environ.get("HOME", "")
    fdir = Path(cwd)
    if not fdir.is_dir():
        print(json.dumps({"ok": False, "error": f"stub-spawner: cwd not a directory: {fdir}"}))
        return 1

    # Three deterministic blocks so tests can assert exact counts.
    blocks = [
        {"type": "summary", "tone": "info", "title": "Stub agent up", "body": "Test stub spawner invoked."},
        {"type": "text", "markdown": f"Task name: **{args.name}**\nCwd: `{cwd}`"},
        {"type": "close", "reason": "Stub agent done.", "links": []},
    ]
    for b in blocks:
        frame.append_block(fdir, b, author="agent")

    # Mirror taskpilot's success envelope.
    print(json.dumps({
        "ok": True,
        "task_id": args.name,
        "tmux_session": args.name,
        "channel_healthy": True,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
