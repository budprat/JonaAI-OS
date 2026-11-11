# ABOUTME: Usage metering API — quota checks, event logging, tier enforcement (Phase 14).

"""
Usage API — /api/v1/usage/*

- GET  /api/v1/usage/me            — current user's usage this month vs limits
- POST /api/v1/usage/events        — log a usage event (internal use)
- GET  /api/v1/usage/history       — event history for the current user
"""

from __future__ import annotations

import os
import json as _json
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from openeo_ai import _pg_compat
from openeo_ai.supabase_auth import require_auth

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])

# Tier limits (matches api/auth.py RATE_LIMIT_TIERS but expanded with storage + monthly)
TIER_LIMITS = {
    "free": {
        "analyses_per_month": 20,
        "storage_mb": 500,
        "api_calls_per_day": 100,
        "concurrent_analyses": 2,
    },
    "pro": {
        "analyses_per_month": 500,
        "storage_mb": 10_000,
        "api_calls_per_day": 10_000,
        "concurrent_analyses": 10,
    },
    "enterprise": {
        "analyses_per_month": -1,   # unlimited
        "storage_mb": -1,
        "api_calls_per_day": -1,
        "concurrent_analyses": 50,
    },
}


def _tier_for_user(payload: Dict[str, Any]) -> str:
    """Extract the user's tier from the JWT app_metadata claim (default free)."""
    return payload.get("app_metadata", {}).get("tier", "free")


@router.get("/spend")
async def get_my_spend(payload: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """What the current user's AI analysis cost today, against this service's limits."""
    from openeo_ai import budget

    return {
        "today_usd": round(budget.user_spend_today(payload["sub"]), 4),
        "daily_limit_usd": float(os.environ.get("JONA_USER_DAILY_USD") or 50),
        "query_limit_usd": float(os.environ.get("JONA_MAX_QUERY_USD") or 10),
    }


@router.get("/me")
async def get_my_usage(payload: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """Return the current user's usage for the current month and their tier limits."""
    user_id = payload["sub"]
    tier = _tier_for_user(payload)
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT event_type, COALESCE(SUM(quantity), 0) AS total
            FROM usage_events
            WHERE user_id = ?::uuid
              AND created_at >= date_trunc('month', now())
            GROUP BY event_type
            """,
            (user_id,),
        )
        usage_by_type = {r["event_type"]: int(r["total"]) for r in cursor.fetchall()}

        return {
            "tier": tier,
            "month": datetime.now(timezone.utc).strftime("%Y-%m"),
            "usage": usage_by_type,
            "limits": limits,
            "remaining": {
                "analyses": max(0, limits["analyses_per_month"] - usage_by_type.get("analysis", 0))
                if limits["analyses_per_month"] > 0 else -1,
                "storage_mb": max(0, limits["storage_mb"] - usage_by_type.get("storage_bytes", 0) // (1024 * 1024))
                if limits["storage_mb"] > 0 else -1,
            },
        }
    finally:
        conn.close()


@router.post("/events")
async def log_event(
    request: Request,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Log a usage event for the current user."""
    user_id = payload["sub"]
    body = await request.json()

    event_type = body.get("event_type")
    if event_type not in ("analysis", "api_call", "storage_bytes"):
        raise HTTPException(status_code=400, detail="Invalid event_type")

    quantity = int(body.get("quantity", 1))
    metadata = body.get("metadata", {})

    conn = _pg_compat.connect()
    try:
        conn.execute(
            """
            INSERT INTO usage_events (user_id, event_type, quantity, metadata)
            VALUES (?::uuid, ?, ?, ?::jsonb)
            """,
            (user_id, event_type, quantity, _json.dumps(metadata)),
        )
        conn.commit()
        return {"logged": True, "event_type": event_type, "quantity": quantity}
    finally:
        conn.close()


@router.get("/history")
async def event_history(
    limit: int = Query(100, ge=1, le=500),
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Return the current user's raw usage event history."""
    user_id = payload["sub"]
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT id, event_type, quantity, metadata, created_at
            FROM usage_events
            WHERE user_id = ?::uuid
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        events = []
        for r in cursor.fetchall():
            events.append({
                "id": str(r["id"]),
                "event_type": r["event_type"],
                "quantity": int(r["quantity"]),
                "metadata": _pg_compat.json_loads(r["metadata"], default={}),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })
        return {"events": events}
    finally:
        conn.close()
