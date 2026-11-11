# ABOUTME: FastAPI routes for persistent Earth Intelligence resources.

import asyncio
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from openeo_ai.api.auth import require_scope
from openeo_ai.supabase_auth import require_auth

from .repository import EarthIntelligenceRepository
from .schemas import (
    AssetCreate,
    AssetUpdate,
    CatalogDiscoverCreate,
    DeliveryCreate,
    DeliveryTargetCreate,
    DeliveryUpdate,
    EvidenceCreate,
    ObservationEventCreate,
    ObservationUpdate,
    SignalCreate,
    SignalUpdate,
    WatchCreate,
    WatchRunCreate,
    WatchUpdate,
)
from .catalog import CatalogDiscoveryService
from .worker import EarthIntelligenceWorker

router = APIRouter(tags=["earth-intelligence"])


def _user_id(payload: dict) -> str:
    return payload["sub"]


def _repo() -> EarthIntelligenceRepository:
    return EarthIntelligenceRepository()


@router.get("/api/assets")
async def list_assets(
    project_id: Optional[str] = None,
    include_archived: bool = False,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    user_id = _user_id(payload)
    assets = _repo().list_assets(
        user_id,
        project_id=project_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return {"assets": assets, "count": len(assets)}


@router.post("/api/assets", status_code=201)
async def create_asset(data: AssetCreate, payload: dict = Depends(require_auth)):
    user_id = _user_id(payload)
    return _repo().create_asset(user_id, data.model_dump())


@router.get("/api/assets/{asset_id}")
async def get_asset(asset_id: str, payload: dict = Depends(require_auth)):
    asset = _repo().get_asset(_user_id(payload), asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.patch("/api/assets/{asset_id}")
async def update_asset(asset_id: str, data: AssetUpdate, payload: dict = Depends(require_auth)):
    asset = _repo().update_asset(_user_id(payload), asset_id, data.model_dump(exclude_unset=True))
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.delete("/api/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, payload: dict = Depends(require_auth)):
    if not _repo().delete_asset(_user_id(payload), asset_id):
        raise HTTPException(status_code=404, detail="Asset not found")


@router.post("/api/assets/{asset_id}/catalog/discover")
async def discover_asset_catalog(
    asset_id: str,
    data: CatalogDiscoverCreate,
    payload: dict = Depends(require_auth),
):
    user_id = _user_id(payload)
    repo = _repo()
    asset = repo.get_asset(user_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    watch = None
    if data.queue_observations_for_watch_id:
        watch = repo.get_watch(user_id, data.queue_observations_for_watch_id)
        if not watch or watch.get("asset_id") != asset_id:
            raise HTTPException(status_code=404, detail="Watch not found for asset")

    collections = data.collections or (watch.get("collections") if watch else None) or ["sentinel-2-l2a"]
    temporal_policy = data.temporal_policy or (watch.get("temporal_policy") if watch else {}) or {}
    quality_policy = data.quality_policy or (watch.get("quality_policy") if watch else {}) or {}

    discovered = await CatalogDiscoveryService().discover_for_asset(
        asset,
        collections=collections,
        temporal_policy=temporal_policy,
        quality_policy=quality_policy,
        limit=data.limit,
    )
    catalog_items = repo.upsert_catalog_items(discovered)
    observations = []
    if watch:
        observations = repo.create_observations_for_catalog_items(
            user_id,
            watch["id"],
            [item["id"] for item in catalog_items],
            quality_metrics={"source": "catalog_discovery", "quality_policy": quality_policy},
        )
    return {
        "catalog_items": catalog_items,
        "observations": observations,
        "count": len(catalog_items),
        "queued_count": len(observations),
    }


@router.get("/api/catalog-items")
async def list_catalog_items(
    provider: Optional[str] = None,
    collection_id: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    # Auth gates access to the shared acquisition catalog. Items are provider
    # metadata, while observations remain user-scoped.
    _user_id(payload)
    items = _repo().list_catalog_items(
        provider=provider,
        collection_id=collection_id,
        limit=limit,
        offset=offset,
    )
    return {"catalog_items": items, "count": len(items)}


@router.get("/api/watches")
async def list_watches(
    asset_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    watches = _repo().list_watches(
        _user_id(payload),
        asset_id=asset_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"watches": watches, "count": len(watches)}


@router.post("/api/watches", status_code=201)
async def create_watch(data: WatchCreate, payload: dict = Depends(require_auth)):
    watch = _repo().create_watch(_user_id(payload), data.model_dump())
    if not watch:
        raise HTTPException(status_code=404, detail="Asset not found")
    return watch


@router.get("/api/watches/{watch_id}")
async def get_watch(watch_id: str, payload: dict = Depends(require_auth)):
    watch = _repo().get_watch(_user_id(payload), watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")
    return watch


@router.patch("/api/watches/{watch_id}")
async def update_watch(watch_id: str, data: WatchUpdate, payload: dict = Depends(require_auth)):
    watch = _repo().update_watch(_user_id(payload), watch_id, data.model_dump(exclude_unset=True))
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")
    return watch


@router.post("/api/watches/{watch_id}/run", status_code=202)
async def run_watch(watch_id: str, data: WatchRunCreate, payload: dict = Depends(require_auth)):
    """Create a durable queued observation for a manual watch run.

    This endpoint intentionally stops at durable queue state. The worker that
    claims queued observations and executes OpenEO jobs is the next layer.
    """
    observation = _repo().create_observation_for_watch(
        _user_id(payload),
        watch_id,
        data.model_dump(),
    )
    if not observation:
        raise HTTPException(status_code=404, detail="Watch not found")
    return {
        "observation": observation,
        "queue_status": "queued",
        "message": "Observation queued for the Earth Intelligence worker.",
    }


@router.post("/api/watches/due/enqueue", status_code=202)
async def enqueue_due_watches(
    limit: int = Query(default=25, ge=1, le=100),
    payload: dict = Depends(require_auth),
):
    observations = _repo().enqueue_due_watches(limit=limit, user_id=_user_id(payload))
    return {"observations": observations, "count": len(observations)}


@router.get("/api/observations")
async def list_observations(
    asset_id: Optional[str] = None,
    watch_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    observations = _repo().list_observations(
        _user_id(payload),
        asset_id=asset_id,
        watch_id=watch_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"observations": observations, "count": len(observations)}


@router.get("/api/observations/{observation_id}")
async def get_observation(observation_id: str, payload: dict = Depends(require_auth)):
    observation = _repo().get_observation(_user_id(payload), observation_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return observation


@router.patch("/api/observations/{observation_id}")
async def update_observation(
    observation_id: str,
    data: ObservationUpdate,
    payload: dict = Depends(require_auth),
):
    observation = _repo().update_observation(
        _user_id(payload),
        observation_id,
        data.model_dump(exclude_unset=True),
    )
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return observation


@router.post("/api/observations/{observation_id}/events", status_code=201)
async def create_observation_event(
    observation_id: str,
    data: ObservationEventCreate,
    payload: dict = Depends(require_auth),
):
    event = _repo().add_observation_event(
        _user_id(payload),
        observation_id,
        data.event_type,
        data.message,
        data.payload,
    )
    if not event:
        raise HTTPException(status_code=404, detail="Observation not found")
    return event


@router.get("/api/observation-events")
async def list_observation_events(
    observation_id: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    events = _repo().list_observation_events(
        _user_id(payload),
        observation_id=observation_id,
        limit=limit,
        offset=offset,
    )
    return {"events": events, "count": len(events)}


@router.get("/api/observations/{observation_id}/events")
async def list_events_for_observation(
    observation_id: str,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    user_id = _user_id(payload)
    observation = _repo().get_observation(user_id, observation_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    events = _repo().list_observation_events(
        user_id,
        observation_id=observation_id,
        limit=limit,
        offset=offset,
    )
    return {"events": events, "count": len(events)}


@router.post("/api/observations/{observation_id}/execute", status_code=202)
async def execute_observation(
    observation_id: str,
    background_tasks: BackgroundTasks,
    payload: dict = Depends(require_auth),
):
    user_id = _user_id(payload)
    observation = _repo().get_observation(user_id, observation_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    if observation.get("status") not in {"queued", "failed"}:
        raise HTTPException(
            status_code=409,
            detail=f"Observation is {observation.get('status')}; only queued or failed ones can be executed",
        )

    worker = EarthIntelligenceWorker.from_env()
    background_tasks.add_task(worker.execute_observation_by_id, user_id, observation_id)
    return {
        "observation": observation,
        "queue_status": "accepted",
        "message": "Observation execution started in a background task.",
    }


@router.post("/api/intelligence/worker/tick")
async def tick_worker_for_user(
    max_observations: int = Query(default=1, ge=0, le=5),
    payload: dict = Depends(require_auth),
):
    worker = EarthIntelligenceWorker.from_env()
    return await worker.tick_for_user(_user_id(payload), max_observations=max_observations)


@router.get("/api/intelligence/worker/status")
async def worker_status(request: Request, payload: dict = Depends(require_auth)):
    worker = getattr(request.app.state, "earth_intelligence_worker", None)
    if worker is None:
        return {
            "enabled": False,
            "message": "Background worker is not running. It is on by default — remove EARTH_INTELLIGENCE_WORKER=false or check startup logs.",
        }
    status = worker.status()
    # The background worker's last tick covers every user's records, so callers get only its
    # time and counts, never the records or the error text.
    last = status.get("last_result") or {}
    status["last_result"] = {"at": last.get("at"), "metrics": last.get("metrics"),
                             "failed": "error" in last} if last else None
    return {"enabled": True, **status}


@router.post("/api/intelligence/deadman/check")
async def deadman_check(request: Request, client=Depends(require_scope("admin"))):
    """Dead-Man Tick probe: assess worker liveness and alarm as a system Signal.

    Operators only (the `admin` scope): it alarms to the deployment's own targets and
    dispatches every user's pending deliveries. Grant it through Supabase app_metadata
    or an admin API key. Callable manually or by an external scheduler (cron/health
    probe); the detector must live outside the watched process, so a FRESH worker
    dispatches the alarm."""
    from .deadman import grace_from_env, run_deadman_check, targets_from_env

    watched = getattr(request.app.state, "earth_intelligence_worker", None)
    status = watched.status() if watched is not None else {"last_tick_at": None}
    fresh = EarthIntelligenceWorker.from_env()
    return await run_deadman_check(
        EarthIntelligenceRepository(),
        status,
        targets_from_env(),
        grace_seconds=grace_from_env(),
        worker_enabled=watched is not None,
        dispatch=fresh.dispatch_deliveries,
    )


@router.get("/api/evidence")
async def list_evidence(
    observation_id: Optional[str] = None,
    saved_result_save_id: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    evidence = _repo().list_evidence(
        _user_id(payload),
        observation_id=observation_id,
        saved_result_save_id=saved_result_save_id,
        limit=limit,
        offset=offset,
    )
    return {"evidence": evidence, "count": len(evidence)}


@router.post("/api/evidence", status_code=201)
async def create_evidence(data: EvidenceCreate, payload: dict = Depends(require_auth)):
    # Evidence grants read access to the files it names, so it can only name your own.
    from ..storage.result_access import artifact_paths, can_read_path

    for path in artifact_paths(data.artifacts):
        if not await asyncio.to_thread(can_read_path, path, _user_id(payload)):
            raise HTTPException(status_code=403, detail="Evidence can only name your own files.")
    evidence = _repo().create_evidence(_user_id(payload), data.model_dump())
    if not evidence:
        raise HTTPException(status_code=404, detail="Observation or saved result not found")
    return evidence


@router.get("/api/evidence/{evidence_id}")
async def get_evidence(evidence_id: str, payload: dict = Depends(require_auth)):
    evidence = _repo().get_evidence(_user_id(payload), evidence_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence package not found")
    return evidence


@router.get("/api/observations/{observation_id}/evidence")
async def list_observation_evidence(observation_id: str, payload: dict = Depends(require_auth)):
    evidence = _repo().list_evidence(_user_id(payload), observation_id=observation_id)
    return {"evidence": evidence, "count": len(evidence)}


@router.get("/api/baselines")
async def list_baselines(
    asset_id: Optional[str] = None,
    watch_id: Optional[str] = None,
    metric: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    baselines = _repo().list_baselines(
        _user_id(payload),
        asset_id=asset_id,
        watch_id=watch_id,
        metric=metric,
        limit=limit,
        offset=offset,
    )
    return {"baselines": baselines, "count": len(baselines)}


@router.get("/api/signals")
async def list_signals(
    asset_id: Optional[str] = None,
    watch_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    signals = _repo().list_signals(
        _user_id(payload),
        asset_id=asset_id,
        watch_id=watch_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"signals": signals, "count": len(signals)}


def _signal_write_payload(data: SignalCreate) -> dict:
    """Map the public 'coverage' field back to the DB column 'confidence' so the
    renamed request contract writes the unchanged column (no migration)."""
    d = data.model_dump()
    d["confidence"] = d.pop("coverage", None)
    return d


@router.post("/api/signals", status_code=201)
async def create_signal(data: SignalCreate, payload: dict = Depends(require_auth)):
    signal = _repo().create_signal(_user_id(payload), _signal_write_payload(data))
    if signal is None:
        raise HTTPException(status_code=404, detail="Asset, watch or observation not found")
    return signal


@router.patch("/api/signals/{signal_id}")
async def update_signal(signal_id: str, data: SignalUpdate, payload: dict = Depends(require_auth)):
    signal = _repo().update_signal(_user_id(payload), signal_id, data.model_dump(exclude_unset=True))
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


@router.get("/api/deliveries")
async def list_deliveries(
    signal_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    payload: dict = Depends(require_auth),
):
    deliveries = _repo().list_deliveries(
        _user_id(payload),
        signal_id=signal_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"deliveries": deliveries, "count": len(deliveries)}


@router.post("/api/deliveries", status_code=201)
async def create_delivery(data: DeliveryCreate, payload: dict = Depends(require_auth)):
    delivery = _repo().create_delivery(_user_id(payload), data.model_dump())
    if not delivery:
        raise HTTPException(status_code=404, detail="Signal not found")
    return delivery


@router.patch("/api/deliveries/{delivery_id}")
async def update_delivery(delivery_id: str, data: DeliveryUpdate, payload: dict = Depends(require_auth)):
    delivery = _repo().update_delivery(
        _user_id(payload),
        delivery_id,
        data.model_dump(exclude_unset=True),
    )
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery


@router.post("/api/signals/{signal_id}/deliveries", status_code=201)
async def create_signal_delivery(signal_id: str, data: DeliveryTargetCreate, payload: dict = Depends(require_auth)):
    body = data.model_dump()
    body["signal_id"] = signal_id
    delivery = _repo().create_delivery(_user_id(payload), body)
    if not delivery:
        raise HTTPException(status_code=404, detail="Signal not found")
    return delivery
