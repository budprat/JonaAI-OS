"""CRS and resolution policy tool."""
from __future__ import annotations

from typing import Any, Dict, Optional

from openeo_ai.validation.scf.tools.dataset_intelligence import DatasetIntelligenceService, _min_gsd


def _extract_native_crs(collection: dict) -> Optional[int]:
    summaries = collection.get("summaries") or {}
    for key in ("proj:epsg", "epsg"):
        if key in summaries:
            value = summaries.get(key)
            if isinstance(value, list) and value:
                return value[0]
            if isinstance(value, int):
                return value
    return None


class CRSResolutionPolicyService:
    def __init__(self, dataset_service: DatasetIntelligenceService):
        self.dataset_service = dataset_service

    async def get_native_metadata(self, collection_id: str) -> Dict[str, Any]:
        raw = await self.dataset_service.fetch_collection(collection_id)
        bands = self.dataset_service.normalize_collection(raw).get("bands", [])
        return {
            "collection_id": collection_id,
            "native_crs": _extract_native_crs(raw),
            "native_resolution": _min_gsd(bands),
        }

    def evaluate_policy(
        self,
        native_crs: Optional[int],
        native_resolution: Optional[float],
        target_crs: Optional[int],
        target_resolution: Optional[float],
        resampling_method: Optional[str],
    ) -> Dict[str, Any]:
        warnings = []
        required = []

        if target_crs and native_crs and target_crs != native_crs:
            if not resampling_method:
                required.append(
                    {
                        "code": "ResamplingMethodMissing",
                        "message": "Target CRS differs from native CRS. resampling_method must be explicit.",
                    }
                )
        if target_resolution and native_resolution:
            if target_resolution < native_resolution:
                warnings.append(
                    {
                        "code": "Upsampling",
                        "message": "Target resolution is finer than native resolution; results may be interpolated.",
                    }
                )
        return {"warnings": warnings, "required": required}
