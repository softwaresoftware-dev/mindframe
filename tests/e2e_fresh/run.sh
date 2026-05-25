#!/usr/bin/env bash
# Tier 3 fresh-install dry-run wrapper.
#
# Runs the native Python harness (no Docker). For Windows runners use the
# Python script directly: `python tests/e2e_fresh/harness.py`.
#
# Usage:
#   ./run.sh                       # main of both repos
#   MINDFRAME_REF=v0.4.0 ./run.sh
#   REPORT_PATH=/tmp/mine.json ./run.sh
set -euo pipefail

cd "$(dirname "$0")"

exec python3 harness.py "$@"
