# ABOUTME: Turns on faulthandler so a native crash (SIGSEGV, SIGABRT, SIGBUS, SIGFPE) leaves a
# ABOUTME: traceback, and SIGUSR1 dumps every thread. Called at import, so it runs under uvicorn too.
"""Crash telemetry for the backend.

The backend has died before with no traceback and no crash report. faulthandler writes one
from inside the signal handler, which only works if the file is already open and unbuffered
enough: on a SIGSEGV Python can't flush, so the handle is opened line-buffered and kept at
module scope for the life of the process.

Where it writes, first that works:

1. ``JONA_FAULT_LOG``: a path, or ``off`` to leave faulthandler alone (the test suite does).
2. ``/var/lib/jonaai/jonaai-backend-fault.log``: the production state directory, which the
   service may write and which survives restarts.
3. ``/tmp/jonaai-backend-fault.log``: a development machine. Not used where the service
   has a private /tmp, since that is deleted when the service stops, which is when a crash
   happens.
4. stderr: under systemd that is the journal, so a traceback still survives.

Nothing here can stop the server from starting: an unusable path moves on to the next.
"""

from __future__ import annotations

import faulthandler
import os
import signal
import sys
from pathlib import Path
from typing import IO, Optional

FAULT_LOG_NAME = "jonaai-backend-fault.log"
PRODUCTION_DIR = Path("/var/lib/jonaai")
DEVELOPMENT_DIR = Path("/tmp")

# Load-bearing: kept open for the life of the process (see the module docstring).
_handle: Optional[IO[str]] = None
_target: Optional[str] = None


def _candidates() -> list:
    configured = os.environ.get("JONA_FAULT_LOG", "").strip()
    if configured:
        return [Path(configured).expanduser()]
    return [PRODUCTION_DIR / FAULT_LOG_NAME, DEVELOPMENT_DIR / FAULT_LOG_NAME]


def _open(path: Path) -> Optional[IO[str]]:
    if not path.parent.is_dir():
        return None
    try:
        return open(path, "a", buffering=1)
    except OSError:
        return None


def _choose():
    """The first usable target: an open line-buffered file and its path, or stderr."""
    for path in _candidates():
        handle = _open(path)
        if handle is not None:
            return handle, str(path)
    return sys.stderr, "stderr"


def enable() -> Optional[str]:
    """Turn on crash telemetry once. Returns where it writes, or None when switched off."""
    global _handle, _target
    if _target is not None:
        return _target
    if os.environ.get("JONA_FAULT_LOG", "").strip().lower() == "off":
        return None

    _handle, _target = _choose()

    try:
        faulthandler.enable(file=_handle)
        if hasattr(signal, "SIGUSR1"):
            # A live dump of every thread on demand: kill -USR1 <pid>
            faulthandler.register(signal.SIGUSR1, file=_handle, all_threads=True)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[startup] Crash telemetry unavailable: {exc}", file=sys.stderr)
        _target = "unavailable"
        return _target
    print(f"[startup] Crash telemetry: {_target} (all-thread dump: kill -USR1 {os.getpid()})",
          file=sys.stderr)
    return _target


def target() -> Optional[str]:
    """Where crash telemetry writes, if it's on."""
    return _target
