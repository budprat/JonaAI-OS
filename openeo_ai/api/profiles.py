# ABOUTME: User profile CRUD endpoints for the Supabase multi-user SaaS.
# Profiles are auto-created via trigger on auth.users insert (Phase 10).

"""
Profile API — /api/v1/profiles/*

- GET  /api/v1/profiles/me          — current user's profile
- PATCH /api/v1/profiles/me         — update own profile
- GET  /api/v1/profiles/{username}  — public profile lookup
- GET  /api/v1/profiles/search?q=   — search public profiles by name
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from openeo_ai import _pg_compat
from openeo_ai.supabase_auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])


def _row_to_profile(row: Any, include_private: bool = False) -> Dict[str, Any]:
    """Serialize a profiles row to a dict. Optionally include onboarding fields."""
    out = {
        "id": str(row["id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "avatar_url": row["avatar_url"],
        "bio": row["bio"],
        "organization": row["organization"],
        "expertise": row["expertise"] or [],
        "is_public": row["is_public"],
        "analysis_count": row["analysis_count"],
        "shared_count": row["shared_count"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    if include_private:
        out["onboarding_completed"] = row["onboarding_completed"]
        out["onboarding_step"] = row["onboarding_step"]
        out["onboarding_data"] = _pg_compat.json_loads(row["onboarding_data"], default={})
    return out


@router.get("/me")
async def get_my_profile(payload: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """Return the authenticated user's profile (including private fields)."""
    user_id = payload["sub"]
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "SELECT * FROM profiles WHERE id = ?::uuid",
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            # Profile trigger should have created one, but handle the race
            raise HTTPException(status_code=404, detail="Profile not found")
        return _row_to_profile(row, include_private=True)
    finally:
        conn.close()


@router.patch("/me")
async def update_my_profile(
    request: Request,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Update editable fields on the current user's profile."""
    user_id = payload["sub"]
    body = await request.json()

    # Whitelist of updatable fields
    allowed = {"display_name", "avatar_url", "bio", "organization", "expertise", "is_public"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields supplied")

    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [user_id]

    conn = _pg_compat.connect()
    try:
        conn.execute(
            f"UPDATE profiles SET {set_clauses}, updated_at = now() WHERE id = ?::uuid",
            params,
        )
        conn.commit()

        cursor = conn.execute("SELECT * FROM profiles WHERE id = ?::uuid", (user_id,))
        row = cursor.fetchone()
        return _row_to_profile(row, include_private=True)
    finally:
        conn.close()


@router.get("/search")
async def search_profiles(
    q: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    _: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Search public profiles by username or display_name prefix."""
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT * FROM profiles
            WHERE is_public = true
              AND (username ILIKE ? OR display_name ILIKE ?)
            ORDER BY analysis_count DESC
            LIMIT ?
            """,
            (f"{q}%", f"{q}%", limit),
        )
        return {
            "profiles": [_row_to_profile(r) for r in cursor.fetchall()],
            "query": q,
        }
    finally:
        conn.close()


@router.get("/{username}")
async def get_profile_by_username(
    username: str,
    _: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Return a public profile by username."""
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "SELECT * FROM profiles WHERE username = ? AND is_public = true",
            (username,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Profile not found")
        return _row_to_profile(row)
    finally:
        conn.close()


@router.post("/me/onboarding")
async def update_onboarding(
    request: Request,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Update onboarding progress for the current user."""
    user_id = payload["sub"]
    body = await request.json()
    import json as _json

    step = int(body.get("step", 0))
    completed = bool(body.get("completed", False))
    data = body.get("data", {})

    conn = _pg_compat.connect()
    try:
        conn.execute(
            """
            UPDATE profiles
               SET onboarding_step = ?,
                   onboarding_completed = ?,
                   onboarding_data = ?::jsonb,
                   updated_at = now()
             WHERE id = ?::uuid
            """,
            (step, completed, _json.dumps(data), user_id),
        )
        conn.commit()
        return {"ok": True, "step": step, "completed": completed}
    finally:
        conn.close()
