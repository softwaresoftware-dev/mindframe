#!/usr/bin/env python3
"""vault-keeper scheduler — long-running loop that invokes keeper.py on a tick.

Sits in front of keeper.py so the whole thing can be supervised by
daemon-manager as a normal always-on daemon. Each tick:

  1. Run keeper.py with a fresh subprocess (so its own deps re-init clean)
  2. Sleep for VAULT_KEEPER_INTERVAL_S (default 3600 = 1h)
  3. Repeat forever

Idle ticks are cheap — keeper.py exits in milliseconds if no new transcripts
exist. The agent only spends tokens when there's actual work to do.

Environment:
  VAULT_KEEPER_INTERVAL_S — seconds between scans (default 3600)
  VAULT_KEEPER_TIMEOUT_S  — kill a stuck keeper.py after this (default 300)

All other env vars (vault path, session-bridge URL, etc.) are read by
keeper.py itself per its existing contract.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
KEEPER = SCRIPT_DIR / "keeper.py"

INTERVAL_S = int(os.environ.get("VAULT_KEEPER_INTERVAL_S", "3600"))
TIMEOUT_S = int(os.environ.get("VAULT_KEEPER_TIMEOUT_S", "300"))


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s vault-keeper.scheduler [%(levelname)s] %(message)s",
    )


log = logging.getLogger("vault-keeper.scheduler")
_stop = False


def _handle_signal(signum, _frame):
    global _stop
    log.info("received signal %d — exiting after current tick", signum)
    _stop = True


def run_once() -> int:
    try:
        result = subprocess.run(
            ["python3", str(KEEPER)],
            timeout=TIMEOUT_S,
            capture_output=True, text=True,
        )
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                log.info("keeper: %s", line)
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                log.warning("keeper: %s", line)
        return result.returncode
    except subprocess.TimeoutExpired:
        log.error("keeper.py exceeded %ds timeout — killed", TIMEOUT_S)
        return 124


def main() -> int:
    _configure_logging()
    log.info("scheduler up — interval=%ds, keeper=%s", INTERVAL_S, KEEPER)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    while not _stop:
        run_once()
        # Sleep in small chunks so signals are picked up promptly.
        slept = 0
        while slept < INTERVAL_S and not _stop:
            time.sleep(min(5, INTERVAL_S - slept))
            slept += 5

    log.info("scheduler exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
