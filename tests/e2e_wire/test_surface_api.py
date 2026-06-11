"""Tier-1 hermetic wire tests for the dashboard's surface API.

Covers the product's core actions — create a mindframe, message its agent,
delete it — plus the security-sensitive paths (frame-id validation, artifact
path-traversal containment). The taskpilot daemon is replaced by an in-process
HTTP stub; no real daemons, no LLM, no network beyond 127.0.0.1.

The server module is loaded via importlib under a unique name (same pattern as
dashboard/tests/test_graph.py); its uvicorn.run is __main__-guarded, so
importing starts nothing. TestClient is used WITHOUT a `with` block so the
lifespan (which starts the `claude mcp list` cache warmer) never runs.
"""
import importlib.util
import json
import pathlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

SERVER_PY = pathlib.Path(__file__).resolve().parents[2] / "dashboard" / "server" / "server.py"
_spec = importlib.util.spec_from_file_location("mf_dashboard_server_wire", SERVER_PY)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

client = TestClient(srv.app)


# --------------------------- stub taskpilot daemon ---------------------------


class _StubTaskpilot(BaseHTTPRequestHandler):
    """Answers the taskpilot 0.15 endpoints the dashboard calls: PUT
    /tasks/{id}, POST /tasks/{id}/start, POST /tasks/{id}/message, DELETE
    /tasks/{id}. Set `agent_dead = True` to make /message 409
    agent_not_running until a /start arrives (the revive scenario)."""

    calls: list = []          # (method, path, body)
    agent_dead: bool = False  # 409 messages until a /start is seen

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {})

    def do_PUT(self):
        body = self._body()
        type(self).calls.append(("PUT", self.path, body))
        self._send(200, {"task_id": self.path.rsplit("/", 1)[-1], "created": True})

    def do_DELETE(self):
        type(self).calls.append(("DELETE", self.path, {}))
        self._send(200, {"ok": True, "deleted": True, "existed": True})

    def do_POST(self):
        body = self._body()
        type(self).calls.append(("POST", self.path, body))
        if self.path.endswith("/start"):
            type(self).agent_dead = False
            self._send(200, {"ok": True, "started": True, "status": "running"})
        elif self.path.endswith("/message"):
            if type(self).agent_dead:
                self._send(409, {"detail": {"code": "agent_not_running",
                                            "task_status": "crashed"}})
            else:
                self._send(200, {"ok": True, "delivered": True})
        else:
            self._send(404, {})

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture()
def stub_daemon(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubTaskpilot)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _StubTaskpilot.calls = []
    _StubTaskpilot.agent_dead = False
    monkeypatch.setattr(srv, "TASKPILOT_DAEMON", f"http://127.0.0.1:{server.server_port}")
    yield _StubTaskpilot
    server.shutdown()


@pytest.fixture()
def down_daemon(monkeypatch):
    """Point the dashboard at a port nothing listens on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(srv, "TASKPILOT_DAEMON", f"http://127.0.0.1:{port}")


@pytest.fixture()
def frames_root(tmp_path, monkeypatch):
    root = tmp_path / "frames"
    root.mkdir()
    monkeypatch.setattr(srv, "FRAMES_ROOT", root)
    # Keep artifact resolution out of the repo's real dashboard/artifacts dir.
    monkeypatch.setattr(srv, "ARTIFACTS_ROOT", tmp_path / "artifacts-none")
    return root


def _make_frame(root: pathlib.Path, mid: str, task_id: str | None = None) -> pathlib.Path:
    fdir = root / mid
    fdir.mkdir()
    (fdir / "index.html").write_text("<!doctype html><title>t</title>ok", "utf-8")
    (fdir / "meta.json").write_text(
        json.dumps({"id": mid, "title": mid, "task_id": task_id or mid}), "utf-8")
    return fdir


# --------------------------- health ---------------------------


def test_health_shape():
    j = client.get("/api/health").json()
    assert j["ok"] is True
    assert set(j) == {"ok", "port", "dispatcher_url", "dispatcher_bearer_present"}


# --------------------------- create ---------------------------


def test_create_mints_frame_and_spawns(frames_root, stub_daemon):
    r = client.post("/api/frames/create", json={"prompt": "watch the build", "title": "Build"})
    assert r.status_code == 200
    j = r.json()
    assert j["spawn"] == "ok" and j["url"] == f"/m/{j['id']}"
    fdir = frames_root / j["id"]
    assert (fdir / "index.html").is_file() and "omposing" in (fdir / "index.html").read_text()
    meta = json.loads((fdir / "meta.json").read_text())
    assert meta["task_id"] == j["id"] and meta["title"] == "Build"
    # Create = idempotent define (PUT) then ensure-running (start).
    put_body = next(b for m, p, b in stub_daemon.calls
                    if m == "PUT" and p == f"/tasks/{j['id']}")
    assert ("POST", f"/tasks/{j['id']}/start", {}) in stub_daemon.calls
    assert put_body["cwd"] == str(fdir)
    assert str(fdir / "index.html") in put_body["description"]
    # The brief must teach the self-messaging button affordance, or agents
    # render prose offers instead of clickable actions.
    assert "location.pathname.replace('/page','/message')" in put_body["description"]


def test_create_with_daemon_down_leaves_no_orphan(frames_root, down_daemon):
    r = client.post("/api/frames/create", json={"prompt": "anything"})
    assert r.status_code == 503
    assert list(frames_root.iterdir()) == []


def test_create_rejects_empty_prompt(frames_root, stub_daemon):
    assert client.post("/api/frames/create", json={"prompt": ""}).status_code == 422


# --------------------------- message ---------------------------


def test_message_routes_to_meta_task_id(frames_root, stub_daemon):
    _make_frame(frames_root, "frame1", task_id="task-77")
    r = client.post("/api/frame/frame1/message", json={"text": "hi"})
    assert r.status_code == 200 and r.json() == {"ok": True, "revived": False}
    assert stub_daemon.calls == [("POST", "/tasks/task-77/message",
                                  {"text": "hi", "from_session": "mindframe-surface"})]


def test_message_revives_dead_agent_then_delivers(frames_root, stub_daemon):
    """The headline lifecycle fix: a frame whose agent died is revived on the
    next message — start with a resume-flavored brief, then deliver — instead
    of failing forever."""
    fdir = _make_frame(frames_root, "frame1", task_id="task-77")
    stub_daemon.agent_dead = True
    r = client.post("/api/frame/frame1/message", json={"text": "hi again"})
    assert r.status_code == 200 and r.json() == {"ok": True, "revived": True}
    assert [(m, p) for m, p, _ in stub_daemon.calls] == [
        ("POST", "/tasks/task-77/message"),   # 409 agent_not_running
        ("POST", "/tasks/task-77/start"),     # revive
        ("POST", "/tasks/task-77/message"),   # delivered
    ]
    start_body = stub_daemon.calls[1][2]
    # The revival brief resumes the existing page — it must point at the real
    # index.html and must NOT be the compose-your-first-page brief.
    assert str(fdir / "index.html") in start_body["prompt"]
    assert "RESUMING" in start_body["prompt"]
    assert "first request is below" not in start_body["prompt"]


def test_message_revival_failure_is_502(frames_root, stub_daemon, monkeypatch):
    _make_frame(frames_root, "frame1", task_id="task-77")
    stub_daemon.agent_dead = True

    # Make /start fail: 404 the task (e.g. row deleted out from under the frame).
    orig = stub_daemon.do_POST
    def failing_post(self):
        if self.path.endswith("/start"):
            self.calls.append(("POST", self.path, self._body()))
            self._send(404, {"detail": "task 'task-77' not found"})
        else:
            orig(self)
    monkeypatch.setattr(stub_daemon, "do_POST", failing_post)

    r = client.post("/api/frame/frame1/message", json={"text": "hi"})
    assert r.status_code == 502 and r.json()["ok"] is False
    assert "revival failed" in r.json()["error"]


def test_message_daemon_down_is_502(frames_root, down_daemon):
    _make_frame(frames_root, "frame1")
    r = client.post("/api/frame/frame1/message", json={"text": "hi"})
    assert r.status_code == 502 and r.json()["ok"] is False


def test_message_unknown_frame_is_404(frames_root, stub_daemon):
    assert client.post("/api/frame/nope/message", json={"text": "hi"}).status_code == 404
    assert stub_daemon.calls == []


# --------------------------- delete ---------------------------


def test_delete_kills_agent_and_removes_dir(frames_root, stub_daemon):
    fdir = _make_frame(frames_root, "frame1", task_id="task-77")
    r = client.delete("/api/frame/frame1")
    assert r.status_code == 200 and r.json() == {"ok": True, "id": "frame1", "killed": True}
    assert not fdir.exists()
    # DELETE (not kill): frees the task id on the taskpilot side too.
    assert stub_daemon.calls == [("DELETE", "/tasks/task-77", {})]


def test_delete_unknown_frame_is_404(frames_root, stub_daemon):
    assert client.delete("/api/frame/nope").status_code == 404


def test_delete_with_daemon_down_still_removes_dir(frames_root, down_daemon):
    fdir = _make_frame(frames_root, "frame1")
    r = client.delete("/api/frame/frame1")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["killed"] is False
    assert not fdir.exists()


# --------------------------- frame-id validation ---------------------------


def test_frame_dir_rejects_malformed_ids(frames_root):
    for bad in ("..", "a/b", "a b", "x" * 65, ""):
        assert srv._frame_dir(bad) is None
    assert srv._frame_dir("missing-but-valid") is None  # well-formed, not on disk
    _make_frame(frames_root, "real_one-2")
    assert srv._frame_dir("real_one-2") == frames_root / "real_one-2"


def test_dotdot_frame_id_is_404(frames_root):
    # %2e%2e survives client-side URL normalization and reaches the route as ".."
    assert client.get("/api/frame/%2e%2e/page").status_code == 404
    assert client.get("/m/%2e%2e").status_code == 404


# --------------------------- artifacts traversal ---------------------------


def test_artifact_serves_sibling_file(frames_root):
    fdir = _make_frame(frames_root, "frame1")
    (fdir / "sub").mkdir()
    (fdir / "sub" / "data.txt").write_text("payload", "utf-8")
    r = client.get("/artifacts/frame1/sub/data.txt")
    assert r.status_code == 200 and r.text == "payload"


def test_artifact_dotdot_traversal_is_rejected(frames_root, tmp_path):
    _make_frame(frames_root, "frame1")
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", "utf-8")
    # frames_root = tmp_path/frames, so ../../secret.txt escapes the frame dir.
    r = client.get("/artifacts/frame1/%2e%2e/%2e%2e/secret.txt")
    assert r.status_code == 404


def test_artifact_symlink_escape_is_rejected(frames_root, tmp_path):
    fdir = _make_frame(frames_root, "frame1")
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", "utf-8")
    try:
        (fdir / "link.txt").symlink_to(secret)
    except OSError:
        pytest.skip("symlinks unavailable (Windows without dev mode)")
    assert client.get("/artifacts/frame1/link.txt").status_code == 404


# --------------------------- rev + listing ---------------------------


def test_rev_zero_without_page_then_bumps(frames_root):
    fdir = frames_root / "frame1"
    fdir.mkdir()
    assert client.get("/api/frame/frame1/rev").json()["rev"] == 0
    (fdir / "index.html").write_text("x", "utf-8")
    assert client.get("/api/frame/frame1/rev").json()["rev"] > 0


def test_frames_listing_requires_index_html(frames_root):
    _make_frame(frames_root, "withpage")
    (frames_root / "nopage").mkdir()  # frame dir without index.html — not listed
    ids = [f["id"] for f in client.get("/api/frames").json()["frames"]]
    assert ids == ["withpage"]
