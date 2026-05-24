"""CLI wrapper around lib.frame.create_frame — so callers in other processes
(dispatcher, scripts, etc.) can mint a frame without importing mindframe code.

Usage:

    python3 -m lib.spawn \\
        --title "OOMKilled in payments-api" \\
        --spawned-by-json '{"kind": "dispatcher-event", "source": "sentry"}' \\
        --seed-block-json '{"type": "summary", "tone": "info", "title": "...", "body": "..."}'

Emits one JSON object on stdout:

    {"ok": true, "id": "abc1234567", "frame_dir": "...", "url": "...", "seed_block_id": "..."}

…or, on failure:

    {"ok": false, "error": "..."}

Exit code is 0 on success, 1 on failure. Stderr is human-readable diagnostics
only — never the JSON envelope.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import frame  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Create a new mindframe")
    p.add_argument("--title", required=True, help="Human-readable mindframe title")
    p.add_argument(
        "--seed-block-json",
        default="",
        help='Seed block JSON (e.g. \'{"type":"summary","tone":"info","title":"...","body":"..."}\'). '
             "Defaults to a generic 'Starting up' summary if omitted.",
    )
    p.add_argument(
        "--spawned-by-json",
        default="",
        help='Spawned_by metadata JSON (e.g. \'{"kind":"dispatcher-event","source":"sentry","event_id":"..."}\'). '
             "Defaults to {kind: manual}.",
    )
    p.add_argument(
        "--tags",
        default="",
        help="Comma-separated tags to attach to meta.json",
    )
    p.add_argument(
        "--id",
        default="",
        help="Explicit mindframe id (for tests / deterministic recreation). "
             "Default: mint a fresh 10-char base62 id.",
    )
    args = p.parse_args(argv)

    seed_block = None
    if args.seed_block_json:
        try:
            seed_block = json.loads(args.seed_block_json)
        except json.JSONDecodeError as e:
            return _fail(f"--seed-block-json parse error: {e}")
        if not isinstance(seed_block, dict):
            return _fail("--seed-block-json must be a JSON object")

    spawned_by = None
    if args.spawned_by_json:
        try:
            spawned_by = json.loads(args.spawned_by_json)
        except json.JSONDecodeError as e:
            return _fail(f"--spawned-by-json parse error: {e}")
        if not isinstance(spawned_by, dict):
            return _fail("--spawned-by-json must be a JSON object")

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None

    try:
        result = frame.create_frame(
            title=args.title,
            seed_block=seed_block,
            spawned_by=spawned_by,
            tags=tags,
            mindframe_id=args.id or None,
        )
    except (ValueError, FileExistsError) as e:
        return _fail(str(e))
    except OSError as e:
        return _fail(f"filesystem error: {e}")

    print(json.dumps({
        "ok": True,
        "id": result["id"],
        "frame_dir": result["frame_dir"],
        "url": result["url"],
        "seed_block_id": result["seed_block_id"],
    }))
    return 0


def _fail(msg: str) -> int:
    print(json.dumps({"ok": False, "error": msg}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
