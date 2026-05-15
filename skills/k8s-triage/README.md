# /mindframe:k8s-triage

The Kubernetes incident-triage skill (used in Act 3 of the Mindframe a16z Speedrun demo). Takes a Kubernetes incident and produces a structured triage summary the on-call human can act on.

## Inputs

| Name | Required | Description |
|---|---|---|
| `incident.id` | yes | e.g. `I-2026-05-12-001`. Used for cache keys + summary header. |
| `pod` | yes | Pod name, e.g. `payments-api-7c9f6b9d8b-xkqr2`. |
| `namespace` | no | Defaults to `production`. |

## Outputs

- `launch/stage/cache/<incident-id>/describe.txt` — `kubectl describe pod` capture
- `launch/stage/cache/<incident-id>/logs-previous.txt` — previous-container logs
- `launch/stage/cache/<incident-id>/commits.json` — recent commits (from `gh` or fixture)
- `launch/stage/cache/<incident-id>/summary.md` — final structured incident summary
- A post in the on-call channel (or a fallback file at `launch/raw/triage-output-<incident-id>.md` if no notification tool is wired)

## Dependencies

- **A2 (`launch/stage/kind-cluster/`)** — runs the live `kind` cluster the skill calls `kubectl` against. Optional during dry-validation: if A2 isn't up, pre-stage fixtures at `launch/stage/cache/<incident-id>/describe.txt` and `logs-previous.txt`.
- **A4 (`launch/stage/vault/`)** — the customer knowledge vault. Already complete. The grep contract is documented in `vault/README.md` and validated below.
- **Notification provider** — any plugin that satisfies the `notification` capability (notify-slack is the intended demo provider). The skill uses intent-based language; the resolver picks the available tool. If none, the skill writes to a fallback file.
- **`gh` CLI** — optional. The demo repo `demoacme/payments-api` does not exist on GitHub, so the skill always falls back to the offline fixture at `launch/stage/vault/fixtures/commits-payments-api.json`. The fallback path is part of the skill, not a workaround.

## Replay caching

Every external tool call writes its raw output to `launch/stage/cache/<incident-id>/`. On a second run with the same `incident.id`, the skill reads from cache instead of re-calling. This is what makes the demo idempotent across rehearsal takes — same incident in, same triage out, no flakiness from rate-limited APIs or transient kubectl state.

To force a fresh run, delete the cache directory:

```bash
rm -rf launch/stage/cache/I-2026-05-12-001
```

## Demo-rehearsal command

The exact invocation used during B1 (dress rehearsal) and B2 (recording):

```
/mindframe:k8s-triage incident.id=I-2026-05-12-001 pod=payments-api-7c9f6b9d8b-xkqr2 namespace=production
```

Expected runtime: 15–30 seconds end-to-end. Expected output: 7 numbered step lines, one prime-suspect commit identified (`abc123`), confidence `0.85`, summary file written, post to `#oncall-payments`.

## Dry-validation (without a live cluster)

Since A2 isn't validated yet, you can prove the offline path end-to-end:

```bash
mkdir -p launch/stage/cache/I-2026-05-12-001
# stage a fake describe output the skill can read
cat > launch/stage/cache/I-2026-05-12-001/describe.txt <<EOF
Last State:     Terminated
  Reason:       OOMKilled
  Exit Code:    137
EOF
echo "MemoryError: out of memory" > launch/stage/cache/I-2026-05-12-001/logs-previous.txt
# then run the skill — it should detect cache, skip kubectl, hit the fixture for commits
```

## Validated grep contract

These commands return the expected files. Confirmed at skill-authoring time:

```bash
grep -r OOMKilled launch/stage/vault/
# → runbooks/payments-api-OOM.md
# → incidents/I-2026-03-14-payments-OOM.md
# → services/payments-api.md (recent-incidents reference)

grep -lE "service: payments-api" launch/stage/vault/services/*.md
# → services/payments-api.md
```

## Failure modes the skill handles

| Mode | Source signal | Demo behavior |
|---|---|---|
| OOMKilled | `Reason: OOMKilled` in describe | Primary demo path. Matches `runbooks/payments-api-OOM.md`. |
| CrashLoopBackOff | `CrashLoopBackOff` in describe | Falls through to OOM runbook if the underlying exit code is 137; otherwise unknown. |
| ImagePullBackOff | `ImagePullBackOff` in describe | Reports as failure mode; no matching runbook in the demo vault. |
| unknown | none of the above | Reports `failure_mode=unknown` and hands off raw signal in the summary. |

## What this skill intentionally does NOT do

- **Execute the rollback.** The summary includes an "approve rollback" affordance; the actual `kubectl rollout undo` runs only when the on-call human approves. The dashboard (A8) wires that button.
- **Open a PR.** Sentry-triage does that; k8s-triage stops at "recommend + notify."
- **Write to the vault.** Incident notes are written by a separate post-resolution skill, not at triage time.

## Sibling

`../sentry-triage/SKILL.md` — closest existing template. K8s triage clones its shape (read → map → investigate → decide → notify) and adapts inputs/tools for the cluster failure path.
