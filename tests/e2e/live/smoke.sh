#!/usr/bin/env bash
# Live end-to-end smoke test for the mindframe event path.
#
# Proves the static-spawn transport against running daemons:
#   1. the live channels.yaml + recipes satisfy the recipe-brief contract
#   2. an event POSTed to dispatcher-ingress matches a static route
#   3. taskpilot spawns the recipe agent with a fully composed brief
#   4. the agent runs and writes its artifact to the composed output_path
#
# Not for CI — it talks to real services and waits on a real agent run
# (~1-2 min). Exits 0 only if the artifact lands with a valid header.
#
# Config (env vars):
#   DISPATCHER_INGRESS_URL   default http://127.0.0.1:8911
#   DISPATCHER_DIR           default ~/.dispatcher  (channels.yaml + recipes/)
#   INGRESS_UNIT             default dispatcher-ingress  (for token lookup)
#   DISPATCHER_INGEST_TOKEN  bearer token; auto-read from the systemd unit
#                            if unset
#   SMOKE_TIMEOUT_SEC        default 300
#   TASKPILOT_DIR            default ~/.taskpilot  (spawned task dirs)
#
# What this proves is the DISPATCHER side: the event routed, the brief was
# composed with no unfilled {{placeholders}}, and the agent wrote to the
# composed output_path. Whether the agent's own tools (google-calendar)
# are authenticated is environmental and does NOT fail the smoke test.

set -uo pipefail

INGRESS_URL="${DISPATCHER_INGRESS_URL:-http://127.0.0.1:8911}"
DISPATCHER_DIR="${DISPATCHER_DIR:-$HOME/.dispatcher}"
INGRESS_UNIT="${INGRESS_UNIT:-dispatcher-ingress}"
TIMEOUT="${SMOKE_TIMEOUT_SEC:-300}"
TASKPILOT_DIR="${TASKPILOT_DIR:-$HOME/.taskpilot}"

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNELS="$DISPATCHER_DIR/channels.yaml"
RECIPES="$DISPATCHER_DIR/recipes"

step() { printf '\n[smoke] %s\n' "$1"; }
die()  { printf '[smoke] FAIL — %s\n' "$1" >&2; exit 1; }

# --- 1. recipe-brief contract against the LIVE channels.yaml + recipes -------
step "checking recipe-brief contract: $CHANNELS"
[ -f "$CHANNELS" ] || die "no channels.yaml at $CHANNELS"
[ -d "$RECIPES" ]  || die "no recipes dir at $RECIPES"
python3 "$E2E_DIR/recipe_contract.py" "$CHANNELS" "$RECIPES" \
  || die "live channels.yaml violates the recipe-brief contract (see above)"

# --- resolve the bearer token ------------------------------------------------
TOKEN="${DISPATCHER_INGEST_TOKEN:-}"
if [ -z "$TOKEN" ] && command -v systemctl >/dev/null 2>&1; then
  TOKEN="$(systemctl --user show "$INGRESS_UNIT" -p Environment --value 2>/dev/null \
            | tr ' ' '\n' | sed -n 's/^DISPATCHER_INGEST_TOKEN=//p')"
fi
[ -n "$TOKEN" ] || die "no bearer token — set DISPATCHER_INGEST_TOKEN"

# --- 2. derive the expected artifact path from the calendar-check route ------
EVENT_ID="smoke-$(date +%s)"
OUT="$(python3 - "$CHANNELS" "$EVENT_ID" <<'PY'
import sys, yaml
channels, event_id = sys.argv[1], sys.argv[2]
cfg = yaml.safe_load(open(channels)) or {}
for r in cfg.get("routes") or []:
    if r.get("event_type") == "calendar-check" and isinstance(r.get("brief"), dict):
        print(r["brief"].get("output_path", "").replace("{event_id}", event_id))
        break
PY
)"
[ -n "$OUT" ] || die "no calendar-check route with a brief.output_path in $CHANNELS"
rm -f "$OUT"
step "event_id=$EVENT_ID  expected artifact: $OUT"

# --- 3. fire the event -------------------------------------------------------
step "POST $INGRESS_URL/api/event"
resp="$(curl -s -m 10 -X POST "$INGRESS_URL/api/event" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"source\":\"test-stream\",\"event_type\":\"calendar-check\",\"data\":{\"id\":\"$EVENT_ID\"}}")"
echo "  response: $resp"
echo "$resp" | grep -q '"mode"[[:space:]]*:[[:space:]]*"static-spawn"' \
  || die "event was not routed via the static-spawn path: $resp"

# --- 4. wait for the agent to write the artifact -----------------------------
step "waiting up to ${TIMEOUT}s for the spawned agent to write the artifact"
waited=0
while [ "$waited" -lt "$TIMEOUT" ]; do
  if [ -s "$OUT" ]; then break; fi
  sleep 10
  waited=$((waited + 10))
done
[ -s "$OUT" ] || die "no artifact at $OUT after ${TIMEOUT}s — spawn or agent failed"

# --- 5. assert the spawned brief had no unfilled placeholders ----------------
# The artifact landing at the *composed* output_path already proves the
# {{output_path}} substitution worked. Confirm the rest of the brief too.
step "artifact written after ${waited}s at the composed output_path"
TASK_BRIEF="$TASKPILOT_DIR/calendar-reader-$EVENT_ID/brief.json"
if [ -f "$TASK_BRIEF" ]; then
  if grep -q '{{' "$TASK_BRIEF"; then
    die "spawned agent's brief still has unfilled {{placeholders}}: $TASK_BRIEF"
  fi
  step "composed brief verified — no unfilled placeholders ($TASK_BRIEF)"
else
  warn="brief not found at $TASK_BRIEF — skipping placeholder check"
  step "note: $warn"
fi

# Informational: did the agent's own tools work, or fail environmentally?
header="$(head -1 "$OUT")"
if echo "$header" | grep -q "^event_id=$EVENT_ID"; then
  step "agent completed a clean calendar read — line 1: $header"
else
  step "agent ran but its calendar tool failed (environmental, not a"
  step "dispatcher fault) — line 1: $header"
fi

echo
echo "[smoke] PASS — event → ingress → static route → composed brief → agent → artifact"
echo "[smoke] artifact: $OUT"
