"""Band and index semantics tool."""
from __future__ import annotations

import re

from typing import Any, Dict, List, Optional

from openeo_ai.validation.dataset_intelligence import DatasetIntelligenceService

INDEX_TEMPLATES = {
    "ndvi": {
        "required_bands": ["red", "nir"],
        "formula": "(nir - red) / (nir + red)",
    },
    "evi": {
        "required_bands": ["red", "nir", "blue"],
        "formula": "2.5 * (nir - red) / (nir + 6*red - 7.5*blue + 1)",
    },
    "ndwi": {
        "required_bands": ["green", "nir"],
        "formula": "(green - nir) / (green + nir)",
    },
    "nbr": {
        "required_bands": ["nir", "swir16"],
        "formula": "(nir - swir16) / (nir + swir16)",
    },
}


_FORMULA_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _rename_whole_bands(formula: str, alias_map: Dict[str, str]) -> str:
    """Map identifier tokens through alias_map in one order-independent pass."""
    if not alias_map:
        return formula
    return _FORMULA_IDENTIFIER.sub(
        lambda m: alias_map.get(m.group(0), m.group(0)), formula
    )


class BandIndexSemanticsService:
    def __init__(self, dataset_service: DatasetIntelligenceService):
        self.dataset_service = dataset_service

    async def get_band_semantics(self, collection_id: str) -> Dict[str, Any]:
        raw = await self.dataset_service.fetch_collection(collection_id)
        normalized = self.dataset_service.normalize_collection(raw)

        alias_map = {}
        for band in normalized.get("bands", []):
            name = band.get("name")
            common = band.get("common_name")
            if common and name:
                alias_map[common] = name
            if name:
                alias_map.setdefault(name, name)

        return {
            "collection_id": normalized.get("id"),
            "bands": normalized.get("bands"),
            "alias_map": alias_map,
            "extensions": normalized.get("extensions"),
            "scale_offset": {
                b.get("name"): {"scale": b.get("scale"), "offset": b.get("offset")}
                for b in normalized.get("bands", [])
                if b.get("name")
            },
            "index_templates": self._map_index_templates(alias_map),
        }

    def get_band_semantics_sync(self, collection_id: str) -> Dict[str, Any]:
        raw = self.dataset_service.fetch_collection_sync(collection_id)
        normalized = self.dataset_service.normalize_collection(raw)

        alias_map = {}
        for band in normalized.get("bands", []):
            name = band.get("name")
            common = band.get("common_name")
            if common and name:
                alias_map[common] = name
            if name:
                alias_map.setdefault(name, name)

        return {
            "collection_id": normalized.get("id"),
            "bands": normalized.get("bands"),
            "alias_map": alias_map,
            "extensions": normalized.get("extensions"),
            "scale_offset": {
                b.get("name"): {"scale": b.get("scale"), "offset": b.get("offset")}
                for b in normalized.get("bands", [])
                if b.get("name")
            },
            "index_templates": self._map_index_templates(alias_map),
        }

    def _map_index_templates(self, alias_map: Dict[str, str]) -> Dict[str, Dict]:
        mapped = {}
        for name, template in INDEX_TEMPLATES.items():
            formula = template["formula"]
            # Whole band names only. A replace() per alias splices any name that
            # contains another as a substring, and alias_map deliberately holds
            # overlapping keys: every common_name AND every real band name, including
            # identity entries (lines 40-43). The same pattern was order-dependent and
            # wrong in lowering.band_math; see _apply_band_aliases there.
            formula = _rename_whole_bands(formula, alias_map)
            mapped[name] = {
                "required_bands": template["required_bands"],
                "formula": formula,
                "division_by_zero_policy": "epsilon",
                "epsilon": 1e-10,
            }
        return mapped

    async def validate_index(self, collection_id: str, index_name: str) -> Dict[str, Any]:
        semantics = await self.get_band_semantics(collection_id)
        template = semantics.get("index_templates", {}).get(index_name)
        if not template:
            return {
                "valid": False,
                "errors": [f"Index template '{index_name}' not supported."],
            }

        available = set(
            [
                b.get("common_name") or b.get("name")
                for b in semantics.get("bands", [])
                if b.get("name") or b.get("common_name")
            ]
        )
        missing = [b for b in template["required_bands"] if b not in available]
        if missing:
            return {
                "valid": False,
                "errors": [f"Missing required bands: {missing}"],
            }
        return {"valid": True, "errors": []}

    def validate_index_sync(self, collection_id: str, index_name: str) -> Dict[str, Any]:
        semantics = self.get_band_semantics_sync(collection_id)
        template = semantics.get("index_templates", {}).get(index_name)
        if not template:
            return {
                "valid": False,
                "errors": [f"Index template '{index_name}' not supported."],
            }

        available = set(
            [
                b.get("common_name") or b.get("name")
                for b in semantics.get("bands", [])
                if b.get("name") or b.get("common_name")
            ]
        )
        missing = [b for b in template["required_bands"] if b not in available]
        if missing:
            return {
                "valid": False,
                "errors": [f"Missing required bands: {missing}"],
            }
        return {"valid": True, "errors": []}
