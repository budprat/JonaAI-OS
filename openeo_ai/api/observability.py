# ABOUTME: Langfuse observability endpoints — user feedback scoring (Phase 16).

"""
Observability API — /api/v1/observability/*

- POST /api/v1/observability/feedback  — capture thumbs-up/down on a trace
- POST /api/v1/observability/score      — emit arbitrary Langfuse scores
- GET  /api/v1/observability/status     — check if tracing is active
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from openeo_ai.langfuse_setup import emit_score, get_langfuse_client
from openeo_ai.supabase_auth import require_auth

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])


@router.get("/status")
async def status(_: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """Return whether Langfuse observability is active."""
    client = get_langfuse_client()
    return {
        "enabled": client is not None,
        "host": None if client is None else getattr(client, "base_url", None),
    }


@router.post("/feedback")
async def post_feedback(
    request: Request,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Capture user thumbs-up/down feedback as a Langfuse score.

    Body:
        {
          "trace_id": "<langfuse trace id>",   # optional; defaults to current trace
          "rating": 1 | -1,                    # 1 = thumbs-up, -1 = thumbs-down
          "comment": "optional free text"
        }
    """
    body = await request.json()
    rating = body.get("rating")
    if rating not in (1, -1, 1.0, -1.0):
        raise HTTPException(status_code=400, detail="rating must be 1 (up) or -1 (down)")

    trace_id = body.get("trace_id")
    comment = body.get("comment")
    user_id = payload["sub"]

    # Include the submitting user in the comment so reviewers see who voted.
    enriched_comment = f"[user={user_id}] {comment}" if comment else f"[user={user_id}]"

    ok = emit_score(
        name="user_feedback",
        value=float(rating),
        trace_id=trace_id,
        comment=enriched_comment,
        data_type="NUMERIC",
    )
    if not ok:
        raise HTTPException(
            status_code=503,
            detail="Observability not configured or emission failed",
        )
    return {"ok": True, "rating": rating}


@router.post("/score")
async def post_score(
    request: Request,
    payload: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    """Emit an arbitrary Langfuse score on a trace.

    Body:
        {
          "name": "custom_metric",
          "value": 0.87,
          "trace_id": "...",       # optional
          "comment": "...",        # optional
          "data_type": "NUMERIC"   # NUMERIC | CATEGORICAL | BOOLEAN
        }
    """
    body = await request.json()
    name = body.get("name")
    value = body.get("value")
    if not name or value is None:
        raise HTTPException(status_code=400, detail="name and value are required")

    ok = emit_score(
        name=str(name),
        value=float(value) if body.get("data_type", "NUMERIC") == "NUMERIC" else value,
        trace_id=body.get("trace_id"),
        comment=body.get("comment"),
        data_type=body.get("data_type", "NUMERIC"),
    )
    if not ok:
        raise HTTPException(
            status_code=503,
            detail="Observability not configured or emission failed",
        )
    return {"ok": True, "name": name, "value": value}
