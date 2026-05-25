#!/usr/bin/env python3
"""Tier 2 — real-agent smoke test.

Fires the mindframe-poc recipe at the **live local dispatcher**. Spawns an
actual claude session via taskpilot. Polls until the agent has written at
least N blocks (default 5) or a timeout (default 90s) elapses, then asserts
the wire delivered them through SSE.

Burns real Claude tokens. Requires the live bundle daemons to be running:
  - dispatcher-ingress.service (127.0.0.1:8911)
  - dashboard           (127.0.0.1:5174)
  - taskpilot-daemon    (127.0.0.1:8912)
  - session-bridge      (127.0.0.1:8910)

Idempotent: each run uses a fresh event_id, producing a new mindframe.

Pass/fail criteria:
  PASS — at least MIN_BLOCKS blocks appeared in the frame, SSE replayed all
         of them, agent reached at least one `text` or `table` block (proves
         real content authored, not just the seed).
  FAIL — dispatcher rejected the event, no frame materialized, agent stalled
         below MIN_BLOCKS, or SSE replay didn't match the on-disk file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULTS = {
    "dispatcher_url": "http://127.0.0.1:8911",
    "dashboard_url": "http://127.0.0.1:5174",
    "bearer_file": str(Path.home() / ".mindframe" / "secrets" / "dispatcher-bearer.token"),
    "frames_root": str(Path.home() / ".mindframe" / "frames"),
    "recipe_event_type": "infra-survey",
    "recipe_source": "manual",
    "min_blocks": 5,
    "timeout_s": 90,
    "min_content_types": {"text", "table"},
}


# ---------- pretty output ----------


def green(s): return f"\033[32m{s}\033[0m"
def red(s): return f"\033[31m{s}\033[0m"
def dim(s): return f"\033[90m{s}\033[0m"


def step(msg):
    print(f"  {dim('•')} {msg}", flush=True)


def ok(msg):
    print(f"  {green('✓')} {msg}", flush=True)


def fail(msg, code=1):
    print(f"  {red('✗')} {msg}", flush=True)
    sys.exit(code)


# ---------- HTTP helpers ----------


def _post(url, body, bearer=None, timeout=10):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


# ---------- preflight ----------


def preflight(args) -> str:
    step("preflight: checking daemons reachable")
    try:
        h = _get_json(f"{args.dispatcher_url}/api/health")
        assert h.get("ok"), h
    except Exception as e:
        fail(f"dispatcher /api/health unreachable: {e}")
    try:
        h = _get_json(f"{args.dashboard_url}/api/health")
        assert h.get("ok"), h
    except Exception as e:
        fail(f"dashboard /api/health unreachable: {e}")

    bearer_path = Path(args.bearer_file)
    if not bearer_path.is_file():
        fail(f"bearer file not found at {bearer_path}")
    bearer = bearer_path.read_text().strip()
    if not bearer:
        fail("bearer file is empty")
    ok(f"daemons reachable; bearer loaded from {bearer_path}")
    return bearer


# ---------- main flow ----------


def fire_event(args, bearer) -> str:
    event_id = f"tier2-{int(time.time())}"
    step(f"firing event: source={args.recipe_source} event_type={args.recipe_event_type} event_id={event_id}")
    res = _post(
        f"{args.dispatcher_url}/api/event",
        {"source": args.recipe_source, "event_type": args.recipe_event_type, "data": {"event_id": event_id}},
        bearer=bearer,
    )
    if not res.get("ok"):
        fail(f"dispatcher rejected the event: {res}")
    if res.get("mode") not in ("static-spawn", "auto"):
        fail(f"unexpected dispatch mode: {res}")
    ok(f"event accepted: routed_to={res.get('routed_to')}")
    return event_id


def wait_for_frame(args, event_id, timeout_s) -> Path:
    """Poll until a frame appears with the matching spawned_by.event_id."""
    step(f"waiting for frame matching event_id={event_id} (frame mint should be ~immediate)")
    deadline = time.time() + 15  # frame creation is fast; longer timeout for the agent comes later
    frames_root = Path(args.frames_root)
    while time.time() < deadline:
        for fdir in frames_root.iterdir():
            meta_path = fdir / "meta.json"
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                continue
            spawned_by = meta.get("spawned_by", {})
            if spawned_by.get("event_id") == event_id:
                ok(f"frame found: id={fdir.name} title={meta.get('title', '(untitled)')!r}")
                return fdir
        time.sleep(0.5)
    fail(f"no frame appeared within 15s for event_id={event_id}")


def wait_for_blocks(args, fdir, min_blocks, timeout_s):
    """Poll the frame's blocks.jsonl. Return when min_blocks reached or timeout."""
    step(f"waiting for ≥{min_blocks} blocks (timeout {timeout_s}s)")
    blocks_path = fdir / "blocks.jsonl"
    deadline = time.time() + timeout_s
    last_count = -1
    while time.time() < deadline:
        if not blocks_path.is_file():
            time.sleep(0.5)
            continue
        try:
            lines = [l for l in blocks_path.read_text().splitlines() if l.strip()]
        except OSError:
            time.sleep(0.5)
            continue
        if len(lines) != last_count:
            step(f"  block count: {len(lines)}")
            last_count = len(lines)
        if len(lines) >= min_blocks:
            ok(f"reached {len(lines)} blocks")
            return [json.loads(l) for l in lines]
        time.sleep(1.0)
    # Timeout — return what we got for diagnostics.
    fail(f"only {last_count} blocks within {timeout_s}s; agent didn't narrate enough (need {min_blocks})")


def assert_content_types(blocks, min_types):
    types = {b.get("type") for b in blocks}
    overlap = types & set(min_types)
    if not overlap:
        fail(f"agent never wrote any of {sorted(min_types)} blocks; only saw {sorted(types)}. Probably "
             f"didn't actually do real work — seed + a couple of summaries.")
    ok(f"saw content types: {sorted(types & set(min_types))} (out of {sorted(types)})")


def assert_sse_matches(args, fdir, blocks):
    """Open the SSE stream, read all events, assert the count matches what's on disk."""
    step(f"verifying SSE stream serves all {len(blocks)} blocks")
    url = f"{args.dashboard_url}/api/frame/{fdir.name}/stream"
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    events = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            deadline = time.time() + 8
            cur_id, cur_data = None, []
            while time.time() < deadline and len(events) < len(blocks):
                line = r.readline()
                if not line:
                    break
                line = line.decode().rstrip("\r\n")
                if line == "":
                    if cur_data:
                        events.append({"id": cur_id, "data": json.loads("\n".join(cur_data))})
                        cur_id, cur_data = None, []
                elif line.startswith("id:"):
                    cur_id = line[3:].strip()
                elif line.startswith("data:"):
                    cur_data.append(line[5:].lstrip(" "))
    except (urllib.error.URLError, TimeoutError) as e:
        fail(f"SSE stream errored: {e}")

    if len(events) != len(blocks):
        fail(f"SSE delivered {len(events)} events but on-disk has {len(blocks)} blocks")
    ok("SSE stream matches on-disk count")


# ---------- main ----------


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dispatcher-url", default=DEFAULTS["dispatcher_url"])
    p.add_argument("--dashboard-url", default=DEFAULTS["dashboard_url"])
    p.add_argument("--bearer-file", default=DEFAULTS["bearer_file"])
    p.add_argument("--frames-root", default=DEFAULTS["frames_root"])
    p.add_argument("--recipe-source", default=DEFAULTS["recipe_source"])
    p.add_argument("--recipe-event-type", default=DEFAULTS["recipe_event_type"])
    p.add_argument("--min-blocks", type=int, default=DEFAULTS["min_blocks"])
    p.add_argument("--timeout-s", type=int, default=DEFAULTS["timeout_s"])
    args = p.parse_args()

    print("Tier 2 — real-agent smoke")
    print(dim(f"  dispatcher: {args.dispatcher_url}"))
    print(dim(f"  dashboard:  {args.dashboard_url}"))
    print(dim(f"  recipe:     {args.recipe_source}/{args.recipe_event_type}"))
    print(dim(f"  min blocks: {args.min_blocks}, timeout: {args.timeout_s}s"))
    print()

    bearer = preflight(args)
    event_id = fire_event(args, bearer)
    fdir = wait_for_frame(args, event_id, args.timeout_s)
    blocks = wait_for_blocks(args, fdir, args.min_blocks, args.timeout_s)
    assert_content_types(blocks, DEFAULTS["min_content_types"])
    assert_sse_matches(args, fdir, blocks)

    print()
    print(green(f"✓ Tier 2 PASS — frame {fdir.name} authored {len(blocks)} blocks end-to-end."))
    print(f"  View it: {args.dashboard_url}/m/{fdir.name}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fail("interrupted", code=130)
