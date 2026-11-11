# ABOUTME: LLM metering on the phase14 usage_events table: tags every call site (joint),
# ABOUTME: records what each query cost, and reports a user's spend today. All fail-open.
from __future__ import annotations

import json as _json
import logging
from typing import Any, Dict, Optional, Tuple

from openeo_ai import _pg_compat

logger = logging.getLogger(__name__)

_EVENT_TYPE = "llm_call"
_COST_EVENT_TYPE = "llm_cost"
_MICRO_USD = 1_000_000

_INSERT_SQL = (
    "INSERT INTO usage_events (user_id, event_type, quantity, metadata)\n"
    "        VALUES (?::uuid, ?, ?, ?::jsonb)"
)


def _build_llm_metadata(
    joint: str,
    model: str,
    plan_id: Optional[str],
    watch_id: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"joint": joint, "model": model}
    if plan_id:
        metadata["plan_id"] = plan_id
    if watch_id:
        metadata["watch_id"] = watch_id
    if session_id:
        metadata["session_id"] = session_id
    return metadata


def _build_insert(user_id: str, metadata: Dict[str, Any]) -> Tuple[str, tuple]:
    return _INSERT_SQL, (user_id, _EVENT_TYPE, 1, _json.dumps(metadata))


def record_llm_call(
    user_id: Optional[str],
    joint: str,
    model: str,
    *,
    plan_id: Optional[str] = None,
    watch_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Record one LLM call for observe-mode metering. Never enforces, never raises.

    Returns True if a row was written, False if skipped (no user) or on failure.
    """
    if not user_id:
        logger.debug("[budget] skip llm_call joint=%s: no authenticated user", joint)
        return False
    metadata = _build_llm_metadata(joint, model, plan_id, watch_id, session_id)
    sql, params = _build_insert(user_id, metadata)
    try:
        conn = _pg_compat.connect()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[budget] record_llm_call failed joint=%s: %s", joint, exc)
        return False


def record_llm_cost(user_id: Optional[str], usd: float, *, session_id: Optional[str] = None) -> bool:
    """Record what a query cost, in millionths of a dollar. Never raises."""
    if not user_id:
        return False
    metadata = {"session_id": session_id} if session_id else {}
    try:
        conn = _pg_compat.connect()
        try:
            conn.execute(_INSERT_SQL, (user_id, _COST_EVENT_TYPE, int(usd * _MICRO_USD), _json.dumps(metadata)))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[budget] record_llm_cost failed: %s", exc)
        return False


def user_spend_today(user_id: Optional[str]) -> float:
    """Dollars this user has spent since midnight UTC, or 0.0 if it can't be read."""
    if not user_id:
        return 0.0
    try:
        conn = _pg_compat.connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM usage_events "
                "WHERE user_id = ?::uuid AND event_type = ? "
                "AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
                (user_id, _COST_EVENT_TYPE),
            ).fetchone()
        finally:
            conn.close()
        return float(row[0]) / _MICRO_USD
    except Exception as exc:  # noqa: BLE001
        logger.warning("[budget] user_spend_today failed: %s", exc)
        return 0.0


def total_spend_this_month() -> float:
    """Dollars all users have spent since the 1st, UTC, or 0.0 if it can't be read."""
    try:
        conn = _pg_compat.connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM usage_events WHERE event_type = ? "
                "AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
                (_COST_EVENT_TYPE,),
            ).fetchone()
        finally:
            conn.close()
        return float(row[0]) / _MICRO_USD
    except Exception as exc:  # noqa: BLE001
        logger.warning("[budget] total_spend_this_month failed: %s", exc)
        return 0.0
