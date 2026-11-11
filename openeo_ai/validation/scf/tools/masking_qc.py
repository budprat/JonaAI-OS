"""Masking and quality control tool."""
from __future__ import annotations

from typing import Any, Dict, List

from openeo_ai.validation.scf.tools.dataset_intelligence import DatasetIntelligenceService


SCL_CLASS_MAP = {
    1: "saturated/defective",
    2: "dark area pixels",
    3: "cloud shadows",
    4: "vegetation",
    5: "not-vegetated",
    6: "water",
    7: "unclassified",
    8: "cloud medium probability",
    9: "cloud high probability",
    10: "thin cirrus",
    11: "snow or ice",
}


class MaskingQCService:
    def __init__(self, dataset_service: DatasetIntelligenceService):
        self.dataset_service = dataset_service

    async def analyze(self, collection_id: str) -> Dict[str, Any]:
        raw = await self.dataset_service.fetch_collection(collection_id)
        normalized = self.dataset_service.normalize_collection(raw)
        bands = normalized.get("bands", [])
        qa_bands = normalized.get("qa_bands", [])
        band_names = {b.get("name") for b in bands if b.get("name")}

        recommended = []
        required_policy = []

        # Case-insensitive SCL check
        band_names_lower = {b.lower() for b in band_names if b}
        if "scl" in band_names_lower:
            recommended.append(
                {
                    "mask": "cloud",
                    "algorithm": "scl",
                    "classes": [8, 9, 10],
                    "description": "Sentinel-2 SCL classes for cloud masking",
                }
            )
            recommended.append(
                {
                    "mask": "cloud_shadow",
                    "algorithm": "scl",
                    "classes": [3],
                    "description": "SCL class for cloud shadows",
                }
            )
            recommended.append(
                {
                    "mask": "snow",
                    "algorithm": "scl",
                    "classes": [11],
                    "description": "SCL class for snow/ice",
                }
            )
            required_policy.append("cloud_mask_algorithm")
            required_policy.append("cloud_mask_classes")

        if not recommended and qa_bands:
            recommended.append(
                {
                    "mask": "qa",
                    "algorithm": "qa",
                    "bands": qa_bands,
                    "description": "QA bands available; define mask classes explicitly.",
                }
            )
            required_policy.append("qa_mask_classes")

        return {
            "collection_id": collection_id,
            "qa_bands": qa_bands,
            "recommended_masks": recommended,
            "required_policy_fields": sorted(list(set(required_policy))),
            "scl_class_map": SCL_CLASS_MAP,
        }
