#!/usr/bin/env python3
"""mindframe telemetry emitter — POSTs structured events to the central endpoint.

Honors two opt-out paths, checked on every call:
1. MINDFRAME_TELEMETRY environment variable: 0 / false / no / off → silently exit
2. ~/.claude/settings.json pluginConfigs.mindframe.options.telemetry: false → silently exit

Failures are non-blocking — telemetry should never break install.

Usage:
  telemetry.py event --type phase-start --data '{"phase":3}'
  telemetry.py event --type pack-activated --data '{"pack":"software-ops"}'
  telemetry.py event --type free-text-answer --data '{"question":"...","answer":"..."}'
  telemetry.py event --type install-error --data '{"phase":7,"error":"..."}'

The agent calls this at well-defined moments — phase starts/completions,
pack activations, free-text discovery answers, errors. Endpoint accepts any
JSON; no schema enforcement on the server side.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ENDPOINT = os.environ.get(
    "MINDFRAME_TELEMETRY_URL",
    "https://telemetry.softwaresoftware.dev/api/freeform/mindframe:setup",
)


def _settings_opt_out() -> bool:
    """Read pluginConfigs.mindframe.options.telemetry from settings.json.
    Returns True if explicitly opted out, False otherwise (including if
    the file or key is missing — default is opt-in)."""
    p = Path.home() / ".claude" / "settings.json"
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    val = (data.get("pluginConfigs", {})
               .get("mindframe", {})
               .get("options", {})
               .get("telemetry"))
    # False / "false" / 0 means opt-out; anything else (including missing) = opt-in.
    return val is False or (isinstance(val, str) and val.lower() in ("false", "0", "no", "off"))


def _env_opt_out() -> bool:
    val = os.environ.get("MINDFRAME_TELEMETRY", "").strip().lower()
    return val in ("0", "false", "no", "off")


def opted_out() -> bool:
    return _env_opt_out() or _settings_opt_out()


def emit(event_type: str, data: dict | None = None) -> int:
    """Returns: 0 if opted out (no-op), 1 if sent successfully, 2 if send failed.
    Never raises. Never blocks longer than ~3s."""
    if opted_out():
        return 0
    body = {"event_type": event_type, "data": data or {}}
    try:
        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return 1 if 200 <= resp.status < 300 else 2
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("event", help="emit one event")
    e.add_argument("--type", required=True, help="event_type tag")
    e.add_argument("--data", default="{}", help="JSON-encoded data field")

    s = sub.add_parser("status", help="print opt-in/out status (no event sent)")
    s.set_defaults()

    args = ap.parse_args()

    if args.cmd == "status":
        if opted_out():
            print(f"telemetry: OPTED OUT "
                  f"(env={_env_opt_out()}, settings={_settings_opt_out()})")
            return 0
        print(f"telemetry: opt-in → {ENDPOINT}")
        return 0

    if args.cmd == "event":
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as ex:
            print(f"error: --data is not valid JSON: {ex}", file=sys.stderr)
            return 1
        result = emit(args.type, data)
        # Surface result to caller for debugging but never fail the call.
        if result == 0:
            print(f"telemetry: skipped (opted out)")
        elif result == 1:
            print(f"telemetry: sent ({args.type})")
        else:
            print(f"telemetry: send failed (non-blocking)", file=sys.stderr)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
