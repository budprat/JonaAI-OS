# ABOUTME: Sharing API for analyses, workflows, and projects (Phase 11).

"""
Sharing API — /api/v1/share/*

- POST   /api/v1/share                    — create a share (returns share_link)
- GET    /api/v1/shared/{share_link}      — retrieve a shared item
- GET    /api/v1/shared/with-me           — items shared with current user
- GET    /api/v1/shared/by-me             — items the current user has shared
- DELETE /api/v1/share/{id}               — revoke a share
- POST   /api/v1/shared/{share_link}/duplicate — clone a shared item into own workspace
"""

from __future__ import annotations

import secrets
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from openeo_ai import _pg_compat
from openeo_ai.supabase_auth import require_auth

router = APIRouter(prefix="/api/v1", tags=["sharing"])


def _row_to_share(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "item_type": row["item_type"],
        "item_id": row["item_id"],
        "owner_id": str(row["owner_id"]),
        "shared_with_id": str(row["shared_with_id"]) if row["shared_with_id"] else None,
        "permission": row["permission"],
        "share_link": row["share_link"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


_SHARE_OWNER_QUERIES = {
    "analysis": "SELECT 1 FROM saved_results WHERE save_id = ? AND user_id = ?",
    "workflow": "SELECT 1 FROM saved_process_graphs WHERE id::text = ? AND user_id = ?",
    "project": "SELECT 1 FROM projects WHERE id::text = ? AND user_id = ?",
}


def _owns_item(item_type: str, item_id: str, user_id: str) -> bool:
    conn = _pg_compat.connect()
    try:
        return conn.execute(_SHARE_OWNER_QUERIES[item_type], (item_id, user_id)).fetchone() is not None
    finally:
        conn.close()


@router.post("/share")
async def create_share(
    request: Request,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Create a new share (public link or direct to a user)."""
    user_id = payload["sub"]
    body = await request.json()

    item_type = body.get("item_type")
    item_id = body.get("item_id")
    if item_type not in ("analysis", "workflow", "project"):
        raise HTTPException(status_code=400, detail="Invalid item_type")
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id required")

    if not _owns_item(item_type, str(item_id), user_id):
        raise HTTPException(status_code=404, detail="Item not found")

    shared_with = body.get("shared_with_id")
    permission = body.get("permission", "view")
    share_link = secrets.token_urlsafe(12) if not shared_with else None

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO shared_items (item_type, item_id, owner_id, shared_with_id, permission, share_link)
            VALUES (?, ?, ?::uuid, ?::uuid, ?, ?)
            RETURNING id, item_type, item_id, owner_id, shared_with_id, permission, share_link, created_at
            """,
            (item_type, item_id, user_id, shared_with, permission, share_link),
        )
        row = cursor.fetchone()
        conn.commit()
        return _row_to_share(row)
    finally:
        conn.close()


@router.get("/shared/with-me")
async def list_shared_with_me(payload: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """List items explicitly shared with the current user."""
    user_id = payload["sub"]
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT * FROM shared_items
            WHERE shared_with_id = ?::uuid
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return {"shares": [_row_to_share(r) for r in cursor.fetchall()]}
    finally:
        conn.close()


@router.get("/shared/by-me")
async def list_shared_by_me(payload: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """List items the current user has shared (to others or publicly)."""
    user_id = payload["sub"]
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "SELECT * FROM shared_items WHERE owner_id = ?::uuid ORDER BY created_at DESC",
            (user_id,),
        )
        return {"shares": [_row_to_share(r) for r in cursor.fetchall()]}
    finally:
        conn.close()


@router.get("/shared/{share_link}")
async def get_shared_item(
    share_link: str,
    _: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Retrieve a shared item by its public link."""
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "SELECT * FROM shared_items WHERE share_link = ?",
            (share_link,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Shared item not found")
        return _row_to_share(row)
    finally:
        conn.close()


@router.delete("/share/{share_id}")
async def revoke_share(
    share_id: str,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Revoke a share. Only the owner can revoke."""
    user_id = payload["sub"]
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "DELETE FROM shared_items WHERE id = ?::uuid AND owner_id = ?::uuid",
            (share_id, user_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Share not found or not owned")
        return {"revoked": True}
    finally:
        conn.close()
