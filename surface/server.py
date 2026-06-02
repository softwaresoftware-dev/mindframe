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


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
