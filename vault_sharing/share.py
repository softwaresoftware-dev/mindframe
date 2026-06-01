#!/usr/bin/env python3
"""vault-sharing — outbound share trigger.

Sends a "share this vault with X" job to the vault-sharing agent via
session-bridge. The agent handles the gh CLI mechanics, sends the
GitHub collaborator invite, writes a record to the outgoing-shares index.

Usage:
  share.py --vault acme-matter --to beth@firm.com
  share.py --vault acme-matter --to beth-github --permission pull
  share.py --vault acme-matter --to beth@firm.com --owner softwaresoftware-dev
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from lib import vaults_yaml  # noqa: E402

STATE_DIR = Path(os.environ.get(
    "VAULT_SHARING_STATE_DIR",
    str(Path.home() / ".mindframe" / "vault-sharing"),
))
QUEUE_DIR = STATE_DIR / "queue"
RESPONSES_DIR = STATE_DIR / "responses"
SESSION_BRIDGE_URL = os.environ.get(
    "SESSION_BRIDGE_URL", "http://127.0.0.1:8910")
AGENT_NAME = os.environ.get("VAULT_SHARING_AGENT_NAME", "vault-sharing")


def resolve_github_owner() -> str | None:
    """Default the GitHub repo owner to the operator's own gh user."""
    try:
        r = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def write_job(job: dict) -> Path:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    path = QUEUE_DIR / f"{job['job_id']}.json"
    path.write_text(json.dumps(job, indent=2))
    return path


def send_to_agent(job_path: Path) -> dict:
    url = f"{SESSION_BRIDGE_URL}/sessions/{AGENT_NAME}/message"
    r = httpx.post(url, json={"text": f"vault-sharing job: {job_path}"}, timeout=10)
    if r.status_code == 409:
        raise RuntimeError(
            f"agent '{AGENT_NAME}' has no channel — is it spawned? "
            f"See ~/.taskpilot/{AGENT_NAME}/."
        )
    if r.status_code == 404:
        raise RuntimeError(f"agent '{AGENT_NAME}' not in the mesh. Spawn via taskpilot first.")
    r.raise_for_status()
    return r.json()


def wait_for_response(response_path: Path, job_path: Path, timeout_s: int) -> bool:
    waited = 0
    while waited < timeout_s:
        if response_path.is_file():
            return True
        if not job_path.exists():
            time.sleep(2)
            return response_path.is_file()
        time.sleep(3)
        waited += 3
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vault", required=True, help="vault name in ~/.mindframe/vaults.yaml")
    ap.add_argument("--to", required=True, help="recipient email or GitHub username")
    ap.add_argument("--permission", default="push", choices=["pull", "push", "admin"])
    ap.add_argument("--owner", help="GitHub owner (default: your gh user)")
    ap.add_argument("--wait", action="store_true", help="block for agent response (max 5 min)")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    vault = vaults_yaml.get_vault(args.vault)
    if not vault:
        print(f"error: no vault named '{args.vault}' in ~/.mindframe/vaults.yaml",
              file=sys.stderr)
        known = [v["name"] for v in vaults_yaml.list_vaults()]
        if known:
            print(f"  available vaults: {', '.join(known)}", file=sys.stderr)
        else:
            print("  no vaults configured yet — see /mindframe:setup", file=sys.stderr)
        return 1

    owner = args.owner or resolve_github_owner()
    if not owner:
        print("error: couldn't determine GitHub owner. Run `gh auth login`, or pass --owner.",
              file=sys.stderr)
        return 1

    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    response_path = RESPONSES_DIR / f"share-{job_id}.json"

    job = {
        "job_id": job_id,
        "kind": "share",
        "vault_name": args.vault,
        "vault_path": vault["path"],
        "recipient": args.to,
        "permission": args.permission,
        "github_owner": owner,
        "response_path": str(response_path),
    }
    job_path = write_job(job)

    print(f"vault:      {args.vault} ({vault['path']})")
    print(f"recipient:  {args.to} (perm: {args.permission})")
    print(f"repo:       {owner}/vault-{args.vault}")
    print(f"job:        {job_path}")
    print(f"response:   {response_path}")

    try:
        result = send_to_agent(job_path)
        print(f"sent → agent {AGENT_NAME} (chat_id={result.get('chat_id', '?')})")
    except (httpx.HTTPError, RuntimeError) as e:
        print(f"! send failed: {e}", file=sys.stderr)
        return 1

    if args.wait:
        print(f"waiting up to {args.timeout}s for agent response...")
        if wait_for_response(response_path, job_path, args.timeout):
            print(f"\n--- response ---\n")
            print(response_path.read_text())
        else:
            print(f"! timed out", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
