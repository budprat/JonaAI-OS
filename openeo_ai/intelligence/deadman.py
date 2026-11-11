# ABOUTME: Dead-Man Tick (Brain-1 wave 1) — worker heartbeat + watch due-lag detector
# ABOUTME: that alarms as a system Signal through the existing delivery pipe, anti-spam gated.

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

SYSTEM_USER_ID = "system"
SIGNAL_TYPE = "system"
DEFAULT_GRACE_SECONDS = 900


@dataclass(frozen=True)
class Fault:
    metric: str        # worker_heartbeat | watch_due_lag
    severity: str
    explanation: str


def assess_liveness(
    last_tick_at: Optional[datetime],
    due_lag_seconds: Optional[float],
    now: datetime,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
    worker_enabled: bool = True,
) -> List[Fault]:
    """Pure liveness verdict — no side effects.

    - Worker disabled on purpose → no faults (intentional silence).
    - last_tick_at None (no tick since boot) → heartbeat is unknown, not stale;
      only due-lag can prove deadness then (watches overdue while nothing ticks).
    - A fault fires at 2x grace so one slow tick never alarms.
    """
    faults: List[Fault] = []
    if not worker_enabled:
        return faults
    if last_tick_at is not None:
        age = (now - last_tick_at).total_seconds()
        if age >= 2 * grace_seconds:
            faults.append(Fault(
                "worker_heartbeat", "critical",
                f"Worker heartbeat is stale: last tick {int(age)}s ago "
                f">= 2x grace ({grace_seconds}s).",
            ))
    if due_lag_seconds is not None and due_lag_seconds >= 2 * grace_seconds:
        faults.append(Fault(
            "watch_due_lag", "critical",
            f"Oldest due watch is {int(due_lag_seconds)}s overdue "
            f">= 2x grace ({grace_seconds}s).",
        ))
    return faults


async def run_deadman_check(
    repository,
    worker_status: Dict[str, Any],
    targets: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
    worker_enabled: bool = True,
    dispatch: Optional[Callable[..., Awaitable[List[Dict[str, Any]]]]] = None,
) -> Dict[str, Any]:
    """Assess liveness, then alarm through the normal Signal→Delivery pipe.

    Anti-spam gate: at most one OPEN system signal per fault metric; a healthy
    re-check resolves it (re-arming the gate). The detector dispatches its own
    deliveries via `dispatch` — a dead worker cannot send its own alarm, so the
    caller binds a FRESH worker's dispatch_deliveries, never the watched one.
    """
    now = now or datetime.utcnow()
    last_tick_at = worker_status.get("last_tick_at")
    if isinstance(last_tick_at, str):
        last_tick_at = datetime.fromisoformat(last_tick_at)

    due_lag = repository.oldest_due_watch_lag_seconds(now=now)
    faults = assess_liveness(last_tick_at, due_lag, now, grace_seconds, worker_enabled)
    fault_metrics = {f.metric for f in faults}

    open_system = {
        s.get("metric"): s
        for s in repository.list_signals(SYSTEM_USER_ID, status="open")
        if s.get("signal_type") == SIGNAL_TYPE
    }

    created: List[Dict[str, Any]] = []
    for fault in faults:
        if fault.metric in open_system:
            continue
        signal = repository.create_signal(SYSTEM_USER_ID, {
            "signal_type": SIGNAL_TYPE,
            "metric": fault.metric,
            "severity": fault.severity,
            "explanation": fault.explanation,
        })
        created.append(signal)
        for target in targets or []:
            channel = (target or {}).get("channel")
            destination = ((target or {}).get("target")
                           or (target or {}).get("url") or (target or {}).get("email"))
            if channel and destination:
                repository.create_delivery(SYSTEM_USER_ID, {
                    "signal_id": signal["id"],
                    "channel": str(channel),
                    "target": str(destination),
                })

    resolved: List[str] = []
    for metric, signal in open_system.items():
        if metric not in fault_metrics:
            repository.update_signal(SYSTEM_USER_ID, signal["id"], {"resolved": True})
            resolved.append(signal["id"])

    dispatched = 0
    if created and dispatch is not None:
        dispatched = len(await dispatch(user_id=SYSTEM_USER_ID))

    return {
        "checked_at": now.isoformat(),
        "faults": [{"metric": f.metric, "severity": f.severity,
                    "explanation": f.explanation} for f in faults],
        "signals_created": len(created),
        "signals_resolved": len(resolved),
        "deliveries_dispatched": dispatched,
    }


def targets_from_env() -> List[Dict[str, Any]]:
    """Alarm targets for system signals (no watch owns them): JONA_DEADMAN_WEBHOOK
    and/or JONA_DEADMAN_SLACK env URLs."""
    targets: List[Dict[str, Any]] = []
    webhook = os.getenv("JONA_DEADMAN_WEBHOOK", "").strip()
    if webhook:
        targets.append({"channel": "webhook", "target": webhook})
    slack = os.getenv("JONA_DEADMAN_SLACK", "").strip()
    if slack:
        targets.append({"channel": "slack", "target": slack})
    return targets


def grace_from_env() -> int:
    raw = os.getenv("JONA_DEADMAN_GRACE_SECONDS", "").strip()
    try:
        return int(raw) if raw else DEFAULT_GRACE_SECONDS
    except ValueError:
        return DEFAULT_GRACE_SECONDS
