# ABOUTME: Pydantic models for the batch analysis system.
# Defines workflow templates, batch jobs, batch items, schedules, and results.
# Schema is now owned by Alembic (phase6_sqlite_to_pg migration), not this module.

"""
Batch Analysis Data Models

Covers five core entities:
1. WorkflowTemplate - Reusable analysis recipes with parameterized slots
2. BatchJob - A group of analysis items submitted together
3. BatchItem - A single analysis unit within a batch (one location)
4. Schedule - Recurring execution configuration
5. BatchResult - Aggregated result record for a completed item

The SCHEMA_SQL constant below is kept as historical reference. Tables are now
created by Alembic migration `phase6_sqlite_to_pg.py`. init_batch_db() is a
no-op left in place for backward compatibility with callers.
"""

import uuid
import json
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field


# ── Enums ──

class BatchStatus(str, Enum):
    """Status of a batch job or individual item."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIALLY_COMPLETED = "partially_completed"


class ScheduleFrequency(str, Enum):
    """Supported schedule frequencies."""
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ON_SATELLITE_PASS = "on_satellite_pass"
    MANUAL = "manual"


class ScheduleStatus(str, Enum):
    """Schedule lifecycle status."""
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TriggerType(str, Enum):
    """What triggers a batch execution."""
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT = "event"


class ParameterType(str, Enum):
    """Types of template parameters."""
    SPATIAL_EXTENT = "spatial_extent"
    TEMPORAL_EXTENT = "temporal_extent"
    COLLECTION = "collection"
    BAND = "band"
    THRESHOLD = "threshold"
    INDEX = "index"
    SCALAR = "scalar"


# ── Pydantic Request/Response Models ──

class TemplateParameter(BaseModel):
    """A variable slot in a workflow template."""
    name: str = Field(..., description="Parameter name (e.g., 'spatial_extent', 'start_date')")
    type: ParameterType = Field(..., description="Parameter type for validation")
    description: str = Field(default="", description="Human-readable description")
    default_value: Optional[Any] = Field(default=None, description="Default value if not overridden")
    required: bool = Field(default=True, description="Whether this parameter must be provided")


class WorkflowTemplateCreate(BaseModel):
    """Request to create a workflow template."""
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="")
    process_graph: Dict[str, Any] = Field(..., description="OpenEO process graph")
    parameters: List[TemplateParameter] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    source_query: str = Field(default="", description="Original natural language query")
    collection_id: str = Field(default="", description="Primary collection used")
    index_type: str = Field(default="", description="Spectral index (NDVI, NDWI, etc.)")


class WorkflowTemplateResponse(BaseModel):
    """Workflow template response."""
    id: str
    name: str
    description: str
    process_graph: Dict[str, Any]
    parameters: List[TemplateParameter]
    tags: List[str]
    source_query: str
    collection_id: str
    index_type: str
    version: int
    user_id: str
    is_public: bool = False
    use_count: int
    created_at: str
    updated_at: str


class LocationInput(BaseModel):
    """A single location for batch processing."""
    name: str = Field(..., description="Location name (e.g., 'Farm Alpha')")
    spatial_extent: Dict[str, float] = Field(
        ...,
        description="Bounding box: {west, south, east, north}"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extra metadata (policy_id, area_hectares, etc.)"
    )


class BatchJobCreate(BaseModel):
    """Request to create and submit a batch job."""
    template_id: str = Field(..., description="Workflow template to execute")
    name: str = Field(default="", max_length=255)
    locations: List[LocationInput] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of locations to analyze"
    )
    parameter_overrides: Dict[str, Any] = Field(
        default_factory=dict,
        description="Override template defaults (e.g., temporal_extent)"
    )
    project_id: Optional[str] = Field(default=None, description="Assign to project")


class BatchJobResponse(BaseModel):
    """Batch job response."""
    id: str
    template_id: str
    name: str
    user_id: Optional[str] = None
    status: BatchStatus
    total_items: int
    completed_items: int
    failed_items: int
    trigger_type: TriggerType
    project_id: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    estimated_duration_seconds: Optional[int]


class BatchItemResponse(BaseModel):
    """Status of one item within a batch."""
    id: str
    batch_job_id: str
    location_name: str
    spatial_extent: Dict[str, float]
    status: BatchStatus
    openeo_job_id: Optional[str]
    result_path: Optional[str]
    result_summary: Optional[Dict[str, Any]]
    error_message: Optional[str]
    retry_count: int
    started_at: Optional[str]
    completed_at: Optional[str]


class ScheduleCreate(BaseModel):
    """Request to create a recurring schedule."""
    template_id: str
    name: str = Field(default="", max_length=255)
    frequency: ScheduleFrequency
    locations: List[LocationInput]
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict)
    project_id: Optional[str] = None
    starts_at: Optional[str] = Field(
        default=None,
        description="ISO datetime for first run (default: now)"
    )
    ends_at: Optional[str] = Field(
        default=None,
        description="ISO datetime to stop scheduling"
    )
    max_runs: Optional[int] = Field(
        default=None,
        description="Maximum number of runs (None = unlimited)"
    )


class ScheduleResponse(BaseModel):
    """Schedule response."""
    id: str
    template_id: str
    name: str
    user_id: Optional[str] = None
    frequency: ScheduleFrequency
    status: ScheduleStatus
    locations_count: int
    runs_completed: int
    max_runs: Optional[int]
    next_run_at: Optional[str]
    last_run_at: Optional[str]
    created_at: str


class BatchResultSummary(BaseModel):
    """Aggregated results from a batch run."""
    batch_job_id: str
    template_name: str
    total_locations: int
    completed: int
    failed: int
    items: List[BatchItemResponse]
    alerts: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Threshold violations or anomalies detected"
    )
    aggregated_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="Cross-location statistics (min, max, mean values)"
    )


class ResourceEstimate(BaseModel):
    """Pre-execution resource estimate."""
    total_items: int
    estimated_duration_seconds: int
    estimated_data_volume_mb: float
    estimated_api_calls: int
    warnings: List[str] = Field(default_factory=list)


class LearnedPattern(BaseModel):
    """A workflow pattern learned from successful job execution."""
    id: str
    domain: str = ""              # fire, vegetation, water, etc.
    index_type: str = ""          # ndvi, ndwi, nbr, etc.
    collection_id: str = ""       # sentinel-2-l2a, landsat-c2-l2, etc.
    temporal_strategy: str = "single_snapshot"  # single_snapshot, change_detection, time_series
    cloud_masked: bool = False
    time_reducer: str = "median"  # median, mean, max, min
    source_query: str = ""        # original job title
    summary: str = ""             # 1-line description
    use_count: int = 1
    success_count: int = 1
    created_at: str = ""
    last_used_at: str = ""


class LearnedTrajectory(BaseModel):
    """A successful tool sequence learned from real usage.

    Unlike LearnedPattern (which stores process graph structures),
    this stores TOOL SEQUENCES that succeeded. Multiple different
    tool orderings can achieve the same outcome.
    """
    id: str
    domain: str = ""                    # fire, vegetation, water, etc.
    tool_sequence: List[str] = Field(   # e.g. ["recommend", "resolve", "compose_graph", ...]
        default_factory=list
    )
    source_query: str = ""              # Original user prompt
    outcome_score: float = 0.0          # Composite outcome quality (0.0-1.0)
    use_count: int = 1
    created_at: str = ""
    last_used_at: str = ""


# ── SQLite Schema ──

SCHEMA_SQL = """
-- Workflow templates: reusable analysis recipes
CREATE TABLE IF NOT EXISTS workflow_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    process_graph TEXT NOT NULL,          -- JSON: the OpenEO process graph
    parameters TEXT DEFAULT '[]',         -- JSON: list of TemplateParameter dicts
    tags TEXT DEFAULT '[]',               -- JSON: list of tag strings
    source_query TEXT DEFAULT '',         -- original NL query that generated this
    collection_id TEXT DEFAULT '',        -- primary data collection
    index_type TEXT DEFAULT '',           -- spectral index type
    version INTEGER DEFAULT 1,
    user_id TEXT NOT NULL,
    use_count INTEGER DEFAULT 0,
    is_public INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_templates_user ON workflow_templates(user_id);
CREATE INDEX IF NOT EXISTS idx_templates_tags ON workflow_templates(tags);

-- Batch jobs: groups of analysis items
CREATE TABLE IF NOT EXISTS batch_jobs (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES workflow_templates(id),
    name TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    trigger_type TEXT NOT NULL DEFAULT 'manual',
    schedule_id TEXT,                     -- NULL for manual runs
    total_items INTEGER NOT NULL DEFAULT 0,
    completed_items INTEGER DEFAULT 0,
    failed_items INTEGER DEFAULT 0,
    parameter_overrides TEXT DEFAULT '{}', -- JSON
    project_id TEXT,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    estimated_duration_seconds INTEGER,
    FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_user ON batch_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_status ON batch_jobs(status);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_template ON batch_jobs(template_id);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_schedule ON batch_jobs(schedule_id);

-- Batch items: individual analysis units within a batch
CREATE TABLE IF NOT EXISTS batch_items (
    id TEXT PRIMARY KEY,
    batch_job_id TEXT NOT NULL REFERENCES batch_jobs(id) ON DELETE CASCADE,
    location_name TEXT NOT NULL,
    spatial_extent TEXT NOT NULL,          -- JSON: {west, south, east, north}
    location_metadata TEXT DEFAULT '{}',   -- JSON: extra fields
    status TEXT NOT NULL DEFAULT 'pending',
    openeo_job_id TEXT,                    -- ID from OpenEO backend
    result_path TEXT,                      -- path to result file
    result_summary TEXT DEFAULT '{}',      -- JSON: extracted statistics
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_batch_items_job ON batch_items(batch_job_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_status ON batch_items(status);

-- Schedules: recurring execution configurations
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES workflow_templates(id),
    name TEXT DEFAULT '',
    frequency TEXT NOT NULL,              -- daily, weekly, monthly, etc.
    status TEXT NOT NULL DEFAULT 'active',
    locations TEXT NOT NULL,              -- JSON: list of LocationInput dicts
    parameter_overrides TEXT DEFAULT '{}', -- JSON
    project_id TEXT,
    user_id TEXT NOT NULL,
    runs_completed INTEGER DEFAULT 0,
    max_runs INTEGER,                     -- NULL = unlimited
    starts_at TEXT,
    ends_at TEXT,
    next_run_at TEXT,
    last_run_at TEXT,
    last_batch_job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules(user_id);
CREATE INDEX IF NOT EXISTS idx_schedules_status ON schedules(status);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(next_run_at);

-- Alert rules: threshold-based notifications per template
CREATE TABLE IF NOT EXISTS alert_rules (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES workflow_templates(id),
    name TEXT NOT NULL,
    metric_path TEXT NOT NULL,            -- JSON path into result_summary (e.g., 'mean_ndvi')
    operator TEXT NOT NULL,               -- lt, gt, lte, gte, eq
    threshold_value REAL NOT NULL,
    user_id TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_rules_template ON alert_rules(template_id);

-- Alert history: fired alerts
CREATE TABLE IF NOT EXISTS alert_history (
    id TEXT PRIMARY KEY,
    alert_rule_id TEXT NOT NULL REFERENCES alert_rules(id),
    batch_item_id TEXT NOT NULL REFERENCES batch_items(id),
    batch_job_id TEXT NOT NULL,
    location_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    threshold_value REAL NOT NULL,
    message TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_history_job ON alert_history(batch_job_id);
CREATE INDEX IF NOT EXISTS idx_alert_history_unack ON alert_history(acknowledged);

-- Learned workflow patterns from successful job executions
CREATE TABLE IF NOT EXISTS learned_patterns (
    id TEXT PRIMARY KEY,
    pattern_hash TEXT UNIQUE NOT NULL,
    domain TEXT DEFAULT '',
    index_type TEXT DEFAULT '',
    collection_id TEXT DEFAULT '',
    temporal_strategy TEXT DEFAULT 'single_snapshot',
    cloud_masked INTEGER DEFAULT 0,
    time_reducer TEXT DEFAULT 'median',
    source_query TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    process_graph TEXT NOT NULL,
    use_count INTEGER DEFAULT 1,
    success_count INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patterns_domain ON learned_patterns(domain);
CREATE INDEX IF NOT EXISTS idx_patterns_index ON learned_patterns(index_type);
CREATE INDEX IF NOT EXISTS idx_patterns_collection ON learned_patterns(collection_id);

-- Learned tool sequences from successful trajectories
CREATE TABLE IF NOT EXISTS learned_trajectories (
    id TEXT PRIMARY KEY,
    sequence_hash TEXT UNIQUE NOT NULL,
    domain TEXT DEFAULT '',
    tool_sequence TEXT NOT NULL,        -- JSON array of tool names
    source_query TEXT DEFAULT '',
    outcome_score REAL DEFAULT 0.0,
    use_count INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lt_domain ON learned_trajectories(domain);
CREATE INDEX IF NOT EXISTS idx_lt_score ON learned_trajectories(outcome_score);
"""


def init_batch_db(db_path: str) -> None:
    """No-op (schema is now owned by Alembic migrations).

    Kept in the public API so existing callers (BatchStore.__init__,
    ScheduleStore.__init__, tests) keep working without changes.

    Args:
        db_path: Legacy SQLite path. Ignored.
    """
    # All batch tables are created by openeo_app/psql/alembic/versions/
    # phase6_sqlite_to_pg.py — running `alembic upgrade head` is now the
    # correct way to ensure tables exist.
    return None
