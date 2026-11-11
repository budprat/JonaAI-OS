# ABOUTME: Community API — challenges, submissions, leaderboards, activity feed (Phase 12).

"""
Community API — /api/v1/community/*

- GET  /api/v1/community/challenges           — list challenges with filters
- GET  /api/v1/community/challenges/{id}      — challenge detail + top submissions
- POST /api/v1/community/challenges/{id}/submit  — submit an analysis
- GET  /api/v1/community/feed                 — global public activity feed
- GET  /api/v1/community/feed/me              — user's own activity
- POST /api/v1/community/feed                 — publish an activity event
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from openeo_ai import _pg_compat
from openeo_ai.supabase_auth import require_auth

router = APIRouter(prefix="/api/v1/community", tags=["community"])


def _row_to_challenge(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "description": row["description"],
        "category": row["category"],
        "status": row["status"],
        "deadline": row["deadline"].isoformat() if row["deadline"] else None,
        "prize": row["prize"],
        "created_by": str(row["created_by"]) if row["created_by"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def _row_to_submission(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "challenge_id": str(row["challenge_id"]),
        "user_id": str(row["user_id"]),
        "analysis_id": row["analysis_id"],
        "score": row["score"],
        "status": row["status"],
        "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
    }


def _row_to_activity(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "event_type": row["event_type"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "metadata": _pg_compat.json_loads(row["metadata"], default={}),
        "is_public": row["is_public"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


@router.get("/challenges")
async def list_challenges(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    _: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """List challenges with optional filters."""
    where = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if category:
        where.append("category = ?")
        params.append(category)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            f"SELECT * FROM challenges {clause} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        return {"challenges": [_row_to_challenge(r) for r in cursor.fetchall()]}
    finally:
        conn.close()


@router.get("/challenges/{challenge_id}")
async def get_challenge(
    challenge_id: str,
    _: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Return a challenge + top 10 submissions (leaderboard)."""
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "SELECT * FROM challenges WHERE id = ?::uuid", (challenge_id,)
        )
        challenge_row = cursor.fetchone()
        if not challenge_row:
            raise HTTPException(status_code=404, detail="Challenge not found")

        cursor = conn.execute(
            """
            SELECT * FROM challenge_submissions
            WHERE challenge_id = ?::uuid AND status = 'scored'
            ORDER BY score DESC NULLS LAST
            LIMIT 10
            """,
            (challenge_id,),
        )
        leaderboard = [_row_to_submission(r) for r in cursor.fetchall()]

        return {
            "challenge": _row_to_challenge(challenge_row),
            "leaderboard": leaderboard,
        }
    finally:
        conn.close()


@router.post("/challenges/{challenge_id}/submit")
async def submit_to_challenge(
    challenge_id: str,
    request: Request,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Submit an analysis to a challenge. One submission per user per challenge."""
    user_id = payload["sub"]
    body = await request.json()
    analysis_id = body.get("analysis_id")
    if not analysis_id:
        raise HTTPException(status_code=400, detail="analysis_id required")

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO challenge_submissions (challenge_id, user_id, analysis_id, status)
            VALUES (?::uuid, ?::uuid, ?, 'pending')
            ON CONFLICT (challenge_id, user_id) DO UPDATE SET
                analysis_id = EXCLUDED.analysis_id,
                status = 'pending',
                submitted_at = now()
            RETURNING *
            """,
            (challenge_id, user_id, analysis_id),
        )
        row = cursor.fetchone()
        conn.commit()

        # Emit activity event
        conn.execute(
            """
            INSERT INTO activity_feed (user_id, event_type, entity_type, entity_id, metadata)
            VALUES (?::uuid, 'challenge_submitted', 'challenge', ?, ?::jsonb)
            """,
            (user_id, challenge_id, _json.dumps({"analysis_id": analysis_id})),
        )
        conn.commit()

        return _row_to_submission(row)
    finally:
        conn.close()


@router.get("/feed")
async def public_feed(
    limit: int = Query(50, ge=1, le=200),
    _: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Return the global public activity feed (most recent first)."""
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT * FROM activity_feed
            WHERE is_public = true
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return {"events": [_row_to_activity(r) for r in cursor.fetchall()]}
    finally:
        conn.close()


@router.get("/feed/me")
async def my_feed(
    limit: int = Query(50, ge=1, le=200),
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Return the current user's activity history."""
    user_id = payload["sub"]
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT * FROM activity_feed
            WHERE user_id = ?::uuid
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return {"events": [_row_to_activity(r) for r in cursor.fetchall()]}
    finally:
        conn.close()


@router.post("/feed")
async def publish_activity(
    request: Request,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Publish an activity event (analysis saved, workflow shared, etc.)."""
    user_id = payload["sub"]
    body = await request.json()

    event_type = body.get("event_type")
    if not event_type:
        raise HTTPException(status_code=400, detail="event_type required")

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO activity_feed (user_id, event_type, entity_type, entity_id, metadata, is_public)
            VALUES (?::uuid, ?, ?, ?, ?::jsonb, ?)
            RETURNING *
            """,
            (
                user_id,
                event_type,
                body.get("entity_type"),
                body.get("entity_id"),
                _json.dumps(body.get("metadata", {})),
                bool(body.get("is_public", True)),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _row_to_activity(row)
    finally:
        conn.close()
