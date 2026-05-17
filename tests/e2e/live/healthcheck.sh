#!/usr/bin/env bash
# Live health check for the mindframe bundle daemons.
#
# Run this against a real install (not in CI — it talks to running
# services). Exits 0 only if every critical daemon is up.
#
# Config (env vars, with defaults matching the plugin defaults):
#   DISPATCHER_INGRESS_URL   default http://127.0.0.1:8911
#   SESSION_BRIDGE_URL       default http://127.0.0.1:8910
#   DISPATCHER_UNIT          default dispatcher           (systemd --user unit)
#   INGRESS_UNIT             default dispatcher-ingress   (systemd --user unit)

set -uo pipefail

INGRESS_URL="${DISPATCHER_INGRESS_URL:-http://127.0.0.1:8911}"
SESSION_BRIDGE_URL="${SESSION_BRIDGE_URL:-http://127.0.0.1:8910}"
DISPATCHER_UNIT="${DISPATCHER_UNIT:-dispatcher}"
INGRESS_UNIT="${INGRESS_UNIT:-dispatcher-ingress}"

fail=0

pass_line() { printf '  [ OK ]  %-22s %s\n' "$1" "$2"; }
fail_line() { printf '  [FAIL]  %-22s %s\n' "$1" "$2"; fail=1; }
warn_line() { printf '  [warn]  %-22s %s\n' "$1" "$2"; }

echo "mindframe bundle — live health check"
echo

# --- dispatcher-ingress: HTTP /api/health must return ok ---------------------
health="$(curl -s -m 5 "$INGRESS_URL/api/health" 2>/dev/null)"
if echo "$health" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
  pass_line "dispatcher-ingress" "$INGRESS_URL — $health"
else
  fail_line "dispatcher-ingress" "$INGRESS_URL — no healthy response"
fi

# --- session-bridge: any HTTP response means the server is listening ---------
code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$SESSION_BRIDGE_URL/" 2>/dev/null)"
if [ "${code:-000}" != "000" ]; then
  pass_line "session-bridge" "$SESSION_BRIDGE_URL — HTTP $code"
else
  fail_line "session-bridge" "$SESSION_BRIDGE_URL — not listening"
fi

# --- systemd --user units ----------------------------------------------------
if command -v systemctl >/dev/null 2>&1; then
  for unit in "$DISPATCHER_UNIT" "$INGRESS_UNIT"; do
    state="$(systemctl --user is-active "$unit" 2>/dev/null)"
    if [ "$state" = "active" ]; then
      pass_line "$unit (systemd)" "active"
    else
      fail_line "$unit (systemd)" "${state:-not found}"
    fi
  done
else
  warn_line "systemd" "systemctl unavailable — skipped unit checks"
fi

# --- tmux: taskpilot spawns agents into tmux sessions ------------------------
if command -v tmux >/dev/null 2>&1; then
  pass_line "tmux" "$(tmux -V 2>/dev/null)"
else
  fail_line "tmux" "not installed — taskpilot cannot spawn agents"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "RESULT: all bundle daemons healthy"
else
  echo "RESULT: one or more daemons are down — see [FAIL] lines above"
fi
exit "$fail"
