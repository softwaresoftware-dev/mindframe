"""Mindframe surface server — the v0 mindframe substrate.

A mindframe is a conversation where the agent's replies are full web pages
instead of text. The agent owns ONE html surface (frame/index.html) and
rewrites it in place. The user has ONE message box. User types -> the message
is delivered to the agent -> the agent rewrites index.html -> the shell polls
/api/rev and reloads the surface.

The server owns the shell + the message rail. It NEVER touches the agent's
html. The agent owns everything inside the surface. No blocks, no typed state,
no renderer, no component library. The browser is the renderer; the agent
writes the page.

All paths are env-driven so one server binary serves any mindframe:

  MF_FRAME_DIR  directory holding index.html (the agent's surface)
  MF_TASK_ID    the agent's task id (for message delivery)
  MF_DAEMON     base URL of the agent-runtime daemon (message transport)
  MF_PORT       port to bind
"""
import json
import os
import pathlib
import subprocess

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

HERE = pathlib.Path(__file__).resolve().parent
FRAME = pathlib.Path(
    os.environ.get("MF_FRAME_DIR", pathlib.Path.home() / ".mindframe" / "surface-frame")
)
FRAME.mkdir(parents=True, exist_ok=True)
INDEX = FRAME / "index.html"

TASK_ID = os.environ.get("MF_TASK_ID", "mindframe")
DAEMON = os.environ.get("MF_DAEMON", "http://127.0.0.1:8912")
PORT = int(os.environ.get("MF_PORT", "5180"))
# Where the agent's Claude session writes its transcript. For the taskpilot
# provider the agent's HOME is the task dir; the transcript lives under
# <agent home>/.claude/projects/<hash>/<session>.jsonl.
AGENT_HOME = pathlib.Path(
    os.environ.get("MF_AGENT_HOME", pathlib.Path.home() / ".taskpilot" / TASK_ID)
)

app = FastAPI()


def deliver(text: str) -> None:
    """Hand the user's message to the agent via the agent-runtime daemon — the
    same path the runtime's own message delivery uses."""
    subprocess.run(
        ["curl", "-s", "--max-time", "15", "-X", "POST",
         f"{DAEMON}/tasks/{TASK_ID}/message",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({"text": text, "from_session": "mindframe-surface"})],
        capture_output=True,
    )


@app.get("/")
def shell() -> HTMLResponse:
    return HTMLResponse((HERE / "shell.html").read_text())


@app.get("/frame")
def frame() -> HTMLResponse:
    if INDEX.exists():
        return HTMLResponse(INDEX.read_text())
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<body style='margin:0;height:100vh;display:grid;place-items:center;"
        "font:16px system-ui;color:#777;background:#0d0d0f'>"
        "<div>Composing this mindframe&hellip;</div></body>"
    )


@app.get("/api/rev")
def rev() -> JSONResponse:
    """Revision = the surface file's mtime. Bumps whenever the agent rewrites."""
    r = INDEX.stat().st_mtime_ns if INDEX.exists() else 0
    return JSONResponse({"rev": r})


@app.post("/api/message")
async def message(req: Request) -> JSONResponse:
    text = (await req.json()).get("text", "").strip()
    if text:
        deliver(text)
    return JSONResponse({"ok": True})


def _active_transcript():
    """Newest Claude session JSONL for this agent, or None."""
    proj = AGENT_HOME / ".claude" / "projects"
    if not proj.exists():
        return None
    files = sorted(proj.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _parse_events(line: str) -> list:
    """One transcript line -> 0+ compact cognition events. The agent's real
    stream (thinking / narration / tool calls), the same one the TUI renders."""
    try:
        e = json.loads(line)
    except Exception:
        return []
    msg = e.get("message") or {}
    if (msg.get("role") or e.get("type")) == "user":
        return []  # injected prompts + tool results from the user side
    content = msg.get("content")
    if isinstance(content, str):
        s = content.strip().replace("\n", " ")
        return [{"kind": "text", "label": s[:160]}] if s else []
    if not isinstance(content, list):
        return []
    out = []
    for b in content:
        t = b.get("type")
        if t == "text" and b.get("text", "").strip():
            out.append({"kind": "text", "label": b["text"].strip().replace("\n", " ")[:160]})
        elif t == "thinking":
            out.append({"kind": "thinking", "label": "thinking…"})
        elif t == "tool_use":
            inp = b.get("input") or {}
            hint = (inp.get("command") or inp.get("file_path") or inp.get("description")
                    or inp.get("path") or inp.get("url") or "")
            label = b.get("name") or "tool"
            if hint:
                label += ": " + str(hint)[:100]
            out.append({"kind": "tool", "label": label})
    return out


@app.get("/api/activity")
def activity(offset: int = 0, file: str = "") -> JSONResponse:
    """Tail the agent's live transcript and return cognition events since
    `offset` (byte position in the current session file). When the session
    file rotates, `file` won't match the client's and we restart from 0."""
    tp = _active_transcript()
    if tp is None:
        return JSONResponse({"events": [], "offset": 0, "file": ""})
    fid = tp.name
    if fid != file:
        offset = 0
    events: list = []
    new_offset = offset
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if offset > size:
                offset = 0
            f.seek(offset)
            chunk = f.read()
        text = chunk.decode("utf-8", "replace")
        if "\n" in text:
            complete, _, remainder = text.rpartition("\n")
            new_offset = offset + (len(chunk) - len(remainder.encode("utf-8")))
            for ln in complete.split("\n"):
                if ln.strip():
                    events.extend(_parse_events(ln))
    except Exception:
        return JSONResponse({"events": [], "offset": offset, "file": fid})
    return JSONResponse({"events": events, "offset": new_offset, "file": fid})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
