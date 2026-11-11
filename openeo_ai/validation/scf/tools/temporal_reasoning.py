"""Temporal reasoning and alignment tool."""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from openeo_ai.validation.scf.tools.dataset_intelligence import DatasetIntelligenceService


def _parse_date(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Ensure timezone-aware for consistent comparison
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        return None


def _days_between(start: Optional[datetime.datetime], end: Optional[datetime.datetime]) -> Optional[int]:
    if not start or not end:
        return None
    return (end - start).days


class TemporalReasoningService:
    def __init__(self, dataset_service: DatasetIntelligenceService):
        self.dataset_service = dataset_service

    async def analyze(self, collection_id: str, temporal_extent: Dict[str, str], aggregation: Optional[str] = None) -> Dict[str, Any]:
        raw = await self.dataset_service.fetch_collection(collection_id)
        extent = raw.get("extent", {}) or {}
        temporal = extent.get("temporal", {}) or {}
        interval = temporal.get("interval") or [[None, None]]
        native_start = _parse_date(interval[0][0] if interval else None)
        native_end = _parse_date(interval[0][1] if interval else None)

        request_start = _parse_date(temporal_extent.get("start"))
        request_end = _parse_date(temporal_extent.get("end"))

        duration_days = _days_between(request_start, request_end)
        dataset_days = _days_between(native_start, native_end)

        warnings = []
        if request_start and native_start and request_start < native_start:
            warnings.append(
                {
                    "code": "TemporalStartsBeforeCollection",
                    "message": "Requested start is before collection temporal coverage.",
                }
            )
        if request_end and native_end and request_end > native_end:
            warnings.append(
                {
                    "code": "TemporalEndsAfterCollection",
                    "message": "Requested end is after collection temporal coverage.",
                }
            )

        suggestions = []
        if aggregation:
            agg = aggregation.lower()
            if agg in {"monthly", "month"}:
                suggestions.append({"code": "TemporalStepSuggestion", "message": "Use temporal_step='P1M'"})
            elif agg in {"seasonal", "quarter"}:
                suggestions.append({"code": "TemporalStepSuggestion", "message": "Use temporal_step='P3M'"})
            elif agg in {"yearly", "annual"}:
                suggestions.append({"code": "TemporalStepSuggestion", "message": "Use temporal_step='P1Y'"})

        return {
            "collection_temporal_extent": {
                "start": interval[0][0] if interval else None,
                "end": interval[0][1] if interval else None,
            },
            "request_duration_days": duration_days,
            "collection_duration_days": dataset_days,
            "warnings": warnings,
            "suggestions": suggestions,
        }
