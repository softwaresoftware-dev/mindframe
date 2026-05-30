#!/usr/bin/env python3
"""vault-keeper — scheduled trigger that hands transcript work to a long-running agent.

v1.1 architecture (session-bridge edition):
- Reads Claude Code session transcripts (~/.claude/projects/<encoded>/*.jsonl)
  modified since the last successful run.
- Extracts the ~2% of bytes that are actual user+assistant text (skipping
  tool calls, file snapshots, attachments).
- Writes a job file per project to a queue dir: ~/.mindframe/vault-keeper/queue/.
- Sends a channel message to the `vault-keeper` taskpilot session via
  session-bridge, pointing it at the job file. Fire-and-forget — the agent
  decides what to remember and writes the memory entries itself.

This script holds zero Anthropic credentials. The agent uses Claude Code's
subscription auth (the same auth every taskpilot service-kind agent uses).

Usage:
  keeper.py                              # process transcripts since last run
  keeper.py --since 2026-05-29T12:00:00  # ad-hoc backfill
  keeper.py --dry-run                    # extract + write job files, skip the send
  keeper.py --project PATH               # scope to one project dir
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# --------------------------- config ---------------------------

PROJECTS_DIR = Path(os.environ.get(
    "CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))
STATE_DIR = Path(os.environ.get(
    "VAULT_KEEPER_STATE_DIR", str(Path.home() / ".mindframe" / "vault-keeper")))
STATE_PATH = STATE_DIR / "state.json"
QUEUE_DIR = STATE_DIR / "queue"
SESSION_BRIDGE_URL = os.environ.get(
    "SESSION_BRIDGE_URL", "http://127.0.0.1:8910")
AGENT_NAME = os.environ.get("VAULT_KEEPER_AGENT_NAME", "vault-keeper")


# --------------------------- transcript extraction ---------------------------


@dataclass
class TranscriptChunk:
    role: str           # "user" | "assistant"
    text: str
    timestamp: str      # ISO 8601, best-effort


def extract_chunks(transcript_path: Path) -> list[TranscriptChunk]:
    """Pull user+assistant text from a Claude Code transcript jsonl file.

    Skips tool_use, tool_result, file-history-snapshot, permission-mode,
    system, and attachment entries — those are the bulk of the bytes but
    no narrative signal.
    """
    out: list[TranscriptChunk] = []
    try:
        text = transcript_path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = obj.get("type")
        if kind not in ("user", "assistant"):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        ts = obj.get("timestamp") or obj.get("created_at") or ""
        if isinstance(content, str):
            if content.strip():
                out.append(TranscriptChunk(role=kind, text=content, timestamp=ts))
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text") or ""
                    if t.strip():
                        text_parts.append(t)
            if text_parts:
                out.append(TranscriptChunk(
                    role=kind, text="\n".join(text_parts), timestamp=ts,
                ))
    return out


def find_recent_transcripts(project_dir: Path, since: datetime) -> list[Path]:
    if not project_dir.is_dir():
        return []
    since_ts = since.timestamp()
    out = [p for p in project_dir.glob("*.jsonl")
           if _safe_mtime(p) > since_ts]
    out.sort(key=_safe_mtime)
    return out


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def format_chunks(chunks: list[TranscriptChunk]) -> str:
    parts = []
    for c in chunks:
        ts_short = c.timestamp[:19] if c.timestamp else ""
        prefix = f"[{c.role.upper()}{(' ' + ts_short) if ts_short else ''}]"
        parts.append(f"{prefix}\n{c.text}\n")
    return "\n".join(parts)


def decode_project_path(encoded: str) -> str:
    """Lossy decode of Claude's project dir name back to a cwd label."""
    return encoded.replace("-", "/") if encoded else encoded


# --------------------------- state + queue ---------------------------


def load_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def write_job(
    *, vault_path: Path, transcript_text: str, project_label: str,
    since: datetime, until: datetime, queue_dir: Path | None = None,
) -> Path:
    """Write a job file + transcript snapshot to the queue. Returns job path.

    The job lands in `queue_dir` if provided (used by simulations to keep
    runs sandboxed), otherwise in the default ~/.mindframe/vault-keeper/queue/.
    """
    qdir = queue_dir or QUEUE_DIR
    qdir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    transcript_path = qdir / f"{job_id}.transcript.txt"
    transcript_path.write_text(transcript_text)
    job = {
        "job_id": job_id,
        "vault_path": str(vault_path),
        "transcript_text_path": str(transcript_path),
        "project_label": project_label,
        "since": since.isoformat(),
        "until": until.isoformat(),
    }
    job_path = qdir / f"{job_id}.json"
    job_path.write_text(json.dumps(job, indent=2))
    return job_path


# --------------------------- session-bridge dispatch ---------------------------


def send_to_agent(job_path: Path) -> dict:
    """Fire-and-forget message to the vault-keeper agent."""
    url = f"{SESSION_BRIDGE_URL}/sessions/{AGENT_NAME}/message"
    body = {"text": f"vault-keeper job: {job_path}"}
    r = httpx.post(url, json=body, timeout=10)
    if r.status_code == 409:
        raise RuntimeError(
            f"agent '{AGENT_NAME}' has no channel registered — is it running? "
            f"see: ~/.taskpilot/{AGENT_NAME}/. Try: spawn it via taskpilot."
        )
    if r.status_code == 404:
        raise RuntimeError(
            f"agent '{AGENT_NAME}' is not in the session mesh. "
            f"Spawn it via taskpilot first (see vault_keeper/agent/CLAUDE.md)."
        )
    r.raise_for_status()
    return r.json()


# --------------------------- main ---------------------------


def resolve_vault_path() -> Path | None:
    """Read vault_path from mindframe plugin config in ~/.claude/settings.json."""
    settings = Path.home() / ".claude" / "settings.json"
    if not settings.is_file():
        return None
    try:
        data = json.loads(settings.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    vp = (data.get("pluginConfigs", {})
              .get("mindframe", {})
              .get("options", {})
              .get("vault_path"))
    return Path(vp).expanduser() if vp else None


def process_project(
    project_dir: Path, since: datetime, until: datetime, *,
    vault_path: Path, dry_run: bool,
) -> int:
    """Returns count of jobs dispatched (0 or 1 per project per run)."""
    transcripts = find_recent_transcripts(project_dir, since)
    if not transcripts:
        return 0
    chunks: list[TranscriptChunk] = []
    for t in transcripts:
        chunks.extend(extract_chunks(t))
    if not chunks:
        return 0

    text = format_chunks(chunks)
    print(f"  project: {project_dir.name}")
    print(f"  text size: {len(text):,} chars from {len(chunks)} chunks "
          f"across {len(transcripts)} transcript(s)")

    job_path = write_job(
        vault_path=vault_path, transcript_text=text,
        project_label=decode_project_path(project_dir.name),
        since=since, until=until,
    )
    print(f"  job: {job_path.name}")

    if dry_run:
        print(f"  [dry-run] skipping send to agent")
        return 1

    try:
        result = send_to_agent(job_path)
        print(f"  sent → agent {AGENT_NAME} (chat_id={result.get('chat_id', '?')})")
    except (httpx.HTTPError, RuntimeError) as e:
        print(f"  ! send failed: {e}", file=sys.stderr)
        # Leave the job file on disk so the agent can be pointed at it later.
        return 0
    return 1


def process_direct_transcript(
    *, transcript_path: Path, vault_path: Path, project_label: str,
    queue_dir: Path | None, dry_run: bool,
) -> int:
    """Skip the Claude-Code-transcript scan entirely. Used by simulations:
    you already have a pre-extracted transcript text file, you want it
    routed to vault-keeper against a specific vault. One job, one shot.
    """
    text = transcript_path.read_text()
    print(f"  transcript: {transcript_path} ({len(text):,} chars)")
    print(f"  vault:      {vault_path}")

    now = datetime.now(timezone.utc)
    job_path = write_job(
        vault_path=vault_path, transcript_text=text,
        project_label=project_label,
        since=now, until=now,
        queue_dir=queue_dir,
    )
    print(f"  job: {job_path}")

    if dry_run:
        print(f"  [dry-run] skipping send to agent")
        return 1

    try:
        result = send_to_agent(job_path)
        print(f"  sent → agent {AGENT_NAME} (chat_id={result.get('chat_id', '?')})")
    except (httpx.HTTPError, RuntimeError) as e:
        print(f"  ! send failed: {e}", file=sys.stderr)
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", help="ISO timestamp; default: state or 24h ago")
    ap.add_argument("--dry-run", action="store_true",
                    help="write job files but don't message the agent")
    ap.add_argument("--project", help="basename or full path of one project dir")
    ap.add_argument("--vault-path",
                    help="override vault_path (default: read from mindframe settings)")
    ap.add_argument("--transcript-file",
                    help="bypass jsonl scan, send this pre-extracted transcript directly")
    ap.add_argument("--project-label", default="manual",
                    help="label for the work (used in commit messages, etc.)")
    ap.add_argument("--queue-dir",
                    help="override queue dir (used by simulations to sandbox)")
    args = ap.parse_args()

    vault_path = (Path(args.vault_path).expanduser() if args.vault_path
                  else resolve_vault_path())
    if not vault_path:
        print("error: no vault_path — set pluginConfigs.mindframe.options.vault_path "
              "in ~/.claude/settings.json or pass --vault-path", file=sys.stderr)
        return 1

    queue_dir = Path(args.queue_dir).expanduser() if args.queue_dir else None

    # Direct-transcript mode (used by simulations).
    if args.transcript_file:
        tpath = Path(args.transcript_file).expanduser()
        if not tpath.is_file():
            print(f"error: transcript file not found: {tpath}", file=sys.stderr)
            return 1
        print(f"vault-keeper direct-transcript mode")
        sent = process_direct_transcript(
            transcript_path=tpath, vault_path=vault_path,
            project_label=args.project_label,
            queue_dir=queue_dir, dry_run=args.dry_run,
        )
        print(f"\nsummary: {sent} job(s) sent")
        return 0

    state = load_state()

    if args.since:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    elif state.get("last_run_at"):
        since = datetime.fromisoformat(state["last_run_at"])
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    until = datetime.now(timezone.utc)

    print(f"vault-keeper run: scanning transcripts since {since.isoformat()}")
    if args.dry_run:
        print("  (dry-run: jobs queued, agent NOT messaged)")

    if not PROJECTS_DIR.is_dir():
        print(f"error: {PROJECTS_DIR} does not exist", file=sys.stderr)
        return 1

    if args.project:
        project_dirs = ([Path(args.project)] if "/" in args.project
                        else [PROJECTS_DIR / args.project])
        project_dirs = [p for p in project_dirs if p.is_dir()]
    else:
        project_dirs = [p for p in PROJECTS_DIR.iterdir() if p.is_dir()]

    sent = 0
    for pd in project_dirs:
        if not find_recent_transcripts(pd, since):
            continue
        sent += process_project(
            pd, since, until,
            vault_path=vault_path, dry_run=args.dry_run,
        )

    print(f"\nsummary: {sent} job(s) sent")

    if not args.dry_run:
        state["last_run_at"] = until.isoformat()
        state["last_run_sent"] = sent
        save_state(state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
