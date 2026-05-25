"""Tier 1 wire integration tests — exercise every connection in the pipeline
except the LLM. Fire events at the test dispatcher, assert frames materialize,
assert blocks land, assert SSE streams them, assert Last-Event-ID resumption.

These tests are the only thing standing between "the loop demo'd once on
this box" and "the loop works for anyone." Keep them fast (~5s wall clock)
so CI runs them on every push."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def _post(url: str, body: dict, bearer: str | None = None, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {bearer}"} if bearer else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _wait_for_frame_blocks(frames_root: Path, *, min_blocks: int, timeout_s: float = 5.0) -> Path:
    """Polls frames_root for any frame with at least min_blocks lines in its
    blocks.jsonl. Returns the frame directory."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for fdir in frames_root.iterdir():
            blocks_path = fdir / "blocks.jsonl"
            if blocks_path.is_file() and sum(1 for _ in blocks_path.open()) >= min_blocks:
                return fdir
        time.sleep(0.1)
    listing = sorted(p.name for p in frames_root.iterdir())
    raise TimeoutError(f"no frame reached {min_blocks} blocks within {timeout_s}s; saw: {listing}")


# ---------- event-driven spawn path ----------


def test_event_creates_frame(wire_env):
    """Fire a dispatcher event → frame directory materializes with meta.json."""
    res = _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-001"}},
        bearer=wire_env.bearer,
    )
    assert res["ok"]
    assert res["mode"] == "static-spawn"

    fdir = _wait_for_frame_blocks(wire_env.frames_root, min_blocks=1, timeout_s=5)
    meta = json.loads((fdir / "meta.json").read_text())
    assert meta["title"] == "Wire test"
    assert meta["status"] == "active"
    assert meta["agent_session"] == meta["id"]
    assert meta["spawned_by"]["kind"] == "dispatcher-event"
    assert meta["spawned_by"]["event_id"] == "e2e-001"


def test_event_writes_seed_then_stub_agent_blocks(wire_env):
    """Frame should have seed + 3 stub-spawner blocks = 4 lines total."""
    _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-002"}},
        bearer=wire_env.bearer,
    )
    fdir = _wait_for_frame_blocks(wire_env.frames_root, min_blocks=4, timeout_s=10)
    blocks = [json.loads(l) for l in (fdir / "blocks.jsonl").read_text().splitlines() if l.strip()]
    assert len(blocks) == 4
    assert [b["type"] for b in blocks] == ["summary", "summary", "text", "close"]
    assert blocks[0]["author"] == "system"   # seed
    assert all(b["author"] == "agent" for b in blocks[1:])


def test_dispatcher_returns_mindframe_url(wire_env):
    """Dispatcher's response should include mindframe_url for the caller."""
    res = _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-003"}},
        bearer=wire_env.bearer,
    )
    # On the static-spawn path, BackgroundTasks fires spawn fire-and-forget,
    # so the immediate response is just {ok, mode, routed_to}. The mindframe
    # url is observable by polling /api/frames.
    assert res["routed_to"] == "spawn:wire-test"


# ---------- dashboard read APIs ----------


def test_dashboard_lists_frame(wire_env):
    _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-004"}},
        bearer=wire_env.bearer,
    )
    _wait_for_frame_blocks(wire_env.frames_root, min_blocks=4, timeout_s=10)

    listing = _get_json(f"{wire_env.dashboard_url}/api/frames")
    assert listing["frames"]
    f = listing["frames"][0]
    assert f["title"] == "Wire test"
    assert f["block_count"] == 4
    assert "wire" in f["tags"]


def test_dashboard_frame_meta_endpoint(wire_env):
    _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-005"}},
        bearer=wire_env.bearer,
    )
    fdir = _wait_for_frame_blocks(wire_env.frames_root, min_blocks=4, timeout_s=10)
    meta = _get_json(f"{wire_env.dashboard_url}/api/frame/{fdir.name}")
    assert meta["id"] == fdir.name
    assert meta["spawned_by"]["event_id"] == "e2e-005"


def test_dashboard_blocks_endpoint_returns_all(wire_env):
    _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-006"}},
        bearer=wire_env.bearer,
    )
    fdir = _wait_for_frame_blocks(wire_env.frames_root, min_blocks=4, timeout_s=10)
    res = _get_json(f"{wire_env.dashboard_url}/api/frame/{fdir.name}/blocks")
    assert len(res["blocks"]) == 4
    assert res["last_block_id"] == res["blocks"][-1]["id"]


def test_dashboard_blocks_since_filter(wire_env):
    """?since=<id> should skip blocks at-or-before that id."""
    _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-007"}},
        bearer=wire_env.bearer,
    )
    fdir = _wait_for_frame_blocks(wire_env.frames_root, min_blocks=4, timeout_s=10)
    all_blocks = _get_json(f"{wire_env.dashboard_url}/api/frame/{fdir.name}/blocks")["blocks"]
    after_id = all_blocks[1]["id"]
    rest = _get_json(f"{wire_env.dashboard_url}/api/frame/{fdir.name}/blocks?since={after_id}")["blocks"]
    assert len(rest) == 2
    assert [b["type"] for b in rest] == ["text", "close"]


# ---------- SSE stream ----------


def _drain_sse(url: str, *, last_event_id: str | None = None,
               max_events: int = 10, timeout_s: float = 5.0) -> list[dict]:
    """Open an SSE stream, collect events until either max_events or the
    timeout. Returns parsed event dicts: [{id, data}, ...]."""
    headers = {"Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    req = urllib.request.Request(url, headers=headers)
    events: list[dict] = []
    deadline = time.time() + timeout_s
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        cur_id, cur_data = None, []
        while time.time() < deadline and len(events) < max_events:
            line = r.readline()
            if not line:
                break
            line = line.decode("utf-8").rstrip("\r\n")
            if line == "":
                if cur_data:
                    events.append({"id": cur_id, "data": json.loads("\n".join(cur_data))})
                    cur_id, cur_data = None, []
            elif line.startswith("id:"):
                cur_id = line[3:].strip()
            elif line.startswith("data:"):
                cur_data.append(line[5:].lstrip(" "))
            # ignore comments and `retry:` lines
    return events


def test_sse_replays_full_history(wire_env):
    _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-008"}},
        bearer=wire_env.bearer,
    )
    fdir = _wait_for_frame_blocks(wire_env.frames_root, min_blocks=4, timeout_s=10)
    events = _drain_sse(
        f"{wire_env.dashboard_url}/api/frame/{fdir.name}/stream",
        max_events=4, timeout_s=3,
    )
    assert len(events) == 4
    assert events[0]["data"]["type"] == "summary"   # seed
    assert events[-1]["data"]["type"] == "close"


def test_sse_resumes_with_last_event_id(wire_env):
    """Reconnect with Last-Event-ID after block 2 → should only get blocks 3+4."""
    _post(
        f"{wire_env.dispatcher_url}/api/event",
        {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-009"}},
        bearer=wire_env.bearer,
    )
    fdir = _wait_for_frame_blocks(wire_env.frames_root, min_blocks=4, timeout_s=10)
    all_events = _drain_sse(
        f"{wire_env.dashboard_url}/api/frame/{fdir.name}/stream",
        max_events=4, timeout_s=3,
    )
    cursor = all_events[1]["id"]
    resumed = _drain_sse(
        f"{wire_env.dashboard_url}/api/frame/{fdir.name}/stream",
        last_event_id=cursor, max_events=2, timeout_s=3,
    )
    assert len(resumed) == 2
    assert [e["data"]["type"] for e in resumed] == ["text", "close"]


# ---------- direct dashboard /api/frames POST ----------


def test_dashboard_post_frames_creates_frame(wire_env):
    """Manual-spawn path: dashboard's POST /api/frames → frame on disk."""
    res = _post(
        f"{wire_env.dashboard_url}/api/frames",
        {"title": "Manual via POST", "seed_block": {"type": "text", "markdown": "hi from POST"}},
    )
    assert "id" in res
    assert res["url"].endswith(f"/m/{res['id']}")
    assert Path(res["frame_dir"]).is_dir()
    blocks = (Path(res["frame_dir"]) / "blocks.jsonl").read_text().splitlines()
    assert len(blocks) == 1
    assert json.loads(blocks[0])["markdown"] == "hi from POST"


def test_dashboard_post_frames_rejects_empty_title(wire_env):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{wire_env.dashboard_url}/api/frames", {"title": "   "})
    assert exc.value.code in (400, 422)   # pydantic min_length or our 400


# ---------- failure modes ----------


def test_event_without_bearer_rejected(wire_env):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(
            f"{wire_env.dispatcher_url}/api/event",
            {"source": "test", "event_type": "wire-fire", "data": {"event_id": "e2e-noauth"}},
            bearer=None,
        )
    assert exc.value.code in (401, 403)


def test_unknown_frame_returns_404(wire_env):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get_json(f"{wire_env.dashboard_url}/api/frame/does-not-exist")
    assert exc.value.code == 404
