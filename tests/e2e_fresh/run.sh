#!/usr/bin/env bash
# Tier 3 fresh-install dry-run entry point.
#
# Builds the harness image, runs it against the current main branch by
# default, prints the report. Pass MINDFRAME_REF / DISPATCHER_REF to test
# specific commits or branches.
#
# Usage:
#   ./run.sh                       # main of both repos
#   MINDFRAME_REF=v0.4.0 ./run.sh
set -euo pipefail

cd "$(dirname "$0")"

MINDFRAME_REF="${MINDFRAME_REF:-main}"
DISPATCHER_REF="${DISPATCHER_REF:-main}"
TAG="${TAG:-mindframe-tier3:latest}"
REPORT_HOST_PATH="${REPORT_HOST_PATH:-/tmp/mindframe-tier3-report.json}"

echo "Tier 3 — fresh-install dry-run"
echo "  mindframe:  $MINDFRAME_REF"
echo "  dispatcher: $DISPATCHER_REF"
echo "  image:      $TAG"
echo

echo "[build] docker build $TAG"
docker build -t "$TAG" .

echo
echo "[run] booting clean container"
CONTAINER_ID=$(docker create \
    -e MINDFRAME_REF="$MINDFRAME_REF" \
    -e DISPATCHER_REF="$DISPATCHER_REF" \
    -e REPORT_PATH="/tmp/report.json" \
    "$TAG")
trap "docker rm -f $CONTAINER_ID >/dev/null 2>&1 || true" EXIT
RC=0
docker start -a "$CONTAINER_ID" || RC=$?
# Pull the report out via docker cp — avoids the volume-mount-creates-dir trap.
if docker cp "$CONTAINER_ID:/tmp/report.json" "$REPORT_HOST_PATH" 2>/dev/null; then
    echo
    echo "Report:"
    cat "$REPORT_HOST_PATH" | python3 -m json.tool 2>/dev/null || cat "$REPORT_HOST_PATH"
else
    echo "(no report produced — harness crashed before writing it)"
fi
exit $RC
