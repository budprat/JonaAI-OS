# ABOUTME: STAC catalog discovery for Earth Intelligence watches and assets.

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from openeo_ai.retry import retryable


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def _bbox_from_geometry(geometry: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    if not geometry:
        return None

    coords: List[List[float]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
            coords.append([float(value[0]), float(value[1])])
            return
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(geometry.get("coordinates"))
    if not coords:
        return None
    xs = [coord[0] for coord in coords]
    ys = [coord[1] for coord in coords]
    return [min(xs), min(ys), max(xs), max(ys)]


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


class CatalogDiscoveryService:
    """Small STAC client focused on storing acquisition metadata, not pixels."""

    def __init__(self, stac_api_url: Optional[str] = None, provider: str = "earth-search") -> None:
        self.stac_api_url = (stac_api_url or os.environ.get("STAC_API_URL") or "https://earth-search.aws.element84.com/v1/").rstrip("/")
        self.provider = provider

    async def discover_for_asset(
        self,
        asset: Dict[str, Any],
        collections: Optional[List[str]] = None,
        temporal_policy: Optional[Dict[str, Any]] = None,
        quality_policy: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        bbox = asset.get("bbox") or _bbox_from_geometry(asset.get("geometry"))
        if not bbox:
            return []

        temporal_policy = temporal_policy or {}
        quality_policy = quality_policy or {}
        collections = collections or ["sentinel-2-l2a"]

        end = _coerce_datetime(temporal_policy.get("end")) or datetime.utcnow()
        start = _coerce_datetime(temporal_policy.get("start"))
        if not start:
            lookback_days = temporal_policy.get("lookback_days", temporal_policy.get("window_days", 30))
            try:
                start = end - timedelta(days=float(lookback_days))
            except (TypeError, ValueError):
                start = end - timedelta(days=30)

        query: Dict[str, Any] = {}
        max_cloud = quality_policy.get("max_cloud_cover", quality_policy.get("cloud_cover_lte"))
        if max_cloud is not None:
            query["eo:cloud_cover"] = {"lte": max_cloud}

        body: Dict[str, Any] = {
            "collections": collections,
            "bbox": bbox,
            "datetime": f"{_iso(start)}/{_iso(end)}",
            "limit": limit,
        }
        if query:
            body["query"] = query

        payload = await self._post_stac_search(body)

        features = payload.get("features") if isinstance(payload, dict) else []
        if not isinstance(features, list):
            return []
        return [self.normalize_feature(feature) for feature in features[:limit] if isinstance(feature, dict)]

    @retryable(retry_on=(httpx.TransportError,))
    async def _post_stac_search(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return await self._post_stac_search_impl(body)

    async def _post_stac_search_impl(self, body: Dict[str, Any]) -> Dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=float(os.environ.get("STAC_TIMEOUT_SECONDS", "30"))
        ) as client:
            response = await client.post(f"{self.stac_api_url}/search", json=body)
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def normalize_feature(self, feature: Dict[str, Any]) -> Dict[str, Any]:
        collection_id = str(feature.get("collection") or "unknown")
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        item_dt = _coerce_datetime(properties.get("datetime"))
        stac_assets = feature.get("assets") if isinstance(feature.get("assets"), dict) else {}
        return {
            "provider": self.provider,
            "collection_id": collection_id,
            "stac_id": str(feature.get("id") or ""),
            "datetime": item_dt,
            "geometry": feature.get("geometry") if isinstance(feature.get("geometry"), dict) else None,
            "bbox": feature.get("bbox") if isinstance(feature.get("bbox"), list) else None,
            "cloud_cover": float(properties["eo:cloud_cover"]) if isinstance(properties.get("eo:cloud_cover"), (int, float)) else None,
            "assets": stac_assets,
            "properties": properties,
        }
