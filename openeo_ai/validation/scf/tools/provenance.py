"""Provenance and reproducibility tool."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from openeo_ai.validation.scf.hash import hash_json
from openeo_ai.validation.scf.tools.dataset_intelligence import DatasetIntelligenceService


class ProvenanceService:
    def __init__(self, dataset_service: DatasetIntelligenceService, backend_id: Optional[str] = None):
        self.dataset_service = dataset_service
        self.backend_id = backend_id or "openeo-fastapi"

    async def build(self, scf: dict, process_graph: Optional[dict] = None) -> Dict[str, Any]:
        collection_id = (scf.get("data") or {}).get("collection")
        collection_meta = None
        if isinstance(collection_id, str):
            try:
                collection_meta = await self.dataset_service.fetch_collection(collection_id)
            except Exception:
                collection_meta = None

        methods_section = self._build_methods_section(scf, process_graph, collection_meta)

        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "backend": self.backend_id,
            "scf_hash": hash_json(scf) if scf else None,
            "pg_hash": hash_json(process_graph) if process_graph else None,
            "collection": {
                "id": collection_meta.get("id") if collection_meta else collection_id,
                "title": collection_meta.get("title") if collection_meta else None,
                "version": collection_meta.get("version") if collection_meta else None,
                "stac_extensions": collection_meta.get("stac_extensions") if collection_meta else None,
            },
            "methods_section": methods_section,
            "scf": scf,
            "process_graph": process_graph,
        }

    def _build_methods_section(self, scf: dict, process_graph: Optional[dict], collection_meta: Optional[dict]) -> str:
        data = scf.get("data", {}) or {}
        policy = scf.get("policy_profile", {}) or {}
        transformations = scf.get("transformations", []) or []

        collection_id = data.get("collection")
        bands = ", ".join(data.get("bands") or [])
        spatial = data.get("spatial_extent") or {}
        temporal = data.get("temporal_extent") or {}

        transform_desc = []
        for t in transformations:
            t_type = t.get("type")
            if t_type == "spectral_index":
                transform_desc.append(f"Computed spectral index {t.get('name')} using formula {t.get('formula')}.")
            elif t_type == "temporal_reduction":
                transform_desc.append(
                    f"Applied temporal reduction over {t.get('dimension', 'time')} using reducer {t.get('reducer')}."
                )
            elif t_type == "spatial_reduction":
                transform_desc.append(f"Applied spatial reduction using reducer {t.get('reducer')}.")
            elif t_type == "band_math":
                transform_desc.append(f"Applied band math expression {t.get('expression') or t.get('formula')}.")
            elif t_type == "sar_backscatter":
                transform_desc.append(
                    f"Extracted SAR backscatter for polarization {t.get('polarization')} with scale {t.get('scale', 'linear')}."
                )
            else:
                transform_desc.append(f"Applied transformation {t_type}.")

        lines = [
            f"Data were retrieved from collection '{collection_id}'.",
            f"Bands used: {bands}.",
            f"Spatial extent: west={spatial.get('west')}, south={spatial.get('south')}, east={spatial.get('east')}, north={spatial.get('north')}.",
            f"Temporal extent: {temporal.get('start')} to {temporal.get('end')}.",
            f"Policy profile: {policy}.",
        ]
        lines.extend(transform_desc)
        if process_graph:
            lines.append(f"openEO process graph hash: {hash_json(process_graph)}.")
        return " ".join([line for line in lines if line])
