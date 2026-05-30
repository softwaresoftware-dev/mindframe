#!/usr/bin/env python3
"""Simulation runner — exercises the mindframe pipeline against a persona.

Phases:
  A. schema-creation: persona → schema.yaml via `claude -p`
  B. transcript-gen:  persona + topic → synthetic transcript via `claude -p`
  C. vault-keeper:    sandboxed vault + transcript → vault entries (via running vault-keeper agent)
  D. vault-query:     persona's expected questions → answers from the vault (via running vault-query agent)

Future phases:
  E. scored evaluation against persona's expected entity types + expected answers

Outputs live in ~/.mindframe-sim/<run-id>/ — sandboxed, never touches a real
vault. The run dir contains every prompt, every raw model output, and every
artifact so a human can review what happened.

Usage:
  run.py --persona vc-partner
  run.py --persona vc-partner --topic "Sourcing call writeup"
  run.py --persona vc-partner --skip-transcript    # phase A only
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PERSONAS_DIR = REPO_ROOT / "personas"
PROMPTS_DIR = REPO_ROOT / "prompts"
SIM_ROOT = Path(os.environ.get(
    "MINDFRAME_SIM_ROOT", str(Path.home() / ".mindframe-sim")))
KEEPER_SCRIPT = REPO_ROOT.parent / "vault_keeper" / "keeper.py"
QUERY_SCRIPT = REPO_ROOT.parent / "vault_query" / "query.py"


def run_claude(prompt: str, *, model: str = "sonnet") -> str:
    """Invoke `claude -p` non-interactively. Returns stdout.

    Unsets ANTHROPIC_API_KEY for the subprocess so claude falls back to the
    operator's Claude Code subscription auth instead of trying (and failing)
    against a stale key in the environment.
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("GH_TOKEN", None)  # otherwise claude may pick weird env

    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "text"],
        env=env, capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
    return result.stdout


def strip_code_fences(text: str) -> str:
    """If model wrapped output in ```...```, strip those."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text


def write_artifact(run_dir: Path, name: str, content: str) -> Path:
    p = run_dir / name
    p.write_text(content)
    return p


def phase_a_schema_creation(persona_text: str, run_dir: Path) -> str:
    """Generate schema.yaml from the persona."""
    template = (PROMPTS_DIR / "schema_creation.md").read_text()
    prompt = (
        f"{template}\n\n"
        f"## Persona\n\n{persona_text}\n\n"
        f"## Generate the schema now\n\n"
        f"Remember: YAML only, no fences, no prose."
    )
    write_artifact(run_dir, "phase-a-prompt.txt", prompt)
    print(f"  [A] calling claude for schema creation...")
    raw = run_claude(prompt)
    write_artifact(run_dir, "phase-a-raw.txt", raw)
    schema_yaml = strip_code_fences(raw)
    write_artifact(run_dir, "schema.yaml", schema_yaml)
    print(f"  [A] schema.yaml written ({len(schema_yaml):,} bytes)")
    return schema_yaml


def phase_b_transcript_generation(
    persona_text: str, topic: str, run_dir: Path,
) -> str:
    """Generate a synthetic working-session transcript."""
    template = (PROMPTS_DIR / "transcript_generation.md").read_text()
    prompt = (
        f"{template}\n\n"
        f"## Persona\n\n{persona_text}\n\n"
        f"## Working-session topic\n\n{topic}\n\n"
        f"## Generate the transcript now\n\n"
        f"Remember: alternating [USER] / [ASSISTANT] blocks, no JSON, no fences."
    )
    write_artifact(run_dir, "phase-b-prompt.txt", prompt)
    print(f"  [B] calling claude for transcript generation...")
    raw = run_claude(prompt)
    write_artifact(run_dir, "phase-b-raw.txt", raw)
    transcript = strip_code_fences(raw)
    write_artifact(run_dir, "transcript.txt", transcript)
    print(f"  [B] transcript.txt written ({len(transcript):,} bytes)")
    return transcript


def phase_c_vault_keeper(
    *, schema_yaml: str, transcript_text: str, persona: str, run_dir: Path,
    wait_seconds: int = 240,
) -> dict:
    """Phase C: stand up a sandboxed vault, fire the transcript at vault-keeper.

    Steps:
      1. Create <run_dir>/vault/, write schema.yaml, write transcript copy
      2. git init the vault, create entity-type directories per schema
      3. Use keeper.py --transcript-file mode to queue + send a job
      4. Wait for the agent to delete the queue file (signal of completion)
      5. Snapshot the resulting vault state into the run dir

    Returns a small dict describing what landed.
    """
    import yaml

    vault_dir = run_dir / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "schema.yaml").write_text(schema_yaml)

    # Parse schema to create entity-type directories. Skip single-file types
    # (those live as one .md at vault root, not a dir).
    try:
        schema = yaml.safe_load(schema_yaml) or {}
    except yaml.YAMLError as e:
        raise RuntimeError(f"phase-A schema doesn't parse: {e}")

    entity_dirs = []
    for entity in (schema.get("entities") or []):
        et = entity.get("type")
        naming = entity.get("naming", "slug")
        if not et:
            continue
        if naming == "single-file":
            # Single-file types get a placeholder file at vault root.
            (vault_dir / f"{et.capitalize()}.md").write_text(
                f"---\ntype: {et}\n---\n\n# {et.capitalize()}\n\n_(empty)_\n"
            )
        else:
            d = vault_dir / et.capitalize().replace("-", "")
            d.mkdir(exist_ok=True)
            entity_dirs.append(d.name)

    (vault_dir / "CATALOG.md").write_text(
        "# CATALOG\n\nIndex of vault entries by type. Updated by vault-keeper.\n\n"
        + "\n".join(f"## {n}\n\n_(none yet)_\n" for n in entity_dirs)
        + "\n"
    )

    (vault_dir / "README.md").write_text(
        f"# {persona} vault (simulation)\n\n"
        f"Created by mindframe-sim at {datetime.now(timezone.utc).isoformat()}.\n"
        f"Schema: see schema.yaml.\n"
    )

    # git init so freshness contract has something to pull/commit against.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=vault_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=vault_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.name=mindframe-sim",
         "-c", "user.email=sim@local",
         "commit", "-q", "-m", "init sandboxed vault from simulation"],
        cwd=vault_dir, check=True,
    )

    # Drop the transcript where keeper.py can read it.
    transcript_path = run_dir / "transcript-for-keeper.txt"
    transcript_path.write_text(transcript_text)

    # Sandboxed queue dir so simulation jobs don't pollute ~/.mindframe.
    queue_dir = run_dir / "queue"
    queue_dir.mkdir(exist_ok=True)

    print(f"  [C] sandboxed vault: {vault_dir}")
    print(f"  [C] firing transcript at vault-keeper agent...")

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    result = subprocess.run(
        ["python3", str(KEEPER_SCRIPT),
         "--transcript-file", str(transcript_path),
         "--vault-path", str(vault_dir),
         "--project-label", f"simulation:{persona}",
         "--queue-dir", str(queue_dir)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    write_artifact(run_dir, "phase-c-keeper-out.txt", result.stdout)
    write_artifact(run_dir, "phase-c-keeper-err.txt", result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"keeper.py failed (rc={result.returncode}):\n{result.stderr[:500]}"
        )

    # Find the queue file we just wrote (should be exactly one).
    job_files = sorted(queue_dir.glob("*.json"))
    if not job_files:
        raise RuntimeError("no job file in queue dir after keeper.py ran")
    job_path = job_files[0]
    print(f"  [C] job queued: {job_path.name}")
    print(f"  [C] waiting up to {wait_seconds}s for agent to delete job file...")

    waited = 0
    while job_path.exists() and waited < wait_seconds:
        time.sleep(5)
        waited += 5

    completed = not job_path.exists()
    print(f"  [C] agent {'completed' if completed else 'still working'} "
          f"after {waited}s")

    # Snapshot the vault state for review.
    vault_listing = subprocess.run(
        ["find", str(vault_dir), "-type", "f", "-not", "-path", "*/.git/*"],
        capture_output=True, text=True,
    ).stdout
    write_artifact(run_dir, "phase-c-vault-listing.txt", vault_listing)

    git_log = subprocess.run(
        ["git", "-C", str(vault_dir), "log", "--oneline", "--no-decorate"],
        capture_output=True, text=True,
    ).stdout
    write_artifact(run_dir, "phase-c-vault-git-log.txt", git_log)

    return {
        "vault_dir": str(vault_dir),
        "completed": completed,
        "waited_seconds": waited,
        "vault_files": len([l for l in vault_listing.splitlines() if l.strip()]),
        "commits": len([l for l in git_log.splitlines() if l.strip()]),
    }


def extract_expected_questions(persona_text: str) -> list[tuple[str, str]]:
    """Pull the numbered question list under `## Expected questions` from the
    persona file. Returns list of (label, question) pairs.

    Looks for entries shaped like:
        1. **Direct**: "What's the status of Procure.ai (Iota)?"
    """
    m = re.search(
        r"##\s+Expected questions.*?\n(.+?)(?=\n##\s|\Z)",
        persona_text, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []
    block = m.group(1)
    out: list[tuple[str, str]] = []
    for line_m in re.finditer(
        r"^\s*\d+\.\s+\*\*([^*]+)\*\*\s*:\s*\"([^\"]+)\"",
        block, re.MULTILINE,
    ):
        out.append((line_m.group(1).strip(), line_m.group(2).strip()))
    return out


def phase_d_vault_query(
    *, persona_text: str, vault_dir: Path, run_dir: Path,
    wait_seconds: int = 180,
) -> dict:
    """Phase D: run persona's expected questions against the produced vault.

    Reads the `## Expected questions` section from the persona file, fires
    each question at the vault-query agent (one at a time, sequential — the
    agent is single-threaded per session), waits for the response, and
    dumps all (question, answer) pairs into the run dir for review.
    """
    questions = extract_expected_questions(persona_text)
    if not questions:
        print(f"  [D] no expected questions in persona — skipping phase D")
        return {"questions": 0, "answered": 0}

    queue_dir = run_dir / "query-queue"
    responses_dir = run_dir / "query-responses"
    queue_dir.mkdir(exist_ok=True)
    responses_dir.mkdir(exist_ok=True)

    print(f"  [D] running {len(questions)} expected question(s) against vault...")

    results = []
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    for i, (label, q) in enumerate(questions, start=1):
        response_path = responses_dir / f"q{i:02d}-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}.md"
        print(f"  [D] Q{i} ({label}): {q[:70]}{'...' if len(q) > 70 else ''}")
        result = subprocess.run(
            ["python3", str(QUERY_SCRIPT),
             "--question", q,
             "--vault-path", str(vault_dir),
             "--response-path", str(response_path),
             "--queue-dir", str(queue_dir),
             "--responses-dir", str(responses_dir),
             "--wait", "--timeout", str(wait_seconds)],
            env=env, capture_output=True, text=True, timeout=wait_seconds + 30,
        )
        ok = response_path.is_file()
        results.append({
            "label": label, "question": q, "response_path": str(response_path),
            "answered": ok, "exit_code": result.returncode,
        })
        print(f"  [D] Q{i} → {'answered' if ok else 'FAILED'}")
        # Sequential: agent is single-threaded; sending the next while it's
        # still processing the previous would queue but make wait math hard.

    # Compose a single readable summary.
    summary_lines = ["# Phase D — vault-query results\n"]
    for i, r in enumerate(results, start=1):
        summary_lines.append(f"## Q{i}: {r['label']}\n")
        summary_lines.append(f"**Question:** {r['question']}\n")
        if r["answered"]:
            summary_lines.append("**Answer:**\n")
            summary_lines.append(Path(r["response_path"]).read_text())
        else:
            summary_lines.append("**Answer:** (no response — agent timed out or errored)\n")
        summary_lines.append("\n---\n")
    write_artifact(run_dir, "phase-d-summary.md", "\n".join(summary_lines))

    return {
        "questions": len(questions),
        "answered": sum(1 for r in results if r["answered"]),
    }


def default_topic(persona_text: str) -> str:
    """Pick the first 'Working-session topics' bullet from the persona file.

    Fallback if the persona's structure is unfamiliar.
    """
    m = re.search(
        r"##\s+Working-session topics.*?\n(.+?)(?=\n##\s|\Z)",
        persona_text, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return "A typical working session for this persona"
    block = m.group(1)
    first = re.search(r"^\s*(?:\d+\.|-|\*)\s+\*\*([^*]+)\*\*", block, re.MULTILINE)
    if first:
        return first.group(1).strip()
    first_plain = re.search(r"^\s*(?:\d+\.|-|\*)\s+(.+)$", block, re.MULTILINE)
    return first_plain.group(1).strip() if first_plain else "A typical working session"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--persona", required=True,
                    help="persona slug (e.g. vc-partner) from personas/")
    ap.add_argument("--topic", help="working-session topic; default: first from persona")
    ap.add_argument("--skip-transcript", action="store_true",
                    help="run phase A (schema) only")
    ap.add_argument("--skip-vault-keeper", action="store_true",
                    help="run phases A+B but skip phase C (no agent call)")
    ap.add_argument("--skip-vault-query", action="store_true",
                    help="skip phase D (no query agent call)")
    ap.add_argument("--reuse-run",
                    help="reuse phase A+B artifacts from this run dir; only run C+D")
    ap.add_argument("--reuse-vault",
                    help="reuse phase C vault from this run dir; only run D against it")
    ap.add_argument("--model", default="sonnet",
                    help="claude model to use (sonnet/opus/haiku)")
    ap.add_argument("--keeper-wait", type=int, default=240,
                    help="seconds to wait for vault-keeper agent (default 240)")
    args = ap.parse_args()

    persona_path = PERSONAS_DIR / f"{args.persona}.md"
    if not persona_path.is_file():
        print(f"error: persona not found: {persona_path}", file=sys.stderr)
        print(f"  available: {sorted(p.stem for p in PERSONAS_DIR.glob('*.md'))}",
              file=sys.stderr)
        return 1

    persona_text = persona_path.read_text()
    topic = args.topic or default_topic(persona_text)

    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{args.persona}-{uuid.uuid4().hex[:6]}"
    run_dir = SIM_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"mindframe-sim run: {run_id}")
    print(f"  persona: {args.persona}")
    print(f"  topic:   {topic}")
    print(f"  out:     {run_dir}")
    print()

    # Stash inputs for review.
    write_artifact(run_dir, "persona.md", persona_text)
    write_artifact(run_dir, "topic.txt", topic)
    write_artifact(run_dir, "meta.txt",
                   f"run_id={run_id}\n"
                   f"persona={args.persona}\n"
                   f"topic={topic}\n"
                   f"model={args.model}\n"
                   f"started_at={datetime.now(timezone.utc).isoformat()}\n")

    try:
        if args.reuse_run:
            reuse_dir = Path(args.reuse_run).expanduser()
            if not reuse_dir.is_dir():
                print(f"error: --reuse-run dir not found: {reuse_dir}", file=sys.stderr)
                return 1
            schema_yaml = (reuse_dir / "schema.yaml").read_text()
            transcript_text = (reuse_dir / "transcript.txt").read_text()
            # Copy into the new run dir so phase C has its own provenance.
            write_artifact(run_dir, "schema.yaml", schema_yaml)
            write_artifact(run_dir, "transcript.txt", transcript_text)
            print(f"  (reusing phase A+B from {reuse_dir.name})")
        else:
            schema_yaml = phase_a_schema_creation(persona_text, run_dir)
            transcript_text = ""
            if not args.skip_transcript:
                transcript_text = phase_b_transcript_generation(
                    persona_text, topic, run_dir,
                )

        vault_dir = None
        if args.reuse_vault:
            vault_dir = Path(args.reuse_vault).expanduser() / "vault"
            if not vault_dir.is_dir():
                # Maybe they passed the vault path directly.
                vault_dir = Path(args.reuse_vault).expanduser()
            if not vault_dir.is_dir() or not (vault_dir / "schema.yaml").is_file():
                print(f"error: --reuse-vault doesn't look like a vault dir: {vault_dir}",
                      file=sys.stderr)
                return 1
            print(f"  (reusing phase C vault from {vault_dir})")
        elif not args.skip_vault_keeper and transcript_text:
            result_c = phase_c_vault_keeper(
                schema_yaml=schema_yaml, transcript_text=transcript_text,
                persona=args.persona, run_dir=run_dir,
                wait_seconds=args.keeper_wait,
            )
            vault_dir = Path(result_c["vault_dir"])

        if vault_dir and not args.skip_vault_query:
            phase_d_vault_query(
                persona_text=persona_text, vault_dir=vault_dir,
                run_dir=run_dir,
            )
    except RuntimeError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1

    print(f"\nrun complete. artifacts: {run_dir}")
    print(f"  ls {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
