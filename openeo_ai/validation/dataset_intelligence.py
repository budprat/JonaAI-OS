"""Dataset and collection intelligence tool."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import httpx

KNOWN_EXTENSIONS = {
    "eo": ["eo"],
    "projection": ["projection", "proj"],
    "raster": ["raster"],
    "sar": ["sar"],
    "view": ["view"],
    "classification": ["classification"],
    "scientific": ["scientific"],
    "datacube": ["datacube"],
}


def _lower(text: Optional[str]) -> str:
    return (text or "").lower()


def _parse_date(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _extract_bands(collection: Dict[str, Any]) -> List[Dict[str, Any]]:
    summaries = collection.get("summaries") or {}
    bands = []
    for key in ("eo:bands", "raster:bands", "bands"):
        if key in summaries and isinstance(summaries[key], list):
            bands = summaries[key]
            break
    if not bands and "item_assets" in collection:
        # fallback: item_assets entries
        for name, asset in (collection.get("item_assets") or {}).items():
            if not isinstance(asset, dict):
                continue
            if "eo:bands" in asset:
                bands.extend(asset.get("eo:bands") or [])

    # Also scan item_assets for QA/classification bands not in eo:bands
    # (e.g., Sentinel-2 SCL is an item_asset, not in eo:bands)
    seen_names = {(b.get("name") or "").lower() for b in bands if isinstance(b, dict)}
    for name, asset in (collection.get("item_assets") or {}).items():
        if not isinstance(asset, dict):
            continue
        roles = asset.get("roles") or []
        asset_name_lower = name.lower()
        is_qa = (
            any(r in ("classification", "quality", "qa", "mask") for r in roles)
            or any(token in asset_name_lower for token in ["scl", "qa", "mask", "cloud", "classification"])
        )
        if is_qa and asset_name_lower not in seen_names:
            # Extract gsd from raster:bands if available
            raster_bands = asset.get("raster:bands") or []
            gsd = asset.get("gsd")
            data_type = None
            nodata = None
            if raster_bands and isinstance(raster_bands[0], dict):
                gsd = gsd or raster_bands[0].get("spatial_resolution")
                data_type = raster_bands[0].get("data_type")
                nodata = raster_bands[0].get("nodata")
            bands.append({
                "name": name,
                "common_name": name.lower(),
                "description": asset.get("title") or asset.get("description") or name,
                "gsd": gsd,
                "data_type": data_type,
                "nodata": nodata,
            })
            seen_names.add(asset_name_lower)

    normalized = []
    for band in bands:
        if not isinstance(band, dict):
            continue
        normalized.append(
            {
                "name": band.get("name") or band.get("id"),
                "common_name": band.get("common_name"),
                "description": band.get("description"),
                "center_wavelength": band.get("center_wavelength"),
                "full_width_half_max": band.get("full_width_half_max"),
                "gsd": band.get("gsd"),
                "scale": band.get("scale"),
                "offset": band.get("offset"),
                "nodata": band.get("nodata"),
                "data_type": band.get("data_type"),
            }
        )
    return normalized


def _infer_surface_reflectance(collection: Dict[str, Any]) -> Optional[bool]:
    text = " ".join(
        [
            _lower(collection.get("id")),
            _lower(collection.get("title")),
            _lower(collection.get("description")),
        ]
    )
    if "surface reflectance" in text or "l2a" in text or "sr" in text:
        return True
    if "toa" in text or "top of atmosphere" in text or "l1" in text:
        return False
    return None


def _parse_extensions(collection: Dict[str, Any]) -> Dict[str, bool]:
    extensions = collection.get("stac_extensions") or []
    flags = {key: False for key in KNOWN_EXTENSIONS}
    for ext in extensions:
        ext_lower = _lower(ext)
        for key, tokens in KNOWN_EXTENSIONS.items():
            if any(token in ext_lower for token in tokens):
                flags[key] = True
    return flags


def _infer_qa_bands(bands: List[Dict[str, Any]]) -> List[str]:
    qa = []
    for band in bands:
        name = _lower(band.get("name"))
        common = _lower(band.get("common_name"))
        if any(token in name for token in ["qa", "mask", "quality", "cloud", "scl", "classification"]):
            qa.append(band.get("name"))
        elif any(token in common for token in ["qa", "mask", "quality", "cloud", "scl"]):
            qa.append(band.get("name"))
    return sorted(list({b for b in qa if b}))


def _min_gsd(bands: List[Dict[str, Any]]) -> Optional[float]:
    gsds = [b.get("gsd") for b in bands if isinstance(b.get("gsd"), (int, float))]
    if gsds:
        return float(min(gsds))
    return None


class DatasetIntelligenceService:
    def __init__(self, stac_api_url: str):
        self.stac_api_url = stac_api_url.rstrip("/") + "/"

    async def fetch_collections(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(self.stac_api_url + "collections")
            resp.raise_for_status()
            data = resp.json()
            return data.get("collections") or []

    async def fetch_collection(self, collection_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(self.stac_api_url + f"collections/{collection_id}")
            resp.raise_for_status()
            return resp.json()

    def normalize_collection(self, collection: Dict[str, Any]) -> Dict[str, Any]:
        bands = _extract_bands(collection)
        qa_bands = _infer_qa_bands(bands)
        extent = collection.get("extent") or {}
        spatial = extent.get("spatial", {}) if isinstance(extent, dict) else {}
        temporal = extent.get("temporal", {}) if isinstance(extent, dict) else {}
        temporal_interval = (temporal.get("interval") or [[None, None]])
        start = temporal_interval[0][0] if temporal_interval else None
        end = temporal_interval[0][1] if temporal_interval else None
        extensions = _parse_extensions(collection)
        summaries = collection.get("summaries") or {}

        return {
            "id": collection.get("id"),
            "title": collection.get("title"),
            "description": collection.get("description"),
            "keywords": collection.get("keywords") or [],
            "extent": {
                "spatial": spatial,
                "temporal": {"start": start, "end": end},
            },
            "bands": bands,
            "qa_bands": qa_bands,
            "native_gsd": _min_gsd(bands),
            "surface_reflectance": _infer_surface_reflectance(collection),
            "stac_extensions": collection.get("stac_extensions") or [],
            "extensions": extensions,
            "platform": collection.get("platform"),
            "constellation": collection.get("constellation"),
            "instrument": collection.get("instrument"),
            "sar_polarizations": summaries.get("sar:polarizations") or summaries.get("polarizations"),
        }

    async def list_datasets(self) -> List[Dict[str, Any]]:
        collections = await self.fetch_collections()
        return [self.normalize_collection(c) for c in collections]

    async def query_datasets(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        collections = await self.fetch_collections()
        normalized = [self.normalize_collection(c) for c in collections]

        requires_bands = set(criteria.get("requires_bands") or [])
        max_gsd = criteria.get("max_gsd")
        temporal_start = _parse_date(criteria.get("temporal_start"))
        temporal_end = _parse_date(criteria.get("temporal_end"))
        surface_reflectance = criteria.get("surface_reflectance")
        requires_qa = criteria.get("requires_qa")
        collection_ids = set(criteria.get("collection_ids") or [])
        keywords = set([_lower(k) for k in criteria.get("keywords") or []])
        requires_extensions = set(criteria.get("requires_extensions") or [])

        filtered = []
        for item in normalized:
            if collection_ids and item.get("id") not in collection_ids:
                continue
            if requires_bands:
                band_names = {b.get("common_name") or b.get("name") for b in item.get("bands", [])}
                if not requires_bands.issubset({b for b in band_names if b}):
                    continue
            if requires_extensions:
                ext_flags = item.get("extensions") or {}
                if not all(ext_flags.get(ext) for ext in requires_extensions):
                    continue
            if max_gsd is not None and item.get("native_gsd"):
                if float(item.get("native_gsd")) > float(max_gsd):
                    continue
            if surface_reflectance is not None:
                if item.get("surface_reflectance") is not surface_reflectance:
                    continue
            if requires_qa is True and not item.get("qa_bands"):
                continue
            if temporal_start or temporal_end:
                extent = item.get("extent", {}).get("temporal", {})
                start = _parse_date(extent.get("start"))
                end = _parse_date(extent.get("end"))
                if temporal_start and end and end < temporal_start:
                    continue
                if temporal_end and start and start > temporal_end:
                    continue
            if keywords:
                text = " ".join([
                    _lower(item.get("id")),
                    _lower(item.get("title")),
                    _lower(item.get("description")),
                    " ".join([_lower(k) for k in item.get("keywords") or []]),
                ])
                if not any(k in text for k in keywords):
                    continue
            filtered.append(item)

        return filtered

    def fetch_collections_sync(self) -> List[Dict[str, Any]]:
        with httpx.Client(timeout=45.0) as client:
            resp = client.get(self.stac_api_url + "collections")
            resp.raise_for_status()
            data = resp.json()
            return data.get("collections") or []

    def fetch_collection_sync(self, collection_id: str) -> Dict[str, Any]:
        with httpx.Client(timeout=45.0) as client:
            resp = client.get(self.stac_api_url + f"collections/{collection_id}")
            resp.raise_for_status()
            return resp.json()
