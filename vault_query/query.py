#!/usr/bin/env python3
"""vault-query — trigger script for the vault-query agent.

Sends a question to the long-running vault-query taskpilot agent via
session-bridge. Mirror of vault-keeper's keeper.py shape: write a job file
to a queue dir, message the agent, wait for response file to appear (or
fire-and-forget).

Usage:
  query.py --question "what's the status of Procure.ai?" --vault-path /path/to/vault
  query.py --question "..." --vault-path ... --wait        # block for response
  query.py --question "..." --vault-path ... --response-path /tmp/out.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

STATE_DIR = Path(os.environ.get(
    "VAULT_QUERY_STATE_DIR", str(Path.home() / ".mindframe" / "vault-query")))
QUEUE_DIR = STATE_DIR / "queue"
RESPONSES_DIR = STATE_DIR / "responses"
SESSION_BRIDGE_URL = os.environ.get(
    "SESSION_BRIDGE_URL", "http://127.0.0.1:8910")
AGENT_NAME = os.environ.get("VAULT_QUERY_AGENT_NAME", "vault-query")


def write_job(
    *, vault_path: Path, question: str, response_path: Path,
    queue_dir: Path | None = None,
) -> Path:
    qdir = queue_dir or QUEUE_DIR
    qdir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "vault_path": str(vault_path),
        "question": question,
        "response_path": str(response_path),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    job_path = qdir / f"{job_id}.json"
    job_path.write_text(json.dumps(job, indent=2))
    return job_path


def send_to_agent(job_path: Path) -> dict:
    url = f"{SESSION_BRIDGE_URL}/sessions/{AGENT_NAME}/message"
    body = {"text": f"vault-query job: {job_path}"}
    r = httpx.post(url, json=body, timeout=10)
    if r.status_code == 409:
        raise RuntimeError(
            f"agent '{AGENT_NAME}' has no channel — is it spawned? "
            f"See ~/.taskpilot/{AGENT_NAME}/."
        )
    if r.status_code == 404:
        raise RuntimeError(
            f"agent '{AGENT_NAME}' not in mesh. Spawn via taskpilot first."
        )
    r.raise_for_status()
    return r.json()


def wait_for_response(
    response_path: Path, job_path: Path, timeout_s: int = 180,
) -> bool:
    """Block until either the response file appears or the job file
    disappears (agent processed but maybe errored)."""
    waited = 0
    while waited < timeout_s:
        if response_path.is_file():
            return True
        if not job_path.exists():
            # Agent processed and removed the job — response should have
            # been written. If we still don't see it, give it a moment.
            time.sleep(2)
            return response_path.is_file()
        time.sleep(3)
        waited += 3
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--question", required=True)
    ap.add_argument("--vault-path", required=True,
                    help="absolute path to the vault dir to query")
    ap.add_argument("--response-path",
                    help="where to write the answer (default: auto under state dir)")
    ap.add_argument("--queue-dir",
                    help="override queue dir (used by simulations)")
    ap.add_argument("--responses-dir",
                    help="override responses dir (used by simulations)")
    ap.add_argument("--wait", action="store_true",
                    help="block until the response file appears (max 3 min)")
    ap.add_argument("--timeout", type=int, default=180,
                    help="seconds to wait when --wait is set (default 180)")
    args = ap.parse_args()

    vault_path = Path(args.vault_path).expanduser()
    if not vault_path.is_dir():
        print(f"error: vault dir not found: {vault_path}", file=sys.stderr)
        return 1

    queue_dir = Path(args.queue_dir).expanduser() if args.queue_dir else None
    responses_dir = (Path(args.responses_dir).expanduser() if args.responses_dir
                     else RESPONSES_DIR)
    responses_dir.mkdir(parents=True, exist_ok=True)

    if args.response_path:
        response_path = Path(args.response_path).expanduser()
        response_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        rid = uuid.uuid4().hex[:8]
        response_path = responses_dir / f"{rid}.md"

    job_path = write_job(
        vault_path=vault_path, question=args.question,
        response_path=response_path, queue_dir=queue_dir,
    )
    print(f"question: {args.question}")
    print(f"vault:    {vault_path}")
    print(f"job:      {job_path}")
    print(f"response: {response_path}")

    try:
        result = send_to_agent(job_path)
        print(f"sent → agent {AGENT_NAME} (chat_id={result.get('chat_id', '?')})")
    except (httpx.HTTPError, RuntimeError) as e:
        print(f"! send failed: {e}", file=sys.stderr)
        return 1

    if args.wait:
        print(f"waiting up to {args.timeout}s for response...")
        if wait_for_response(response_path, job_path, timeout_s=args.timeout):
            print(f"\n--- response ---\n")
            print(response_path.read_text())
        else:
            print(f"! timed out waiting for response", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
