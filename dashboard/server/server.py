"""Mindframe dashboard backend — taskpilot agent mode.

The dashboard is driven by ONE persistent taskpilot task (the "Mindframe
agent"). Each user instruction is delivered to that agent as a mesh message.
The agent reads the customer vault, composes a complete HTML document, and
writes it to artifacts/<sid>/latest.html. The server watches that file and
notifies the browser over SSE when it lands.

  Browser --SSE /api/run--> this server
                                 |  POST :8912/tasks/<agent>/message
                                 v
                         taskpilot daemon --> session-bridge --> agent (tmux)
                                                                    | writes
                                                                    v
                                             artifacts/<sid>/latest.html

No `claude --print`. No Anthropic API key. The agent is a full Claude Code
session supervised by taskpilot; it authenticates with the user's
subscription exactly as any taskpilot task does.

FastAPI + uvicorn. The HTTP/SSE contract is identical to the previous
Node/Express server — the Vite/TS frontend is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

# --------------------------- config ---------------------------

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
ARTIFACTS_ROOT = ROOT / "artifacts"
SHARES_ROOT = ROOT / "shares"
AGENT_CWD = ROOT / "agent"
AGENT_BRIEF = AGENT_CWD / "brief.json"
AGENT_ID_FILE = ROOT / ".agent-id"
DIST_ROOT = ROOT / "dist"
# The customer vault lives beside the dashboard, under the mindframe launch dir.
VAULT_ROOT = (ROOT / ".." / "launch" / "stage" / "vault").resolve()

PORT = int(os.environ.get("PORT", "5174"))
MODEL = os.environ.get("MINDFRAME_MODEL", "sonnet")
TASKPILOT_DAEMON = os.environ.get("MINDFRAME_TASKPILOT_DAEMON", "http://127.0.0.1:8912")
SESSION_BRIDGE = os.environ.get("MINDFRAME_SESSION_BRIDGE", "http://127.0.0.1:8910")
TASKPILOT_DIR = Path(
    os.environ.get("MINDFRAME_TASKPILOT_DIR", str(ROOT / ".." / ".." / ".." / "providers" / "taskpilot"))
).resolve()

SHARE_RETENTION_DAYS = int(os.environ.get("MINDFRAME_SHARE_RETENTION_DAYS", "60"))
SHARE_RETENTION_MS = SHARE_RETENTION_DAYS * 24 * 60 * 60 * 1000
SHARE_ID_LEN = 10
SHARE_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# How long to wait for the agent to produce an artifact before giving up.
RUN_TIMEOUT_MS = 6 * 60 * 1000
# After the artifact file stops changing for this long, consider it final.
ARTIFACT_STABLE_MS = 3000
# Grace after the agent ends a turn with no file before declaring failure.
NO_WRITE_GRACE_MS = 4000

# CORS allowlist. The Vite dev server (:5173) talks to this backend directly
# for SSE — Vite's proxy buffers and drops the final `done` event. In a
# production build the SPA is same-origin and needs no CORS at all.
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "MINDFRAME_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if o.strip()
]

SID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SHARE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")


def log(msg: str) -> None:
    print(f"[mindframe-dashboard] {msg}", flush=True)


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------- shares ---------------------------


def generate_share_id() -> str:
    for _ in range(8):
        out = "".join(SHARE_ID_ALPHABET[b % len(SHARE_ID_ALPHABET)] for b in secrets.token_bytes(SHARE_ID_LEN))
        if not (SHARES_ROOT / out).exists():
            return out
    return f"{int(time.time() * 1000):x}{secrets.token_hex(3)}"


def sweep_expired_shares() -> dict[str, int]:
    kept = 0
    pruned = 0
    now = now_ms()
    try:
        entries = list(SHARES_ROOT.iterdir())
    except OSError:
        return {"kept": kept, "pruned": pruned}
    for dir_path in entries:
        if not dir_path.is_dir():
            continue
        created_at = int(dir_path.stat().st_mtime * 1000)
        meta_path = dir_path / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
                if isinstance(meta.get("createdAt"), (int, float)):
                    created_at = int(meta["createdAt"])
            except (OSError, ValueError):
                pass
        if now - created_at > SHARE_RETENTION_MS:
            try:
                shutil.rmtree(dir_path, ignore_errors=True)
                pruned += 1
            except OSError:
                pass
        else:
            kept += 1
    return {"kept": kept, "pruned": pruned}


# --------------------------- artifacts ---------------------------


def sid_dir(sid: str) -> Path:
    if not SID_RE.match(sid):
        sid = str(uuid.uuid4())
    d = ARTIFACTS_ROOT / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_path(sid: str) -> Path:
    return sid_dir(sid) / "latest.html"


def artifact_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime * 1000
    except OSError:
        return 0.0


# --------------------------- taskpilot integration ---------------------------


async def check_daemons() -> dict[str, bool]:
    async def probe(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{url}/health")
                return r.is_success
        except httpx.HTTPError:
            return False

    taskpilot, session_bridge = await asyncio.gather(
        probe(TASKPILOT_DAEMON),
        probe(SESSION_BRIDGE),
    )
    return {"taskpilot": taskpilot, "sessionBridge": session_bridge}


def read_agent_id() -> Optional[str]:
    try:
        return AGENT_ID_FILE.read_text("utf-8").strip() or None
    except OSError:
        return None


def write_agent_id(agent_id: str) -> None:
    AGENT_ID_FILE.write_text(agent_id + "\n", "utf-8")


async def get_task(task_id: str) -> Optional[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{TASKPILOT_DAEMON}/tasks/{urllib.parse.quote(task_id)}")
            if not r.is_success:
                return None
            return r.json()
    except (httpx.HTTPError, ValueError):
        return None


async def spawn_task(task_id: str) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{TASKPILOT_DAEMON}/tasks/{urllib.parse.quote(task_id)}/spawn")
    if not r.is_success and r.status_code != 409:
        raise RuntimeError(f"taskpilot spawn failed ({r.status_code}): {r.text}")


async def create_agent_task() -> str:
    """Create the Mindframe agent task config via taskpilot's spawner CLI
    (--dry-run registers the task in the daemon's store without launching it).
    """
    cli = TASKPILOT_DIR / "spawner_cli.py"
    if not cli.exists():
        raise RuntimeError(f"taskpilot spawner_cli not found at {cli} - set MINDFRAME_TASKPILOT_DIR")
    # Unique name per creation so a stale killed task in taskpilot's store
    # never blocks a fresh create. The id is persisted to .agent-id and
    # reused across restarts, so this only mints a new task when there is
    # genuinely no agent to reuse.
    name = f"mindframe-dashboard-agent-{int(time.time() * 1000):x}"
    proc = await asyncio.create_subprocess_exec(
        "python3",
        str(cli),
        "Mindframe dashboard agent",
        "--name", name,
        "--brief", str(AGENT_BRIEF),
        "--cwd", str(AGENT_CWD),
        "--model", MODEL,
        "--dry-run",
        cwd=str(TASKPILOT_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("spawner_cli timed out after 60s")
    stdout = out_b.decode("utf-8", "replace")
    stderr = err_b.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise RuntimeError(f"spawner_cli failed: {stderr or 'exit ' + str(proc.returncode)}")
    last_line = next((ln for ln in reversed(stdout.strip().splitlines()) if ln.strip()), "{}")
    try:
        parsed = json.loads(last_line)
    except ValueError as e:
        raise RuntimeError(f"spawner_cli output unparseable: {stdout} - {e}")
    task_id = parsed.get("task_id") or parsed.get("id")
    if not task_id:
        raise RuntimeError(f"spawner_cli returned no task_id: {stdout}")
    return str(task_id)


async def is_channel_healthy(task_id: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{SESSION_BRIDGE}/sessions")
            if not r.is_success:
                return False
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return False
    sessions = data if isinstance(data, list) else data.get("sessions", [])
    return any((s.get("id") or s.get("session_id") or s.get("name")) == task_id for s in sessions)


def _read_agent_state(agent_id: str) -> dict[str, Any]:
    p = Path.home() / ".taskpilot" / agent_id / "state" / "agent.json"
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _parse_iso_ms(iso: Any) -> int:
    """Parse an ISO-8601 timestamp to epoch-ms, or 0 if unparseable."""
    if not isinstance(iso, str):
        return 0
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def agent_last_stop_ms(agent_id: str) -> int:
    """The agent's Stop hook records the end of every turn to its state file.
    This is the reliable "agent finished a turn" signal. Returns epoch-ms of
    the most recent turn end, or 0 if unknown.
    """
    return _parse_iso_ms(_read_agent_state(agent_id).get("last_stop", {}).get("received_at"))


def agent_last_prompt(agent_id: str) -> dict[str, Any]:
    """The agent's UserPromptSubmit hook records the last prompt it received.
    We use this to confirm the agent has actually PICKED UP our instruction
    (vs. still chewing through an earlier queued message).
    """
    lp = _read_agent_state(agent_id).get("last_prompt") or {}
    prompt = lp.get("prompt")
    if isinstance(prompt, str):
        return {"receivedAt": _parse_iso_ms(lp.get("received_at")), "prompt": prompt}
    return {"receivedAt": 0, "prompt": ""}


# Agent lifecycle - exactly one persistent agent. ensure_agent() is idempotent
# and de-duplicated: concurrent callers await the same in-flight task.
_agent_task: Optional[asyncio.Task] = None


async def _ensure_agent_inner() -> str:
    agent_id = read_agent_id()

    if agent_id:
        task = await get_task(agent_id)
        if task and task.get("status") == "running":
            if await is_channel_healthy(agent_id):
                return agent_id
            for _ in range(20):
                await asyncio.sleep(1)
                if await is_channel_healthy(agent_id):
                    return agent_id
        if task and task.get("status") != "running":
            await spawn_task(agent_id)
            for _ in range(30):
                await asyncio.sleep(1)
                if await is_channel_healthy(agent_id):
                    return agent_id
            return agent_id
        # id on file but daemon doesn't know it - recreate.
        agent_id = None

    new_id = await create_agent_task()
    write_agent_id(new_id)
    await spawn_task(new_id)
    for _ in range(40):
        await asyncio.sleep(1)
        if await is_channel_healthy(new_id):
            return new_id
    return new_id


def ensure_agent(force_recreate: bool = False) -> asyncio.Task:
    global _agent_task
    if force_recreate:
        _agent_task = None
    if _agent_task is None:
        _agent_task = asyncio.create_task(_ensure_agent_inner())
    return _agent_task


async def await_agent(force_recreate: bool = False) -> str:
    global _agent_task
    task = ensure_agent(force_recreate)
    try:
        return await task
    except Exception:
        _agent_task = None
        raise


async def send_message(agent_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{TASKPILOT_DAEMON}/tasks/{urllib.parse.quote(agent_id)}/message",
            json={"text": text, "from_session": "mindframe-dashboard"},
        )
    if not r.is_success:
        raise RuntimeError(f"message delivery failed ({r.status_code}): {r.text}")


# --------------------------- SSE helpers ---------------------------


def sse(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    lines = "".join(f"data: {line}\n" for line in payload.split("\n"))
    return f"event: {event}\n{lines}\n"


# One instruction at a time - there is a single shared agent.
_run_in_flight = False


# --------------------------- app ---------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
    SHARES_ROOT.mkdir(parents=True, exist_ok=True)
    log(f"server on http://127.0.0.1:{PORT}")
    log(f"model: {MODEL}")
    log(f"artifacts: {ARTIFACTS_ROOT}")
    log(f"shares: {SHARES_ROOT} ({SHARE_RETENTION_DAYS}-day retention)")
    log(f"vault: {VAULT_ROOT}")
    swept = sweep_expired_shares()
    log(f"startup share sweep: kept {swept['kept']} pruned {swept['pruned']}")

    sweep_task = asyncio.create_task(_hourly_sweep())

    daemons = await check_daemons()
    if not daemons["taskpilot"] or not daemons["sessionBridge"]:
        log("WARNING: taskpilot/session-bridge daemon down - instructions will hard-fail until it is up.")
    else:
        log("warming the Mindframe agent...")
        asyncio.create_task(_warm_agent())

    try:
        yield
    finally:
        sweep_task.cancel()


async def _warm_agent() -> None:
    try:
        agent_id = await await_agent()
        log(f"agent ready: {agent_id}")
    except Exception as e:  # noqa: BLE001 - warm-up is best-effort
        log(f"agent warm-up failed: {e}")


async def _hourly_sweep() -> None:
    while True:
        try:
            await asyncio.sleep(60 * 60)
        except asyncio.CancelledError:
            return
        pruned = sweep_expired_shares()["pruned"]
        if pruned > 0:
            log(f"hourly sweep pruned {pruned} expired share(s)")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)


@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    daemons = await check_daemons()
    return {"ok": True, "port": PORT, "agentId": read_agent_id(), "daemons": daemons}


@app.get("/s/{share_id}")
def serve_share(share_id: str) -> Response:
    if not SHARE_ID_RE.match(share_id):
        return PlainTextResponse("invalid share id", status_code=400)
    dir_path = SHARES_ROOT / share_id
    file = dir_path / "index.html"
    if not file.exists():
        return PlainTextResponse("share not found", status_code=404)
    meta_path = dir_path / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            created = meta.get("createdAt")
            if isinstance(created, (int, float)) and now_ms() - created > SHARE_RETENTION_MS:
                return PlainTextResponse("this share has expired", status_code=410)
        except (OSError, ValueError):
            pass
    return FileResponse(
        file,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/share/{share_id}")
def api_share(share_id: str) -> Response:
    if not SHARE_ID_RE.match(share_id):
        return JSONResponse({"error": "invalid share id"}, status_code=400)
    meta_path = SHARES_ROOT / share_id / "meta.json"
    if not meta_path.exists():
        return JSONResponse({"error": "share not found"}, status_code=404)
    try:
        return JSONResponse(json.loads(meta_path.read_text("utf-8")))
    except (OSError, ValueError):
        return JSONResponse({"error": "meta unreadable"}, status_code=500)


class SaveBody(BaseModel):
    sid: str = ""
    label: str = ""


@app.post("/api/save")
def api_save(body: SaveBody) -> Response:
    """Snapshot the current artifact to a sharable URL."""
    sid = body.sid.strip()
    label = body.label[:200] if isinstance(body.label, str) else ""
    if not sid:
        return JSONResponse({"error": "sid required"}, status_code=400)
    src = artifact_path(sid)
    if not src.exists():
        return JSONResponse({"error": "no artifact to save - run an instruction first"}, status_code=404)
    html = src.read_text("utf-8")
    share_id = generate_share_id()
    dir_path = SHARES_ROOT / share_id
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "index.html").write_text(html, "utf-8")
    created_at = now_ms()
    meta = {
        "id": share_id,
        "sid": sid,
        "label": label,
        "createdAt": created_at,
        "expiresAt": created_at + SHARE_RETENTION_MS,
        "retentionDays": SHARE_RETENTION_DAYS,
        "bytes": len(html),
    }
    (dir_path / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
    return JSONResponse({"url": f"/s/{share_id}", **meta})


@app.get("/artifacts/{sid}/{path:path}")
def serve_artifact(sid: str, path: str) -> Response:
    """Serve a generated artifact. `sid` is validated and the resolved path is
    confined to the session's directory."""
    if not SID_RE.match(sid):
        return PlainTextResponse("not found", status_code=404)
    base = ARTIFACTS_ROOT / sid
    target = (base / path).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        return PlainTextResponse("not found", status_code=404)
    if not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(target, headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/run")
async def api_run(
    request: Request,
    msg: str = Query(default=""),
    sid: str = Query(default=""),
) -> Response:
    message_text = msg.strip()
    session_id = sid or str(uuid.uuid4())
    if not message_text:
        return JSONResponse({"error": "msg required"}, status_code=400)

    stream = _run_stream(request, message_text, session_id)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_stream(request: Request, msg: str, sid: str) -> AsyncIterator[str]:
    """Drive one instruction end-to-end. A worker coroutine does the work and
    pushes SSE events onto a queue; this generator drains the queue, emitting a
    keepalive comment whenever the worker is quiet for 15s.
    """
    queue: asyncio.Queue = asyncio.Queue()
    worker = asyncio.create_task(_run_worker(msg, sid, queue))
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is None:
                break
            event, data = item
            yield sse(event, data)
    finally:
        if not worker.done():
            worker.cancel()


async def _run_worker(msg: str, sid: str, queue: asyncio.Queue) -> None:
    global _run_in_flight
    acquired = False
    try:
        if _run_in_flight:
            await queue.put(("error", "the agent is already working on an instruction - wait for it to finish"))
            return
        _run_in_flight = True
        acquired = True

        daemons = await check_daemons()
        if not daemons["taskpilot"] or not daemons["sessionBridge"]:
            down = " and ".join(
                p for p in (
                    "taskpilot daemon (:8912)" if not daemons["taskpilot"] else None,
                    "session-bridge daemon (:8910)" if not daemons["sessionBridge"] else None,
                ) if p
            )
            await queue.put((
                "error",
                f"{down} unreachable. Start the taskpilot daemon "
                "(python3 daemon.py --install), then retry.",
            ))
            return

        await queue.put(("progress", {"stage": "agent", "label": "connecting to the Mindframe agent"}))

        try:
            agent_id = await await_agent()
        except Exception as e:  # noqa: BLE001
            await queue.put(("error", f"could not reach the Mindframe agent: {e}"))
            return

        artifact = artifact_path(sid)
        baseline_mtime = artifact_mtime(artifact)

        # Unique per-instruction nonce. The agent is a shared, persistent
        # session with a message queue — a Stop event after we send is NOT
        # necessarily the response to OUR instruction. We embed this run id in
        # the message and confirm pickup by finding it in the agent's
        # last_prompt before arming any fail-fast logic.
        run_id = str(uuid.uuid4())
        message = "\n".join([
            f"INSTRUCTION: {msg}",
            "",
            f"VAULT: {VAULT_ROOT}",
            f"ARTIFACT: {artifact}",
            f"RUN-ID: {run_id}",
            "",
            "Write the complete HTML document to the ARTIFACT path. Read the VAULT for"
            " ground truth. The RUN-ID line is correlation metadata — ignore it."
            " Output nothing else.",
        ])

        try:
            await send_message(agent_id, message)
        except Exception:  # noqa: BLE001 - one retry with a fresh agent
            try:
                agent_id = await await_agent(force_recreate=True)
                await send_message(agent_id, message)
            except Exception as e2:  # noqa: BLE001
                await queue.put(("error", f"could not deliver the instruction to the agent: {e2}"))
                return

        await queue.put(("progress", {"stage": "running", "label": "instruction delivered - agent is working"}))

        # Watch the artifact file until it lands and stabilizes.
        #
        # Fail-fast is keyed off PICKUP, not send time. The agent has a message
        # queue: it may be busy on an earlier message when we send. We only arm
        # the "finished without writing" check once the agent's last_prompt
        # carries our run_id — i.e. it is genuinely working on THIS instruction.
        started = now_ms()
        first_write_at = 0
        last_mtime = baseline_mtime
        last_heartbeat_at = 0
        picked_up_at = 0

        while True:
            await asyncio.sleep(1)
            now = now_ms()

            if now - started > RUN_TIMEOUT_MS:
                await queue.put(("error", "timed out waiting for the agent to produce the page (6 min)"))
                return

            mtime = artifact_mtime(artifact)
            if mtime > baseline_mtime:
                if not first_write_at:
                    first_write_at = now
                    await queue.put(("progress", {"stage": "running", "label": "artifact written - finalizing"}))
                if mtime != last_mtime:
                    last_mtime = mtime          # still changing; reset stability window
                    first_write_at = now
                elif now - first_write_at >= ARTIFACT_STABLE_MS:
                    url = f"/artifacts/{urllib.parse.quote(sid)}/latest.html"
                    try:
                        size = artifact.stat().st_size
                    except OSError:
                        size = 0
                    await queue.put(("done", {"url": url, "sid": sid, "bytes": size}))
                    return
                continue

            # No artifact yet. Confirm the agent has picked up THIS instruction
            # before trusting any Stop event as "finished our turn".
            if not picked_up_at:
                lp = agent_last_prompt(agent_id)
                if run_id in lp["prompt"]:
                    picked_up_at = lp["receivedAt"] or now
                    await queue.put(("progress", {"stage": "running", "label": "agent picked up the instruction"}))

            # Fail-fast only once picked up: if the agent ended a turn AFTER it
            # received our instruction and still wrote nothing, it's a real miss.
            if picked_up_at and not first_write_at:
                stop = agent_last_stop_ms(agent_id)
                if stop > picked_up_at and now - stop >= NO_WRITE_GRACE_MS:
                    await queue.put((
                        "error",
                        "the agent finished without writing a page - try rephrasing the instruction",
                    ))
                    return

            if now - last_heartbeat_at >= 12000:
                last_heartbeat_at = now
                secs = round((now - started) / 1000)
                label = (
                    f"agent is working - {secs}s"
                    if picked_up_at
                    else f"agent is busy - your instruction is queued ({secs}s)"
                )
                await queue.put(("progress", {"stage": "running", "label": label, "kind": "tick"}))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        await queue.put(("error", f"run failed: {e}"))
    finally:
        if acquired:
            _run_in_flight = False
        await queue.put(None)


# Serve the built SPA. Behind nginx, /demo/ is stripped, so the backend sees
# /, /assets/*, etc. Real files in dist/ are served directly; unknown GET paths
# fall back to index.html for SPA client-side routing.
@app.get("/{full_path:path}")
def serve_spa(full_path: str) -> Response:
    if full_path.startswith("api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    dist = DIST_ROOT.resolve()
    if full_path:
        candidate = (dist / full_path).resolve()
        if (dist in candidate.parents) and candidate.is_file():
            return FileResponse(candidate)
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse({"error": "not found"}, status_code=404)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
