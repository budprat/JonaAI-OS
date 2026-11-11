# ABOUTME: Public REST API v1 controls for persistent Earth Intelligence resources.

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from openeo_ai.api.auth import AuthenticatedClient, get_authenticated_client

from .repository import EarthIntelligenceRepository
from .schemas import (
    AssetCreate,
    DeliveryCreate,
    DeliveryUpdate,
    SignalUpdate,
    WatchCreate,
    WatchRunCreate,
)

router = APIRouter(prefix="/api/v1", tags=["Earth Intelligence API v1"])


def _repo() -> EarthIntelligenceRepository:
    return EarthIntelligenceRepository()


def _scope(client: AuthenticatedClient, scope: str) -> None:
    if not client.has_scope(scope):
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")


@router.get("/assets")
async def v1_list_assets(
    project_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "assets:read")
    assets = _repo().list_assets(client.user_id, project_id=project_id, limit=limit, offset=offset)
    return {"assets": assets, "count": len(assets)}


@router.post("/assets", status_code=201)
async def v1_create_asset(
    data: AssetCreate,
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "assets:write")
    return _repo().create_asset(client.user_id, data.model_dump())


@router.get("/assets/{asset_id}")
async def v1_get_asset(asset_id: str, client: AuthenticatedClient = Depends(get_authenticated_client)):
    _scope(client, "assets:read")
    asset = _repo().get_asset(client.user_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.get("/watches")
async def v1_list_watches(
    asset_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "watches:read")
    watches = _repo().list_watches(client.user_id, asset_id=asset_id, status=status, limit=limit, offset=offset)
    return {"watches": watches, "count": len(watches)}


@router.post("/watches", status_code=201)
async def v1_create_watch(
    data: WatchCreate,
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "watches:write")
    watch = _repo().create_watch(client.user_id, data.model_dump())
    if not watch:
        raise HTTPException(status_code=404, detail="Asset not found")
    return watch


@router.get("/watches/{watch_id}")
async def v1_get_watch(watch_id: str, client: AuthenticatedClient = Depends(get_authenticated_client)):
    _scope(client, "watches:read")
    watch = _repo().get_watch(client.user_id, watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")
    return watch


@router.post("/watches/{watch_id}/run", status_code=202)
async def v1_run_watch(
    watch_id: str,
    data: WatchRunCreate,
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "watches:write")
    observation = _repo().create_observation_for_watch(client.user_id, watch_id, data.model_dump())
    if not observation:
        raise HTTPException(status_code=404, detail="Watch not found")
    return {"observation": observation, "queue_status": "queued"}


@router.get("/observations")
async def v1_list_observations(
    asset_id: Optional[str] = None,
    watch_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "observations:read")
    observations = _repo().list_observations(
        client.user_id,
        asset_id=asset_id,
        watch_id=watch_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"observations": observations, "count": len(observations)}


@router.get("/observations/{observation_id}")
async def v1_get_observation(observation_id: str, client: AuthenticatedClient = Depends(get_authenticated_client)):
    _scope(client, "observations:read")
    observation = _repo().get_observation(client.user_id, observation_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return observation


@router.get("/evidence")
async def v1_list_evidence(
    observation_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "evidence:read")
    evidence = _repo().list_evidence(client.user_id, observation_id=observation_id, limit=limit, offset=offset)
    return {"evidence": evidence, "count": len(evidence)}


@router.get("/evidence/{evidence_id}")
async def v1_get_evidence(evidence_id: str, client: AuthenticatedClient = Depends(get_authenticated_client)):
    _scope(client, "evidence:read")
    evidence = _repo().get_evidence(client.user_id, evidence_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence package not found")
    return evidence


@router.get("/signals")
async def v1_list_signals(
    asset_id: Optional[str] = None,
    watch_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "signals:read")
    signals = _repo().list_signals(
        client.user_id,
        asset_id=asset_id,
        watch_id=watch_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"signals": signals, "count": len(signals)}


@router.patch("/signals/{signal_id}")
async def v1_update_signal(
    signal_id: str,
    data: SignalUpdate,
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "alerts:write")
    signal = _repo().update_signal(client.user_id, signal_id, data.model_dump(exclude_unset=True))
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


@router.get("/deliveries")
async def v1_list_deliveries(
    signal_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "signals:read")
    deliveries = _repo().list_deliveries(
        client.user_id,
        signal_id=signal_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"deliveries": deliveries, "count": len(deliveries)}


@router.post("/deliveries", status_code=201)
async def v1_create_delivery(
    data: DeliveryCreate,
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "alerts:write")
    delivery = _repo().create_delivery(client.user_id, data.model_dump())
    if not delivery:
        raise HTTPException(status_code=404, detail="Signal not found")
    return delivery


@router.patch("/deliveries/{delivery_id}")
async def v1_update_delivery(
    delivery_id: str,
    data: DeliveryUpdate,
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    _scope(client, "alerts:write")
    delivery = _repo().update_delivery(client.user_id, delivery_id, data.model_dump(exclude_unset=True))
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery
