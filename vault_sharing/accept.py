#!/usr/bin/env python3
"""vault-sharing — inbound accept trigger.

Tells the vault-sharing agent to accept a GitHub repository invitation,
clone the vault, register it in vaults.yaml, notify vault-keeper and
vault-query that a new vault is available.

Usage:
  accept.py --list                              # show pending GitHub invites
  accept.py --invitation 12345678               # accept by id, auto-name
  accept.py --invitation 12345 --vault acme-x   # accept with explicit local name
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
DEFAULT_VAULTS_ROOT = Path(os.environ.get(
    "MINDFRAME_VAULTS_ROOT", str(Path.home() / "mindframe-vaults")))
SESSION_BRIDGE_URL = os.environ.get(
    "SESSION_BRIDGE_URL", "http://127.0.0.1:8910")
AGENT_NAME = os.environ.get("VAULT_SHARING_AGENT_NAME", "vault-sharing")


def list_pending_invites() -> list[dict]:
    """Read pending GitHub repository_invitations via gh CLI."""
    try:
        r = subprocess.run(
            ["gh", "api", "/user/repository_invitations"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError("gh CLI not installed")
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh CLI timed out")
    if r.returncode != 0:
        raise RuntimeError(f"gh api failed: {r.stderr.strip()}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


def derive_vault_name(repo_full_name: str) -> str:
    """`thatcher/vault-acme-matter` → `acme-matter`. Strips a leading `vault-`
    convention; otherwise uses the repo name verbatim."""
    name = repo_full_name.split("/")[-1]
    if name.startswith("vault-"):
        name = name[len("vault-"):]
    return name


def write_job(job: dict) -> Path:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    path = QUEUE_DIR / f"{job['job_id']}.json"
    path.write_text(json.dumps(job, indent=2))
    return path


def send_to_agent(job_path: Path) -> dict:
    url = f"{SESSION_BRIDGE_URL}/sessions/{AGENT_NAME}/message"
    r = httpx.post(url, json={"text": f"vault-sharing job: {job_path}"}, timeout=10)
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


def cmd_list() -> int:
    try:
        invites = list_pending_invites()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not invites:
        print("no pending repository invitations.")
        return 0
    print(f"pending invitations ({len(invites)}):")
    for inv in invites:
        repo = inv.get("repository", {}).get("full_name", "?")
        inviter = inv.get("inviter", {}).get("login", "?")
        perm = inv.get("permissions", "?")
        when = inv.get("created_at", "?")[:10]
        print(f"  [{inv['id']}] {repo}")
        print(f"     from {inviter}, perm={perm}, created {when}")
        print(f"     accept: accept.py --invitation {inv['id']}")
    return 0


def cmd_accept(args) -> int:
    if not args.invitation:
        print("error: --invitation <id> required (or use --list to see pending)",
              file=sys.stderr)
        return 1

    # Resolve the vault name and repo_full_name from the invitation if not given.
    invitation_id = args.invitation
    repo_full_name = args.repo
    vault_name = args.vault

    if not repo_full_name or not vault_name:
        try:
            invites = list_pending_invites()
        except RuntimeError as e:
            print(f"error fetching invites: {e}", file=sys.stderr)
            return 1
        match = next((i for i in invites if i["id"] == invitation_id), None)
        if not match:
            if not (repo_full_name and vault_name):
                print(f"error: invitation {invitation_id} not in pending list. "
                      f"Pass --repo and --vault explicitly if it's already accepted.",
                      file=sys.stderr)
                return 1
        else:
            repo_full_name = repo_full_name or match["repository"]["full_name"]
            vault_name = vault_name or derive_vault_name(repo_full_name)

    if vaults_yaml.vault_exists(vault_name):
        print(f"error: vault '{vault_name}' already in your vaults.yaml. "
              f"Pass --vault <different-name> to register under a new name.",
              file=sys.stderr)
        return 1

    vaults_root = Path(args.vaults_root or DEFAULT_VAULTS_ROOT).expanduser()
    vaults_root.mkdir(parents=True, exist_ok=True)

    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    response_path = RESPONSES_DIR / f"accept-{job_id}.json"

    job = {
        "job_id": job_id,
        "kind": "accept",
        "invitation_id": invitation_id,
        "repo_full_name": repo_full_name,
        "vault_name": vault_name,
        "vaults_root": str(vaults_root),
        "response_path": str(response_path),
    }
    job_path = write_job(job)

    print(f"invitation: {invitation_id}")
    print(f"repo:       {repo_full_name}")
    print(f"vault_name: {vault_name}")
    print(f"target:     {vaults_root / vault_name}")
    print(f"job:        {job_path}")

    try:
        result = send_to_agent(job_path)
        print(f"sent → agent {AGENT_NAME} (chat_id={result.get('chat_id', '?')})")
    except (httpx.HTTPError, RuntimeError) as e:
        print(f"! send failed: {e}", file=sys.stderr)
        return 1

    if args.wait:
        print(f"waiting up to {args.timeout}s for agent...")
        if wait_for_response(response_path, job_path, args.timeout):
            print(f"\n--- response ---\n")
            print(response_path.read_text())
        else:
            print(f"! timed out", file=sys.stderr)
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="list pending GitHub invitations")
    ap.add_argument("--invitation", type=int,
                    help="GitHub repository invitation id to accept")
    ap.add_argument("--vault", help="local vault name to register under "
                    "(default: derived from repo name)")
    ap.add_argument("--repo", help="repo full name (owner/name), needed if invitation "
                    "is already accepted on GitHub but not yet cloned locally")
    ap.add_argument("--vaults-root",
                    help=f"where to clone (default: {DEFAULT_VAULTS_ROOT})")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    if args.list:
        return cmd_list()
    return cmd_accept(args)


if __name__ == "__main__":
    sys.exit(main())
