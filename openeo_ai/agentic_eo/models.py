# ABOUTME: Typed intermediate representation for the agentic EO pipeline —
# ABOUTME: intent, plan (typed step DAG), execution results, and the decision report.

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Intent — the decomposed user query
# --------------------------------------------------------------------------- #

class AnalysisType(str, Enum):
    SNAPSHOT = "snapshot"      # latest clear value over the AOI
    CHANGE = "change"          # target window vs an explicit baseline window
    TREND = "trend"            # Sen's slope + Mann-Kendall over the series
    ANOMALY = "anomaly"        # target vs a same-season / previous-year baseline


class Variable(str, Enum):
    NDVI = "ndvi"     # vegetation vigour
    NDWI = "ndwi"     # open water (McFeeters)
    NDMI = "ndmi"     # vegetation moisture (Gao)
    NBR = "nbr"       # burn severity
    EVI = "evi"       # dense-canopy vigour


class AOISpec(BaseModel):
    raw_text: str
    geometry: Optional[Dict[str, Any]] = None   # GeoJSON geometry
    bbox: Optional[List[float]] = None           # [minx, miny, maxx, maxy] WGS84
    area_km2: Optional[float] = None
    resolved_name: Optional[str] = None
    # How the AOI was derived. geocoded (Nominatim) | user-supplied
    # (explicit bbox, no geocode) | unresolved. Rendered on the Method Card so a
    # wrong geocode is visible instead of silently trusted.
    provenance: str = "geocoded"


class TimeRange(BaseModel):
    start: date
    end: date


class BaselineSpec(BaseModel):
    mode: str = "previous_period"   # previous_period | previous_year | explicit
    time: Optional[TimeRange] = None


class EOIntent(BaseModel):
    """The decomposed, typed form of a natural-language EO question."""

    query: str
    aoi: AOISpec
    time: TimeRange
    variable: Variable = Variable.NDVI
    analysis_type: AnalysisType = AnalysisType.SNAPSHOT
    baseline: Optional[BaselineSpec] = None
    max_cloud_cover_pct: float = 20.0
    decision_context: str = ""
    assumptions: List[str] = Field(default_factory=list)
    # Per-run threshold overrides (name → value) from the decision verb. Read by
    # report._actions; empty/None resolves to the recipe registry (behavior-identical).
    # Not a step param, so it never enters plan_hash.
    threshold_overrides: Optional[Dict[str, float]] = None
    # Blocking clarification: when set, the pipeline cannot proceed.
    clarification_needed: Optional[str] = None
    # Set by the method-aptness gate when the plan is ill-posed — the
    # pipeline returns early with the plan built but nothing executed. Not a
    # step param, so it never enters plan_hash.
    method_clarification: Optional[str] = None


# --------------------------------------------------------------------------- #
# Plan — a typed step DAG compiled from the intent
# --------------------------------------------------------------------------- #

class StepKind(str, Enum):
    RESOLVE_AOI = "resolve_aoi"
    DISCOVER = "discover"       # STAC search
    LOAD = "load"              # read pixels for a window
    MASK = "mask"              # SCL cloud/shadow mask
    DERIVE = "derive"          # spectral index
    AGGREGATE = "aggregate"    # spatial/temporal reduction
    ANALYZE = "analyze"        # change / trend / anomaly stats
    REPORT = "report"


class Step(BaseModel):
    id: str
    kind: StepKind
    description: str
    params: Dict[str, Any] = Field(default_factory=dict)
    inputs: List[str] = Field(default_factory=list)


class MethodPlan(BaseModel):
    intent: EOIntent
    steps: List[Step]
    # Envelope (minimal — only what a consumer reads today; SLC). recipe_ref
    # links a plan to a pack recipe; improvised marks an ad-hoc plan. Neither
    # enters plan_hash (steps-only), so they never shift cache/trajectory keys.
    recipe_ref: Optional[str] = None
    improvised: bool = True

    @property
    def plan_hash(self) -> str:
        payload = [
            {"kind": s.kind.value, "params": s.params, "inputs": s.inputs}
            for s in self.steps
        ]
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Execution — what the executor produced, with per-step verification
# --------------------------------------------------------------------------- #

class SceneStat(BaseModel):
    """One clear scene reduced to an AOI statistic."""

    item_id: str
    acquired: date
    cloud_cover_pct: float
    valid_fraction: float          # fraction of AOI pixels usable after masking
    value: Optional[float] = None  # AOI median of the index (NaN-aware), None if rejected
    read_error: Optional[str] = None  # set when the COG read failed; the scene is dropped, not fatal


class StepResult(BaseModel):
    step_id: str
    kind: StepKind
    ok: bool
    detail: str = ""
    warnings: List[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    plan_hash: str
    variable: Variable
    analysis_type: AnalysisType
    scenes: List[SceneStat] = Field(default_factory=list)
    baseline_scenes: List[SceneStat] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)   # computed metrics (change, slope, p, etc.)
    steps: List[StepResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @property
    def pipeline_integrity(self) -> float:
        """Fraction of steps that passed their validators (GeoLLM-Engine style)."""
        if not self.steps:
            return 0.0
        return sum(1 for s in self.steps if s.ok) / len(self.steps)


# --------------------------------------------------------------------------- #
# Report — the decision-grade insight
# --------------------------------------------------------------------------- #

class DecisionReport(BaseModel):
    headline: str
    decision_context: str
    evidence: List[str]
    trend_change: List[str]
    confidence: str                 # High | Medium | Low
    confidence_reasons: List[str]
    caveats: List[str]
    recommended_actions: List[str]
    data_lineage: Dict[str, Any]

    def to_markdown(self) -> str:
        def bullets(items: List[str]) -> str:
            return "\n".join(f"- {i}" for i in items) if items else "- (none)"

        lineage = "\n".join(f"- **{k}:** {v}" for k, v in self.data_lineage.items())
        return (
            f"# {self.headline}\n\n"
            f"## Decision context\n{self.decision_context or '(not specified)'}\n\n"
            f"## Evidence\n{bullets(self.evidence)}\n\n"
            f"## Trend & change\n{bullets(self.trend_change)}\n\n"
            f"## Confidence: {self.confidence}\n{bullets(self.confidence_reasons)}\n\n"
            f"## Caveats & alternative explanations\n{bullets(self.caveats)}\n\n"
            f"## Recommended actions\n{bullets(self.recommended_actions)}\n\n"
            f"## Data lineage\n{lineage}\n"
        )
