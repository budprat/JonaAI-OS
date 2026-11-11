# ABOUTME: FastAPI router for batch analysis endpoints.
# Exposes REST API for templates, batch jobs, schedules, and results.

"""
Batch Analysis API Routes

Endpoints organized by resource:
- /api/batch/templates    - Workflow template CRUD
- /api/batch/jobs         - Batch job submission and status
- /api/batch/schedules    - Schedule management
- /api/batch/results      - Results retrieval and aggregation
"""

import json
import os
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import JSONResponse

from openeo_ai.supabase_auth import require_auth

from .models import (
    WorkflowTemplateCreate,
    WorkflowTemplateResponse,
    TemplateParameter,
    BatchJobCreate,
    BatchJobResponse,
    BatchItemResponse,
    BatchResultSummary,
    LocationInput,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleStatus,
    BatchStatus,
    ResourceEstimate,
)
from .template_store import TemplateStore
from .batch_store import BatchStore
from .scheduler import ScheduleStore
from .executor import BatchExecutor

logger = logging.getLogger(__name__)

async def _require_batch_user(request: Request, payload: dict = Depends(require_auth)) -> dict:
    """Authenticate batch routes and expose the Supabase user id to legacy stores."""
    request.state.user_id = payload["sub"]
    return payload


router = APIRouter(
    prefix="/api/batch",
    tags=["batch"],
    dependencies=[Depends(_require_batch_user)],
)

# ── Lazy-initialized singletons ──
# These are created on first request using the same SQLite DB as sessions.

_template_store: Optional[TemplateStore] = None
_batch_store: Optional[BatchStore] = None
_schedule_store: Optional[ScheduleStore] = None
_executor: Optional[BatchExecutor] = None


def _get_db_path() -> str:
    return os.environ.get("OPENEO_AI_DB", "data/openeo_ai.db")


def _get_template_store() -> TemplateStore:
    global _template_store
    if _template_store is None:
        _template_store = TemplateStore(db_path=_get_db_path())
    return _template_store


def _get_batch_store() -> BatchStore:
    global _batch_store
    if _batch_store is None:
        _batch_store = BatchStore(db_path=_get_db_path())
    return _batch_store


def _get_schedule_store() -> ScheduleStore:
    global _schedule_store
    if _schedule_store is None:
        _schedule_store = ScheduleStore(db_path=_get_db_path())
    return _schedule_store


def _get_executor() -> BatchExecutor:
    global _executor
    if _executor is None:
        _executor = BatchExecutor(
            batch_store=_get_batch_store(),
            template_store=_get_template_store(),
        )
    return _executor


def _status_filter(enum, status: Optional[str]):
    """Parse a status query parameter, answering an unknown value with 422 rather than 500."""
    if not status:
        return None
    try:
        return enum(status)
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise HTTPException(status_code=422, detail=f"Unknown status '{status}'. Use one of: {allowed}")


def _owned(record, request: Request, *, public_ok: bool = False):
    """Return the record if this user may see it. Others get the same answer as missing."""
    if record is None:
        return None
    if public_ok and getattr(record, "is_public", False):
        return record
    return record if getattr(record, "user_id", None) == _get_user_id(request) else None


def _get_user_id(request: Request) -> str:
    """Extract user_id from request.

    Batch routes are protected by _require_batch_user, which sets the
    Supabase JWT `sub` on request.state for the existing store APIs.
    """
    if hasattr(request.state, "user_id"):
        return request.state.user_id
    raise HTTPException(status_code=401, detail="Authentication required")


# ══════════════════════════════════════════════════════════════
# WORKFLOW TEMPLATES
# ══════════════════════════════════════════════════════════════

@router.post("/templates", response_model=WorkflowTemplateResponse, status_code=201)
async def create_template(data: WorkflowTemplateCreate, request: Request):
    """Save a completed analysis as a reusable workflow template.

    The process_graph is stored as-is. Parameterizable fields can be marked
    with {"$parameter": "param_name"} placeholders, or auto-detected later.
    """
    user_id = _get_user_id(request)
    store = _get_template_store()
    template = store.create_template(data, user_id)
    return template


@router.get("/templates", response_model=List[WorkflowTemplateResponse])
async def list_templates(
    request: Request,
    tag: Optional[str] = None,
    collection_id: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List workflow templates visible to the current user."""
    user_id = _get_user_id(request)
    store = _get_template_store()
    templates, total = store.list_templates(
        user_id, tag=tag, collection_id=collection_id, limit=limit, offset=offset
    )
    return templates


@router.get("/templates/{template_id}", response_model=WorkflowTemplateResponse)
async def get_template(template_id: str, request: Request):
    """Get a single workflow template by ID."""
    store = _get_template_store()
    template = _owned(store.get_template(template_id), request, public_ok=True)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.patch("/templates/{template_id}", response_model=WorkflowTemplateResponse)
async def update_template(template_id: str, request: Request):
    """Update template fields (bumps version)."""
    user_id = _get_user_id(request)
    body = await request.json()
    store = _get_template_store()

    params = None
    if "parameters" in body:
        params = [TemplateParameter(**p) for p in body["parameters"]]

    result = store.update_template(
        template_id,
        user_id,
        name=body.get("name"),
        description=body.get("description"),
        process_graph=body.get("process_graph"),
        parameters=params,
        tags=body.get("tags"),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Template not found or not owned by user")
    return result


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, request: Request):
    """Delete a workflow template."""
    user_id = _get_user_id(request)
    store = _get_template_store()
    try:
        deleted = store.delete_template(template_id, user_id)
    except Exception as exc:
        if "foreign key" not in str(exc).lower():
            raise
        raise HTTPException(status_code=409, detail="This template is still used by batches or schedules.")
    if deleted:
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Template not found or not owned by user")


@router.post("/templates/{template_id}/detect-parameters", response_model=List[TemplateParameter])
async def detect_parameters(template_id: str, request: Request):
    """Auto-detect parameterizable fields in a template's process graph."""
    store = _get_template_store()
    template = _owned(store.get_template(template_id), request, public_ok=True)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    params = TemplateStore.auto_detect_parameters(template.process_graph)
    return params


# ══════════════════════════════════════════════════════════════
# BATCH JOBS
# ══════════════════════════════════════════════════════════════

@router.post("/jobs", response_model=BatchJobResponse, status_code=201)
async def create_batch_job(data: BatchJobCreate, request: Request):
    """Submit a batch job for execution.

    Creates the batch job and its items, then starts background execution.
    Returns immediately with job status.
    """
    user_id = _get_user_id(request)

    # Validate template exists
    tpl_store = _get_template_store()
    template = _owned(tpl_store.get_template(data.template_id), request, public_ok=True)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template {data.template_id} not found")

    # Validate location count
    if len(data.locations) > 500:
        raise HTTPException(
            status_code=400,
            detail="Maximum 500 locations per batch. Split into multiple batches.",
        )

    store = _get_batch_store()
    batch = store.create_batch_job(data, user_id)

    # Start background execution
    executor = _get_executor()
    executor.start_batch_async(batch.id)

    return batch


@router.get("/jobs", response_model=List[BatchJobResponse])
async def list_batch_jobs(
    request: Request,
    status: Optional[str] = None,
    template_id: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List batch jobs for the current user."""
    user_id = _get_user_id(request)
    store = _get_batch_store()

    batch_status = _status_filter(BatchStatus, status)
    jobs, total = store.list_batch_jobs(
        user_id,
        status=batch_status,
        template_id=template_id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )
    return jobs


@router.get("/jobs/{batch_id}", response_model=BatchJobResponse)
async def get_batch_job(batch_id: str, request: Request):
    """Get batch job status."""
    store = _get_batch_store()
    batch = _owned(store.get_batch_job(batch_id), request)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return batch


@router.get("/jobs/{batch_id}/items", response_model=List[BatchItemResponse])
async def get_batch_items(
    batch_id: str,
    request: Request,
    status: Optional[str] = None,
):
    """Get individual items and their statuses within a batch job."""
    store = _get_batch_store()
    item_status = _status_filter(BatchStatus, status)
    if not _owned(store.get_batch_job(batch_id), request):
        raise HTTPException(status_code=404, detail="Batch job not found")
    items = store.get_batch_items(batch_id, status=item_status)
    return items


@router.post("/jobs/{batch_id}/cancel")
async def cancel_batch_job(batch_id: str, request: Request):
    """Cancel a batch job and its unfinished items."""
    user_id = _get_user_id(request)
    store = _get_batch_store()

    if not store.cancel_batch_job(batch_id, user_id):
        raise HTTPException(status_code=404, detail="Batch job not found or not owned by user")

    # Also cancel the asyncio task if running
    executor = _get_executor()
    await executor.cancel_active_batch(batch_id)

    return {"cancelled": True, "batch_id": batch_id}


@router.post("/jobs/estimate", response_model=ResourceEstimate)
async def estimate_batch_resources(request: Request):
    """Estimate resources for a batch job before submission."""
    body = await request.json()
    location_count = len(body.get("locations", []))
    if location_count == 0:
        raise HTTPException(status_code=400, detail="No locations provided")

    store = _get_batch_store()
    return store.estimate_resources(location_count)


# ══════════════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════════════

@router.get("/jobs/{batch_id}/results", response_model=BatchResultSummary)
async def get_batch_results(batch_id: str, request: Request):
    """Get aggregated results for a batch job.

    Includes per-item results, cross-location statistics, and any
    alert threshold violations.
    """
    store = _get_batch_store()
    if not _owned(store.get_batch_job(batch_id), request):
        raise HTTPException(status_code=404, detail="Batch job not found")
    results = store.get_batch_results(batch_id)
    if not results:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return results


# ══════════════════════════════════════════════════════════════
# SCHEDULES
# ══════════════════════════════════════════════════════════════

@router.post("/schedules", response_model=ScheduleResponse, status_code=201)
async def create_schedule(data: ScheduleCreate, request: Request):
    """Create a recurring schedule for repeated analysis."""
    user_id = _get_user_id(request)

    # Validate template
    tpl_store = _get_template_store()
    if not _owned(tpl_store.get_template(data.template_id), request, public_ok=True):
        raise HTTPException(status_code=404, detail="Template not found")

    store = _get_schedule_store()
    return store.create_schedule(data, user_id)


@router.get("/schedules", response_model=List[ScheduleResponse])
async def list_schedules(
    request: Request,
    status: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
    """List schedules for the current user."""
    user_id = _get_user_id(request)
    store = _get_schedule_store()
    sched_status = _status_filter(ScheduleStatus, status)
    return store.list_schedules(user_id, status=sched_status, limit=limit)


@router.get("/schedules/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: str, request: Request):
    store = _get_schedule_store()
    sched = _owned(store.get_schedule(schedule_id), request)
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return sched


@router.post("/schedules/{schedule_id}/pause")
async def pause_schedule(schedule_id: str, request: Request):
    """Pause a recurring schedule."""
    user_id = _get_user_id(request)
    store = _get_schedule_store()
    if store.update_schedule_status(schedule_id, user_id, ScheduleStatus.PAUSED):
        return {"status": "paused"}
    raise HTTPException(status_code=404, detail="Schedule not found")


@router.post("/schedules/{schedule_id}/resume")
async def resume_schedule(schedule_id: str, request: Request):
    """Resume a paused schedule."""
    user_id = _get_user_id(request)
    store = _get_schedule_store()
    if store.update_schedule_status(schedule_id, user_id, ScheduleStatus.ACTIVE):
        return {"status": "active"}
    raise HTTPException(status_code=404, detail="Schedule not found")


@router.post("/schedules/{schedule_id}/cancel")
async def cancel_schedule(schedule_id: str, request: Request):
    """Cancel a schedule permanently."""
    user_id = _get_user_id(request)
    store = _get_schedule_store()
    if store.update_schedule_status(schedule_id, user_id, ScheduleStatus.CANCELLED):
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="Schedule not found")


@router.post("/schedules/{schedule_id}/trigger")
async def trigger_schedule_now(schedule_id: str, request: Request):
    """Manually trigger a scheduled analysis immediately."""
    store = _get_schedule_store()
    sched = _owned(store.get_schedule(schedule_id), request)
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if sched.status in (ScheduleStatus.CANCELLED, ScheduleStatus.EXPIRED):
        raise HTTPException(status_code=409, detail=f"Schedule is {sched.status.value}; it can't run")

    conn = store._conn()
    try:
        row = dict(conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone())
    finally:
        conn.close()
    from .models import TriggerType
    batch_id = start_batch_from_schedule(row, TriggerType.MANUAL)
    return {"triggered": True, "batch_job_id": batch_id}


def start_batch_from_schedule(row: dict, trigger) -> str:
    """Create and start a batch job from a schedule row; return the batch job id.

    Shared by the manual trigger route and the scheduler loop, so both build a run the
    same way. The batch belongs to the schedule's owner.
    """
    from openeo_ai import _pg_compat  # for json_loads helper that handles JSONB
    from .models import TriggerType

    locations = [LocationInput(**loc) for loc in _pg_compat.json_loads(row["locations"], default=[])]
    overrides = _pg_compat.json_loads(row["parameter_overrides"], default={})
    label = "manual trigger" if trigger == TriggerType.MANUAL else "scheduled run"
    batch_data = BatchJobCreate(
        template_id=row["template_id"],
        name=f"{row.get('name') or ''} ({label})",
        locations=locations,
        parameter_overrides=overrides,
        project_id=None,
    )
    batch = _get_batch_store().create_batch_job(batch_data, row["user_id"], trigger, row["id"])
    _get_executor().start_batch_async(batch.id)
    return batch.id


async def fire_due_schedule(row: dict) -> str:
    """SchedulerLoop callback: start the run for a due schedule, off the event loop."""
    import asyncio
    from .models import TriggerType

    return await asyncio.to_thread(start_batch_from_schedule, row, TriggerType.SCHEDULED)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, request: Request):
    user_id = _get_user_id(request)
    store = _get_schedule_store()
    if store.delete_schedule(schedule_id, user_id):
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Schedule not found")
