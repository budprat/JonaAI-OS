# ABOUTME: Pydantic v2 request/response schemas for Jona AI REST API v1.
# Defines all models following OpenAPI 3.1 conventions with RFC 7807 error format.

"""
Pydantic schemas for the Jona AI REST API.

Covers:
- Authentication (token request/response, API key management)
- Analysis (submit, status, results)
- Templates (workflow templates with parameterized execution)
- Results (data download, reports, process graphs, metrics)
- Batch (multi-location analysis)
- Error responses (RFC 7807 Problem Details)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ──────────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    """Analysis job lifecycle states."""
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class OutputFormat(str, Enum):
    """Supported result output formats."""
    geotiff = "geotiff"
    csv = "csv"
    json = "json"
    netcdf = "netcdf"


class RateLimitTier(str, Enum):
    """API rate limit tiers."""
    free = "free"
    pro = "pro"
    enterprise = "enterprise"


# ── Shared / Common ───────────────────────────────────────────────────


class BoundingBox(BaseModel):
    """Geographic bounding box in WGS84 (EPSG:4326)."""
    west: float = Field(..., ge=-180, le=180, description="Western longitude")
    south: float = Field(..., ge=-90, le=90, description="Southern latitude")
    east: float = Field(..., ge=-180, le=180, description="Eastern longitude")
    north: float = Field(..., ge=-90, le=90, description="Northern latitude")

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.south >= self.north:
            raise ValueError("south must be less than north")
        if self.west >= self.east:
            # Allow anti-meridian crossing to be handled upstream
            pass
        return self

    model_config = {"json_schema_extra": {
        "example": {
            "west": 76.0, "south": 8.0, "east": 77.5, "north": 12.5
        }
    }}


class TemporalExtent(BaseModel):
    """Temporal range for analysis."""
    start: str = Field(..., description="Start date (ISO 8601, e.g. 2024-01-01)")
    end: str = Field(..., description="End date (ISO 8601, e.g. 2024-06-30)")

    model_config = {"json_schema_extra": {
        "example": {"start": "2024-01-01", "end": "2024-06-30"}
    }}


class PaginationParams(BaseModel):
    """Cursor-based pagination parameters."""
    limit: int = Field(20, ge=1, le=100, description="Number of items per page")
    cursor: Optional[str] = Field(None, description="Opaque cursor for next page")


# ── RFC 7807 Problem Details ──────────────────────────────────────────


class ProblemDetail(BaseModel):
    """RFC 7807 Problem Details for HTTP APIs.

    Standard error response format providing machine-readable error details.
    """
    type: str = Field(
        "about:blank",
        description="URI reference identifying the problem type"
    )
    title: str = Field(..., description="Short human-readable summary")
    status: int = Field(..., description="HTTP status code")
    detail: Optional[str] = Field(None, description="Human-readable explanation")
    instance: Optional[str] = Field(None, description="URI reference for this occurrence")
    errors: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Validation errors with field-level detail"
    )

    model_config = {"json_schema_extra": {
        "example": {
            "type": "https://api.jonaai.com/errors/invalid-bbox",
            "title": "Invalid Bounding Box",
            "status": 422,
            "detail": "The southern latitude (15.0) must be less than the northern latitude (10.0).",
            "instance": "/api/v1/analyze",
            "errors": [
                {"field": "bbox.south", "message": "Must be less than north"}
            ]
        }
    }}


# ── Authentication ────────────────────────────────────────────────────


class TokenRequest(BaseModel):
    """Request body for obtaining a JWT/session token."""
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)

    model_config = {"json_schema_extra": {
        "example": {"username": "analyst@insureco.com", "password": "..."}
    }}


class TokenResponse(BaseModel):
    """JWT token response after successful authentication."""
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", description="Token type (always 'bearer')")
    expires_in: int = Field(3600, description="Token lifetime in seconds")
    refresh_token: Optional[str] = Field(None, description="Token for refreshing access")

    model_config = {"json_schema_extra": {
        "example": {
            "access_token": "eyJhbGciOiJIUzI1NiIs...",  # gitleaks:allow (documentation example)
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "dGhpcyBpcyBhIHJlZnJlc2g..."  # gitleaks:allow (documentation example)
        }
    }}


class RefreshTokenRequest(BaseModel):
    """Request body for refreshing an access token."""
    refresh_token: str = Field(..., description="The refresh token from initial auth")


class APIKeyCreate(BaseModel):
    """Request to create a new API key."""
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable key name")
    scopes: List[str] = Field(
        default=["analyze", "results", "assets:read", "watches:read", "evidence:read", "signals:read"],
        description="Permission scopes for this key"
    )
    expires_in_days: Optional[int] = Field(
        None, ge=1, le=365,
        description="Key expiration in days, at most 365 (omitted = 90)"
    )

    model_config = {"json_schema_extra": {
        "example": {
            "name": "Underwriting Pipeline - Production",
            "scopes": ["analyze", "results", "assets:read", "watches:write", "evidence:read"],
            "expires_in_days": 90
        }
    }}


class APIKeyResponse(BaseModel):
    """Response after creating an API key. The key value is shown only once."""
    id: str = Field(..., description="Key identifier for management")
    name: str
    key: str = Field(..., description="The API key (shown only once at creation)")
    prefix: str = Field(..., description="Key prefix for identification (e.g. 'jona_sk_abc...')")
    scopes: List[str]
    created_at: datetime
    expires_at: Optional[datetime] = None

    model_config = {"json_schema_extra": {
        "example": {
            "id": "key_01H8Z...",
            "name": "Underwriting Pipeline - Production",
            "key": "jona_sk_live_a1b2c3d4e5f6...",  # gitleaks:allow (documentation example)
            "prefix": "jona_sk_live_a1b2",
            "scopes": ["analyze", "results", "templates"],
            "created_at": "2024-08-15T10:30:00Z",
            "expires_at": "2024-11-13T10:30:00Z"
        }
    }}


class APIKeyInfo(BaseModel):
    """API key metadata (without the secret key value)."""
    id: str
    name: str
    prefix: str
    scopes: List[str]
    created_at: datetime
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    is_active: bool = True


# ── Analysis ──────────────────────────────────────────────────────────


class AnalyzeRequest(BaseModel):
    """Submit a natural language Earth observation analysis query.

    The query is interpreted by the AI to generate an OpenEO process graph,
    which is then executed against STAC satellite data sources.
    """
    query: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="Natural language analysis query"
    )
    bbox: Optional[BoundingBox] = Field(
        None,
        description="Geographic area of interest. If omitted, extracted from query."
    )
    temporal_extent: Optional[TemporalExtent] = Field(
        None,
        description="Date range. If omitted, extracted from query."
    )
    collection: Optional[str] = Field(
        None,
        description="Specific STAC collection (e.g. 'sentinel-2-l2a'). Auto-selected if omitted."
    )
    output_format: OutputFormat = Field(
        OutputFormat.geotiff,
        description="Desired output format"
    )
    webhook_url: Optional[str] = Field(
        None,
        description="URL to POST results to when analysis completes (async only)"
    )
    project_id: Optional[str] = Field(
        None,
        description="Project to associate results with"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Arbitrary metadata to attach to the job (returned in results)"
    )

    model_config = {"json_schema_extra": {
        "example": {
            "query": "Calculate NDVI for Kerala, India during monsoon season 2024",
            "bbox": {"west": 76.0, "south": 8.0, "east": 77.5, "north": 12.5},
            "temporal_extent": {"start": "2024-06-01", "end": "2024-09-30"},
            "output_format": "geotiff",
            "webhook_url": "https://api.insureco.com/webhooks/jona-results",
            "project_id": "proj_monsoon_2024"
        }
    }}


class AnalyzeSyncRequest(AnalyzeRequest):
    """Synchronous analysis request with timeout.

    Same as AnalyzeRequest but blocks until completion or timeout.
    Suitable for simple queries that complete quickly.
    """
    timeout_seconds: int = Field(
        120,
        ge=10,
        le=300,
        description="Maximum wait time in seconds before returning partial results"
    )


class JobResponse(BaseModel):
    """Response after submitting an analysis job."""
    job_id: str = Field(..., description="Unique job identifier for polling")
    status: JobStatus = Field(JobStatus.queued, description="Current job status")
    created_at: datetime
    estimated_duration_seconds: Optional[int] = Field(
        None,
        description="Estimated time to completion in seconds"
    )
    links: Dict[str, str] = Field(
        default_factory=dict,
        description="HATEOAS links for related resources"
    )

    model_config = {"json_schema_extra": {
        "example": {
            "job_id": "job_01H8Z9K...",
            "status": "queued",
            "created_at": "2024-08-15T10:30:00Z",
            "estimated_duration_seconds": 45,
            "links": {
                "self": "/api/v1/analyze/job_01H8Z9K...",
                "cancel": "/api/v1/analyze/job_01H8Z9K...",
                "results": "/api/v1/results/job_01H8Z9K.../data"
            }
        }
    }}


class JobStatusResponse(BaseModel):
    """Detailed job status with progress information."""
    job_id: str
    status: JobStatus
    progress: Optional[float] = Field(
        None, ge=0, le=100,
        description="Completion percentage (0-100)"
    )
    current_step: Optional[str] = Field(
        None,
        description="Current processing step description"
    )
    steps_completed: int = Field(0, description="Number of completed processing steps")
    steps_total: Optional[int] = Field(None, description="Total expected steps")
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[ProblemDetail] = Field(
        None,
        description="Error details if status is 'failed'"
    )
    result_summary: Optional[str] = Field(
        None,
        description="Brief result summary when completed"
    )
    links: Dict[str, str] = Field(default_factory=dict)

    model_config = {"json_schema_extra": {
        "example": {
            "job_id": "job_01H8Z9K...",
            "status": "running",
            "progress": 65.0,
            "current_step": "Computing NDVI from Sentinel-2 bands",
            "steps_completed": 3,
            "steps_total": 5,
            "created_at": "2024-08-15T10:30:00Z",
            "started_at": "2024-08-15T10:30:02Z",
            "links": {
                "self": "/api/v1/analyze/job_01H8Z9K...",
                "cancel": "/api/v1/analyze/job_01H8Z9K...",
                "events": "/api/v1/analyze/job_01H8Z9K.../events"
            }
        }
    }}


class AnalysisResult(BaseModel):
    """Complete analysis result returned after job completion."""
    job_id: str
    status: JobStatus = JobStatus.completed
    query: str = Field(..., description="Original natural language query")
    process_graph: Optional[Dict[str, Any]] = Field(
        None, description="Generated OpenEO process graph"
    )
    result_summary: Optional[str] = Field(
        None, description="AI-generated summary of the results"
    )
    visualizations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Map layers, charts, and tables generated"
    )
    quality_metrics: Optional[Dict[str, Any]] = Field(
        None, description="Data quality and confidence metrics"
    )
    data_urls: Dict[str, str] = Field(
        default_factory=dict,
        description="Download URLs for result data by format"
    )
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    execution_time_ms: Optional[int] = None
    links: Dict[str, str] = Field(default_factory=dict)


# ── Templates ─────────────────────────────────────────────────────────


class TemplateParameter(BaseModel):
    """Parameter definition for a workflow template."""
    name: str
    type: str = Field(..., description="Parameter type: string, number, bbox, date, enum")
    description: str
    required: bool = True
    default: Optional[Any] = None
    enum_values: Optional[List[str]] = Field(None, description="Valid values for enum type")
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class TemplateInfo(BaseModel):
    """Workflow template metadata."""
    id: str
    name: str
    description: str
    category: str = Field(..., description="e.g. 'vegetation', 'water', 'urban', 'insurance'")
    parameters: List[TemplateParameter]
    example_query: str = Field(..., description="Example natural language query for this template")
    estimated_duration_seconds: int = Field(
        30, description="Typical execution time"
    )
    tags: List[str] = Field(default_factory=list)

    model_config = {"json_schema_extra": {
        "example": {
            "id": "tmpl_ndvi_change",
            "name": "NDVI Change Detection",
            "description": "Compare vegetation health between two time periods to detect changes.",
            "category": "vegetation",
            "parameters": [
                {"name": "bbox", "type": "bbox", "description": "Area of interest", "required": True},
                {"name": "period_before", "type": "date", "description": "Start of baseline period", "required": True},
                {"name": "period_after", "type": "date", "description": "Start of comparison period", "required": True},
                {"name": "threshold", "type": "number", "description": "Change detection threshold", "required": False, "default": 0.1}
            ],
            "example_query": "Show NDVI change between Jan 2024 and Jun 2024 for the selected area",
            "estimated_duration_seconds": 45,
            "tags": ["ndvi", "change-detection", "vegetation"]
        }
    }}


class TemplateExecuteRequest(BaseModel):
    """Execute a workflow template with specific parameters."""
    parameters: Dict[str, Any] = Field(
        ...,
        description="Template parameter values keyed by parameter name"
    )
    output_format: OutputFormat = OutputFormat.geotiff
    webhook_url: Optional[str] = None
    project_id: Optional[str] = None

    model_config = {"json_schema_extra": {
        "example": {
            "parameters": {
                "bbox": {"west": 76.0, "south": 8.0, "east": 77.5, "north": 12.5},
                "period_before": "2024-01-01",
                "period_after": "2024-06-01",
                "threshold": 0.15
            },
            "output_format": "geotiff",
            "project_id": "proj_monsoon_2024"
        }
    }}


# ── Results ───────────────────────────────────────────────────────────


class QualityMetrics(BaseModel):
    """Data quality and confidence metrics for analysis results."""
    overall_score: float = Field(..., ge=0, le=1, description="Overall quality score (0-1)")
    grade: str = Field(..., description="Letter grade (A, B, C, D, F)")
    cloud_coverage: float = Field(..., ge=0, le=1, description="Cloud cover fraction")
    temporal_coverage: float = Field(..., ge=0, le=1, description="Temporal data availability")
    spatial_coverage: float = Field(..., ge=0, le=1, description="Spatial data completeness")
    valid_pixel_percentage: float = Field(..., ge=0, le=100)
    recommendations: List[str] = Field(
        default_factory=list,
        description="Suggestions to improve result quality"
    )


class ProcessGraphResponse(BaseModel):
    """OpenEO process graph generated for an analysis."""
    job_id: str
    process_graph: Dict[str, Any]
    collection: Optional[str] = Field(None, description="Primary data collection used")
    spatial_extent: Optional[BoundingBox] = None
    temporal_extent: Optional[TemporalExtent] = None


# ── Batch ─────────────────────────────────────────────────────────────


class BatchLocation(BaseModel):
    """A single location within a batch analysis."""
    id: str = Field(..., description="User-defined location identifier")
    name: Optional[str] = Field(None, description="Human-readable location name")
    bbox: BoundingBox


class BatchRequest(BaseModel):
    """Submit a batch analysis across multiple locations.

    The same query/template is executed for each location independently.
    """
    query: str = Field(
        ..., min_length=5, max_length=2000,
        description="Natural language query applied to each location"
    )
    locations: List[BatchLocation] = Field(
        ..., min_length=1, max_length=50,
        description="Locations to analyze (max 50 per batch)"
    )
    temporal_extent: Optional[TemporalExtent] = None
    template_id: Optional[str] = Field(
        None,
        description="Use a template instead of free-form query"
    )
    template_parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Template parameters (bbox per-location is auto-injected)"
    )
    output_format: OutputFormat = OutputFormat.geotiff
    webhook_url: Optional[str] = None
    project_id: Optional[str] = None

    @field_validator("locations")
    @classmethod
    def validate_location_ids_unique(cls, v):
        ids = [loc.id for loc in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Location IDs must be unique within a batch")
        return v

    model_config = {"json_schema_extra": {
        "example": {
            "query": "Calculate NDVI for monsoon 2024",
            "locations": [
                {"id": "loc_kerala", "name": "Kerala", "bbox": {"west": 76.0, "south": 8.0, "east": 77.5, "north": 12.5}},
                {"id": "loc_karnataka", "name": "Karnataka", "bbox": {"west": 74.0, "south": 11.5, "east": 78.5, "north": 18.5}},
            ],
            "temporal_extent": {"start": "2024-06-01", "end": "2024-09-30"},
            "output_format": "geotiff"
        }
    }}


class BatchState(str, Enum):
    """A batch's or one location's state. `unknown` means the location's job state was
    lost: location jobs live in memory and don't survive a restart, while the batch does."""
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    unknown = "unknown"


class BatchLocationStatus(BaseModel):
    """Status of a single location within a batch."""
    location_id: str
    name: Optional[str] = None
    job_id: str
    status: BatchState
    progress: Optional[float] = None
    error: Optional[str] = None


class BatchStatusResponse(BaseModel):
    """Aggregated batch status."""
    batch_id: str
    status: BatchState
    total_locations: int
    completed: int
    failed: int
    running: int
    queued: int
    cancelled: int = 0
    unknown: int = 0
    locations: List[BatchLocationStatus]
    created_at: datetime
    completed_at: Optional[datetime] = None
    links: Dict[str, str] = Field(default_factory=dict)


# ── Pagination ────────────────────────────────────────────────────────


class PaginatedResponse(BaseModel):
    """Wrapper for paginated list responses."""
    items: List[Any]
    total: int
    limit: int
    cursor: Optional[str] = Field(None, description="Cursor for next page (None if last page)")
    links: Dict[str, str] = Field(default_factory=dict)


# ── Webhook ───────────────────────────────────────────────────────────


class WebhookPayload(BaseModel):
    """Payload sent to webhook URLs when jobs complete.

    This is the body POST-ed to the user's webhook_url.
    """
    event: str = Field(..., description="Event type: 'job.completed', 'job.failed', 'batch.completed'")
    job_id: str
    batch_id: Optional[str] = None
    status: JobStatus
    result_summary: Optional[str] = None
    data_urls: Dict[str, str] = Field(default_factory=dict)
    quality_metrics: Optional[QualityMetrics] = None
    error: Optional[ProblemDetail] = None
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None


# ── Rate Limiting ─────────────────────────────────────────────────────


class RateLimitInfo(BaseModel):
    """Rate limit status returned in response headers and introspection."""
    tier: RateLimitTier
    limit: int = Field(..., description="Maximum requests per window")
    remaining: int = Field(..., description="Requests remaining in current window")
    reset_at: datetime = Field(..., description="When the rate limit window resets")
    concurrent_limit: int = Field(..., description="Maximum concurrent analysis jobs")
    concurrent_active: int = Field(..., description="Currently running analysis jobs")
