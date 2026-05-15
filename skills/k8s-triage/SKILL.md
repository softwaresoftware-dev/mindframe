---
name: k8s-triage
description: Triage a Kubernetes incident end-to-end — capture pod state from kubectl, identify the failure mode (OOMKilled / CrashLoopBackOff / etc.), grep the customer vault for matching runbooks and service ownership, cross-reference recent commits to find a prime-suspect change, and post a structured incident summary with a recommended action and rollback affordance. Use when invoked by the dispatcher after a PagerDuty / cluster alert fires, or manually with phrases like "triage a kubernetes incident", "investigate this pod", "k8s-triage", or "/mindframe:k8s-triage incident.id=<id> pod=<pod>".
allowed-tools: Bash, Read, Grep, Glob, Write
---

# Mindframe — K8s Triage

You are mindframe's Kubernetes incident-triage skill. A Kubernetes pod is in trouble. Your job: read the cluster, read the vault, read the recent commits, name the most likely cause with a confidence number, and post a summary the on-call human can act on in one click.

The terminal output of this skill is part of a screen recording. Make every step legible. Use `[step N/7] <action>...` log lines. No spinners, no progress bars, no decorative output. The viewer should be able to follow your reasoning as you go.

## Inputs

- `incident.id` — e.g. `I-2026-05-12-001` (used for cache keys and the summary header)
- `pod` — pod name, e.g. `payments-api-7c9f6b9d8b-xkqr2`
- `namespace` — optional, defaults to `production`
- Customer vault path: `launch/stage/vault/` relative to the mindframe plugin root (or `CLAUDE_PLUGIN_OPTION_VAULT_PATH` if set)
- Demo repo: `demoacme/payments-api` (does not exist on GitHub — fixture fallback documented below)

## Cache contract (replayability)

Every external call writes its output to `launch/stage/cache/<incident-id>/`. On subsequent runs with the same `incident.id`, read from cache instead of re-calling kubectl / gh. This makes the demo idempotent across rehearsal takes.

- `cache/<incident-id>/describe.txt`
- `cache/<incident-id>/logs-previous.txt`
- `cache/<incident-id>/commits.json`
- `cache/<incident-id>/summary.md`

If a cache file exists and is non-empty, prefer it. Otherwise, call the tool, write the result, then continue.

## Flow

### [step 1/7] capture pod state

Run `kubectl describe pod <pod> -n <namespace>` and `kubectl logs <pod> -n <namespace> --previous --tail=200`. Write both to the cache. Print the first 20 lines of each to the terminal so the viewer sees real signal.

```bash
kubectl describe pod "$POD" -n "$NS" > cache/$ID/describe.txt
kubectl logs "$POD" -n "$NS" --previous --tail=200 > cache/$ID/logs-previous.txt
```

If `kubectl` is unavailable or the pod doesn't exist, look for a pre-staged fixture at `launch/stage/cache/<incident-id>/describe.txt` already on disk and use that. Never fabricate output.

### [step 2/7] identify failure mode

Grep `describe.txt` for known failure modes. Match in this order — first hit wins:

1. `OOMKilled` → `failure_mode=OOMKilled`
2. `CrashLoopBackOff` → `failure_mode=CrashLoopBackOff`
3. `ImagePullBackOff` → `failure_mode=ImagePullBackOff`
4. `Error` / `Exit Code:` non-zero → `failure_mode=CrashLoopBackOff` (generic)
5. Otherwise → `failure_mode=unknown`

Print the matched line so the viewer sees *why* you chose this mode.

```bash
grep -E 'OOMKilled|CrashLoopBackOff|ImagePullBackOff|Exit Code' cache/$ID/describe.txt | head -5
```

For the demo, the expected match is `OOMKilled` + `Exit Code: 137`.

### [step 3/7] identify affected service

Extract the service name from the pod name (strip ReplicaSet hash suffixes). `payments-api-7c9f6b9d8b-xkqr2` → `payments-api`. Confirm by looking at the `app=` label in the describe output.

```bash
SERVICE=$(echo "$POD" | sed -E 's/-[a-z0-9]{8,10}-[a-z0-9]{5}$//')
```

### [step 4/7] grep the vault for runbook + ownership

Search the vault for runbooks matching the failure mode and the affected service. The grep contract is documented in `launch/stage/vault/README.md`.

```bash
VAULT="${CLAUDE_PLUGIN_OPTION_VAULT_PATH:-launch/stage/vault}"
grep -lrE "$FAILURE_MODE" "$VAULT/runbooks/"
grep -lrE "service: $SERVICE" "$VAULT/services/"
grep -lrE "$FAILURE_MODE" "$VAULT/incidents/"
```

Expected on the demo path: `runbooks/payments-api-OOM.md` + `services/payments-api.md` + `incidents/I-2026-03-14-payments-OOM.md`. Print each match as a separate log line. If multiple runbooks match, pick the one whose `service:` frontmatter equals the affected service.

Pull the runbook's `severity_if_unaddressed`, `slack`, and `notify` fields from frontmatter — these go into the summary.

### [step 5/7] pull recent commits (with fixture fallback)

Try `gh api` first. The demo repo doesn't exist on GitHub, so this will fail. Swallow the error and fall back to the offline fixture. Implement the fallback explicitly:

```bash
REPO="demoacme/payments-api"
if gh api "repos/$REPO/commits" --jq '.[0:10]' > cache/$ID/commits.json 2>/dev/null && \
   [ -s cache/$ID/commits.json ]; then
  echo "[step 5/7] pulled commits from gh api"
else
  cp launch/stage/vault/fixtures/commits-payments-api.json cache/$ID/commits.json
  echo "[step 5/7] gh unavailable — using offline fixture (commits-payments-api.json)"
fi
```

Print the top 5 commits as `<short_sha> <date> <message>` so the viewer can read them.

### [step 6/7] cross-reference commits vs failure mode

For each commit, score it against the failure mode using the runbook's "Known failure pattern" section as the hypothesis.

For `OOMKilled` on `payments-api`, look for:

- Changes to `WORKER_CONCURRENCY` env var
- Changes to `--workers` gunicorn flag
- Changes to `resources.limits.memory` or `resources.requests.memory`
- Commit message containing `concurrency`, `worker`, `memory`, `resource`

A commit that raises concurrency or worker count without a matching memory bump is the prime suspect with confidence **0.85**. A commit that only touches one of memory/concurrency is a secondary suspect (confidence 0.5). Unrelated commits get 0.0.

For the demo fixture: the `_demo_flag: "PRIME_SUSPECT"` marker on the top commit is the deterministic signal. If you see that flag in a commit, treat it as the prime suspect with confidence 0.85, regardless of other heuristics. (The flag is only present in the offline fixture — real `gh api` output will not have it, so the heuristic above takes over.)

Print one line per evaluated commit with its score:

```
  abc123  2026-05-12  bump worker concurrency from 12 to 32   PRIME SUSPECT (0.85)
  abc122  2026-05-09  fix flaky /refund integration test       unrelated (0.0)
```

### [step 7/7] compose summary + post to on-call

Write a structured incident summary to `cache/$ID/summary.md`. The shape matches the recommendation card in `launch/brief.md` Act 3:

```markdown
# Incident <incident-id> — <service> <failure_mode>

**Severity:** <from runbook>
**Pod:** <pod>
**Failure mode:** <failure_mode> (exit code 137 if OOMKilled)

## Hypothesis

<one sentence root-cause statement>. Confidence: **<0.0–1.0>**.

## Prime suspect

- Commit `<short_sha>` by `<author>` on `<date>`
- Message: <message>
- Why: <which heuristic matched — e.g. "WORKER_CONCURRENCY raised 12 → 32; memory unchanged at 512Mi">

## Recommended action

**Roll back `<short_sha>`.** This matches the failure pattern in [<runbook-path>](<runbook-path>) and the prior incident [<incident-path>](<incident-path>).

```bash
kubectl rollout undo deploy/<service> -n <namespace>
```

Alternate (if rollback unsafe): bump memory limit to `1Gi`. See runbook §"Slow: bump memory".

## Runbook

<runbook-path>

## Approve rollback

Reply `approve` in <slack-channel> to execute the rollback. Reply `hold` to keep investigating.
```

Then post it to the on-call channel.

> Post the incident summary at `cache/<incident-id>/summary.md` to the on-call channel (`#oncall-payments`, or whatever the service note's `slack:` field says) along with a one-line headline. Use an available skill or tool. If no notification tool is available, write the summary to `launch/raw/triage-output-<incident-id>.md` instead so the rehearsal can verify it.

Print `[done] incident <incident-id> triaged in <elapsed>s — summary at cache/<incident-id>/summary.md` as the final line.

## Hard rules

- Never fabricate kubectl output, commit shas, or log lines. If a tool fails and there's no cache or fixture, stop and write a blocker to `launch/blockers/a3-runtime-<incident-id>.md`.
- Idempotent. Running this skill twice with the same `incident.id` produces the same summary, because step-by-step output is cached.
- Do not hardcode provider names (Slack MCP, etc.). Use intent-based language for the notification step. The dispatcher has already arranged the right tools in the agent's context.
- Output is for a screen recording. Keep log lines short, declarative, present tense. One line per substep.

## Reference

- Vault schema: `launch/stage/vault/README.md`
- Brief (Act 3 card shape): `launch/brief.md`
- Sentry-triage sibling skill: `../sentry-triage/SKILL.md`
- Orchestration plan step A3: `launch/orchestration-plan.md`
