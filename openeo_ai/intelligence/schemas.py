# ABOUTME: Pydantic schemas for persistent Earth Intelligence resources.

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from openeo_ai.lifecycle import DELIVERY, OBSERVATION, SIGNAL, USER_SETTABLE, WATCH, Lifecycle


JsonDict = Dict[str, Any]


def _settable(lifecycle: Lifecycle):
    """A validator that accepts only the statuses an API caller may request for `lifecycle`."""
    allowed = USER_SETTABLE[lifecycle.name]

    def check(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = "cancelled" if value == "canceled" else value
        if value not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return value

    return field_validator("status")(check)


def _plain_name(value: Optional[str]) -> Optional[str]:
    """Asset names are shown beyond this app's own UI, so they can't carry markup."""
    if value is not None and re.search(r"[<>]", value):
        raise ValueError("Name can't contain < or >")
    return value


class AssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    asset_type: str = "area"
    project_id: Optional[str] = None
    geometry: Optional[JsonDict] = None
    bbox: Optional[List[float]] = None
    properties: JsonDict = Field(default_factory=dict)
    external_refs: JsonDict = Field(default_factory=dict)

    _check_name = field_validator("name")(_plain_name)


class AssetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    asset_type: Optional[str] = None
    project_id: Optional[str] = None
    geometry: Optional[JsonDict] = None
    bbox: Optional[List[float]] = None
    properties: Optional[JsonDict] = None
    external_refs: Optional[JsonDict] = None
    archived: Optional[bool] = None

    _check_name = field_validator("name")(_plain_name)


class WatchCreate(BaseModel):
    asset_id: str
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    project_id: Optional[str] = None
    template_id: Optional[str] = None
    process_graph: JsonDict = Field(default_factory=dict)
    scf: Optional[JsonDict] = None
    collections: List[str] = Field(default_factory=list)
    trigger_type: str = "manual"
    cadence: Optional[str] = None
    temporal_policy: JsonDict = Field(default_factory=dict)
    quality_policy: JsonDict = Field(default_factory=dict)
    budget_policy: JsonDict = Field(default_factory=dict)
    next_run_at: Optional[datetime] = None


class WatchUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[str] = None
    template_id: Optional[str] = None
    process_graph: Optional[JsonDict] = None
    scf: Optional[JsonDict] = None
    collections: Optional[List[str]] = None
    trigger_type: Optional[str] = None
    cadence: Optional[str] = None
    temporal_policy: Optional[JsonDict] = None
    quality_policy: Optional[JsonDict] = None
    budget_policy: Optional[JsonDict] = None
    next_run_at: Optional[datetime] = None

    _status = _settable(WATCH)


class WatchRunCreate(BaseModel):
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    source_catalog_item_ids: List[str] = Field(default_factory=list)
    quality_metrics: JsonDict = Field(default_factory=dict)
    cost_estimate: JsonDict = Field(default_factory=dict)


class CatalogDiscoverCreate(BaseModel):
    collections: List[str] = Field(default_factory=list)
    temporal_policy: JsonDict = Field(default_factory=dict)
    quality_policy: JsonDict = Field(default_factory=dict)
    limit: int = Field(default=10, ge=1, le=100)
    queue_observations_for_watch_id: Optional[str] = None


class ObservationUpdate(BaseModel):
    status: Optional[str] = None
    openeo_job_id: Optional[str] = None
    api_job_id: Optional[str] = None
    process_graph_hash: Optional[str] = None
    scf_hash: Optional[str] = None
    quality_metrics: Optional[JsonDict] = None
    cost_estimate: Optional[JsonDict] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    _status = _settable(OBSERVATION)


class ObservationEventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=80)
    message: Optional[str] = None
    payload: JsonDict = Field(default_factory=dict)


class EvidenceCreate(BaseModel):
    observation_id: Optional[str] = None
    saved_result_save_id: Optional[str] = None
    stac_item: JsonDict = Field(default_factory=dict)
    source_items: List[JsonDict] = Field(default_factory=list)
    artifacts: JsonDict = Field(default_factory=dict)
    statistics: JsonDict = Field(default_factory=dict)
    provenance: JsonDict = Field(default_factory=dict)
    process_graph: JsonDict = Field(default_factory=dict)
    scf: Optional[JsonDict] = None
    trace_id: Optional[str] = None


class SignalCreate(BaseModel):
    asset_id: Optional[str] = None
    watch_id: Optional[str] = None
    observation_id: Optional[str] = None
    signal_type: str = "metric"
    metric: Optional[str] = None
    value: Optional[float] = None
    baseline_value: Optional[float] = None
    delta: Optional[float] = None
    severity: str = "info"
    coverage: Optional[float] = None   # Public field; the route maps it to the DB column 'confidence'
    geometry: Optional[JsonDict] = None
    explanation: Optional[str] = None


class SignalUpdate(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    explanation: Optional[str] = None
    acknowledged: Optional[bool] = None
    resolved: Optional[bool] = None

    _status = _settable(SIGNAL)


class DeliveryCreate(BaseModel):
    signal_id: str
    channel: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1)


class DeliveryTargetCreate(BaseModel):
    channel: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1)


class DeliveryUpdate(BaseModel):
    # Attempts, errors and the sent time are the dispatcher's to record.
    status: Optional[str] = None

    _status = _settable(DELIVERY)
