# ABOUTME: SQLAlchemy models for AI sessions, process graphs, tags, and history.
# Defines database schema with relationships and indexes for efficient queries.

"""
SQLAlchemy models for OpenEO AI storage.

Extends the existing PostgreSQL schema with tables for:
- AI sessions and conversation history
- Saved process graphs library
- Tags for organization
- Execution history for analytics
"""

import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    Column, String, Text, DateTime, Boolean, Integer, BigInteger,
    ForeignKey, JSON, Table, Index, Float, UniqueConstraint, CheckConstraint
)
from sqlalchemy.dialects.postgresql import UUID

from openeo_ai.lifecycle import DELIVERY, OBSERVATION, RESULT_COPY, SIGNAL, WATCH, Lifecycle
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.ext.declarative import declarative_base


def _status_check(lifecycle: Lifecycle, name: str) -> CheckConstraint:
    """Database-level guard that status holds one of the lifecycle's states.

    Migration phase19 creates the same constraint in Postgres with the values written out.
    """
    values = ", ".join(f"'{state}'" for state in sorted(lifecycle.states))
    return CheckConstraint(f"status IN ({values})", name=name)

Base = declarative_base()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _utcnow() -> datetime:
    return datetime.utcnow()


# Association table for many-to-many relationship between process graphs and tags
process_graph_tags = Table(
    'process_graph_tags',
    Base.metadata,
    Column('process_graph_id', UUID(as_uuid=True), ForeignKey('saved_process_graphs.id'), primary_key=True),
    Column('tag_id', UUID(as_uuid=True), ForeignKey('tags.id'), primary_key=True)
)


class AISession(Base):
    """
    AI conversation session.

    Stores conversation context and history for each user session.
    """
    __tablename__ = 'ai_sessions'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    context: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    messages: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    execution_history: Mapped[List["ExecutionHistory"]] = relationship(
        "ExecutionHistory",
        back_populates="session",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('ix_ai_sessions_user_updated', 'user_id', 'updated_at'),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "title": self.title,
            "context": self.context,
            "messages": self.messages,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_active": self.is_active
        }


class SavedProcessGraph(Base):
    """
    Saved process graph for reuse.

    Allows users to save, share, and reuse process graphs.
    """
    __tablename__ = 'saved_process_graphs'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    process_graph: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relationships
    tags: Mapped[List["Tag"]] = relationship(
        "Tag",
        secondary=process_graph_tags,
        back_populates="process_graphs"
    )

    __table_args__ = (
        Index('ix_saved_process_graphs_user_name', 'user_id', 'name'),
        Index('ix_saved_process_graphs_public', 'is_public'),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description,
            "process_graph": self.process_graph,
            "is_public": self.is_public,
            "use_count": self.use_count,
            "tags": [tag.name for tag in self.tags],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


class Tag(Base):
    """
    Tag for organizing process graphs.
    """
    __tablename__ = 'tags'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    # Relationships
    process_graphs: Mapped[List["SavedProcessGraph"]] = relationship(
        "SavedProcessGraph",
        secondary=process_graph_tags,
        back_populates="tags"
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat()
        }


class Project(Base):
    __tablename__ = 'projects'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    saved_results: Mapped[List["SavedResult"]] = relationship("SavedResult", back_populates="project", cascade="all, delete-orphan")

    def to_dict(self, include_files: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "analysis_count": len(self.saved_results) if self.saved_results else 0,
        }
        if include_files and self.saved_results:
            d["files"] = [r.to_dict(include_job_files=True) for r in sorted(self.saved_results, key=lambda r: r.created_at, reverse=True)]
        return d


class SavedResult(Base):
    __tablename__ = 'saved_results'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    save_id: Mapped[str] = mapped_column(String(8), nullable=False, unique=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    result_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bounds: Mapped[Optional[List]] = mapped_column(JSON, nullable=True)
    colormap: Mapped[str] = mapped_column(String(50), nullable=False, default="viridis")
    vmin: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vmax: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey('projects.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="saved_results")

    __table_args__ = (
        Index('ix_saved_results_created', 'created_at'),
    )

    def to_dict(self, include_job_files: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "save_id": self.save_id,
            "user_id": self.user_id,
            "title": self.title,
            "result_path": self.result_path,
            "original_path": self.original_path,
            "bounds": self.bounds,
            "colormap": self.colormap,
            "vmin": self.vmin,
            "vmax": self.vmax,
            "size_bytes": self.size_bytes,
            "source_query": self.source_query,
            "job_id": self.job_id,
            "project_id": self.project_id,
            "created_at": self.created_at.isoformat(),
        }
        if include_job_files:
            d["job_files"] = self._scan_directory_files()
        return d

    def _scan_directory_files(self) -> List[Dict[str, Any]]:
        import os
        from pathlib import Path
        result_dir = Path(self.result_path).parent
        if not result_dir.exists():
            return []
        files = []
        for f in sorted(result_dir.iterdir()):
            if f.is_file() and f.name != "metadata.json":
                ext = f.suffix.lower().lstrip(".")
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "ext": ext,
                    "size_bytes": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
        return files


class BatchRun(Base):
    """Durable public-API batch: maps a batch_id to its per-location jobs.

    Replaces the module-level ``_batches`` dict in api/router.py so batch
    status survives backend restarts. Per-location job *status* still comes
    from the (currently volatile) job manager; this table persists the
    batch->jobs mapping, not job execution state.
    """
    __tablename__ = "batch_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # the batch_... id
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    webhook_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location_jobs: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    webhook_fired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_batch_runs_client", "client_id"),
    )


class ExecutionHistory(Base):
    """
    Execution history for analytics and debugging.

    Tracks all process graph executions with timing and results.
    """
    __tablename__ = 'execution_history'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey('ai_sessions.id'),
        nullable=True
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    process_graph: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    result_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    session: Mapped[Optional["AISession"]] = relationship(
        "AISession",
        back_populates="execution_history"
    )

    __table_args__ = (
        Index('ix_execution_history_user_created', 'user_id', 'created_at'),
        Index('ix_execution_history_status', 'status'),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": str(self.id),
            "session_id": str(self.session_id) if self.session_id else None,
            "user_id": self.user_id,
            "process_graph": self.process_graph,
            "status": self.status,
            "result_path": self.result_path,
            "error_message": self.error_message,
            "execution_time_ms": self.execution_time_ms,
            "tokens_used": self.tokens_used,
            "cost_estimate": self.cost_estimate,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }


class EarthAsset(Base):
    """A persistent Earth asset/AOI that can be watched over time."""
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(80), nullable=False, default="area")
    geometry: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    bbox: Mapped[Optional[List[float]]] = mapped_column(JSON, nullable=True)
    properties: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    external_refs: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    watches: Mapped[List["EarthWatch"]] = relationship("EarthWatch", back_populates="asset")
    observations: Mapped[List["EarthObservation"]] = relationship("EarthObservation", back_populates="asset")

    __table_args__ = (
        Index("ix_assets_user_updated", "user_id", "updated_at"),
        Index("ix_assets_project", "project_id"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "name": self.name,
            "asset_type": self.asset_type,
            "geometry": self.geometry,
            "bbox": self.bbox,
            "properties": self.properties or {},
            "external_refs": self.external_refs or {},
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "archived_at": _iso(self.archived_at),
        }


class EarthWatch(Base):
    """A recurring intelligence contract attached to an asset."""
    __tablename__ = "watches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active", index=True)
    template_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    process_graph: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    scf: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    collections: Mapped[List[str]] = mapped_column(JSON, default=list)
    trigger_type: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    cadence: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    temporal_policy: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_policy: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    budget_policy: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    asset: Mapped["EarthAsset"] = relationship("EarthAsset", back_populates="watches")
    observations: Mapped[List["EarthObservation"]] = relationship("EarthObservation", back_populates="watch")

    __table_args__ = (
        Index("ix_watches_user_status", "user_id", "status"),
        Index("ix_watches_next_run", "next_run_at"),
        _status_check(WATCH, "ck_watches_status"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "asset_id": self.asset_id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "template_id": self.template_id,
            "process_graph": self.process_graph or {},
            "scf": self.scf,
            "collections": self.collections or [],
            "trigger_type": self.trigger_type,
            "cadence": self.cadence,
            "temporal_policy": self.temporal_policy or {},
            "quality_policy": self.quality_policy or {},
            "budget_policy": self.budget_policy or {},
            "next_run_at": _iso(self.next_run_at),
            "last_run_at": _iso(self.last_run_at),
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


class CatalogItem(Base):
    """Normalized external acquisition/catalog metadata."""
    __tablename__ = "catalog_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    collection_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stac_id: Mapped[str] = mapped_column(String(255), nullable=False)
    datetime: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    geometry: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    bbox: Mapped[Optional[List[float]]] = mapped_column(JSON, nullable=True)
    cloud_cover: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    assets: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    properties: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "collection_id", "stac_id", name="uq_catalog_items_source"),
        Index("ix_catalog_items_collection_datetime", "collection_id", "datetime"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "collection_id": self.collection_id,
            "stac_id": self.stac_id,
            "datetime": _iso(self.datetime),
            "geometry": self.geometry,
            "bbox": self.bbox,
            "cloud_cover": self.cloud_cover,
            "assets": self.assets or {},
            "properties": self.properties or {},
            "ingested_at": _iso(self.ingested_at),
        }


class EarthObservation(Base):
    """One concrete run or run opportunity for a watch."""
    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    watch_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("watches.id", ondelete="SET NULL"), nullable=True, index=True)
    asset_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", index=True)
    window_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    window_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_catalog_item_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    openeo_job_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    api_job_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    process_graph_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    scf_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    quality_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_estimate: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    watch: Mapped[Optional["EarthWatch"]] = relationship("EarthWatch", back_populates="observations")
    asset: Mapped[Optional["EarthAsset"]] = relationship("EarthAsset", back_populates="observations")
    evidence_packages: Mapped[List["EvidencePackage"]] = relationship("EvidencePackage", back_populates="observation")
    events: Mapped[List["ObservationEvent"]] = relationship(
        "ObservationEvent",
        back_populates="observation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_observations_user_created", "user_id", "created_at"),
        Index("ix_observations_watch_status", "watch_id", "status"),
        _status_check(OBSERVATION, "ck_observations_status"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "watch_id": self.watch_id,
            "asset_id": self.asset_id,
            "status": self.status,
            "window_start": _iso(self.window_start),
            "window_end": _iso(self.window_end),
            "source_catalog_item_ids": self.source_catalog_item_ids or [],
            "openeo_job_id": self.openeo_job_id,
            "api_job_id": self.api_job_id,
            "process_graph_hash": self.process_graph_hash,
            "scf_hash": self.scf_hash,
            "quality_metrics": self.quality_metrics or {},
            "cost_estimate": self.cost_estimate or {},
            "error": self.error,
            "locked_by": self.locked_by,
            "locked_at": _iso(self.locked_at),
            "attempt_count": self.attempt_count,
            "next_attempt_at": _iso(self.next_attempt_at),
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
            "created_at": _iso(self.created_at),
        }


class ObservationEvent(Base):
    """Append-only event log for observation lifecycle and worker actions."""
    __tablename__ = "observation_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    observation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    observation: Mapped["EarthObservation"] = relationship("EarthObservation", back_populates="events")

    __table_args__ = (
        Index("ix_observation_events_user_created", "user_id", "created_at"),
        Index("ix_observation_events_observation_created", "observation_id", "created_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "observation_id": self.observation_id,
            "event_type": self.event_type,
            "message": self.message,
            "payload": self.payload or {},
            "created_at": _iso(self.created_at),
        }


class EvidencePackage(Base):
    """Immutable evidence generated from an observation and saved result."""
    __tablename__ = "evidence_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    observation_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("observations.id", ondelete="SET NULL"), nullable=True, index=True)
    saved_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("saved_results.id", ondelete="SET NULL"), nullable=True, index=True)
    saved_result_save_id: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)
    stac_item: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    source_items: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    artifacts: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    statistics: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    provenance: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    process_graph: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    scf: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    observation: Mapped[Optional["EarthObservation"]] = relationship("EarthObservation", back_populates="evidence_packages")

    __table_args__ = (
        Index("ix_evidence_user_created", "user_id", "created_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "observation_id": self.observation_id,
            "saved_result_id": str(self.saved_result_id) if self.saved_result_id else None,
            "saved_result_save_id": self.saved_result_save_id,
            "stac_item": self.stac_item or {},
            "source_items": self.source_items or [],
            "artifacts": self.artifacts or {},
            "statistics": self.statistics or {},
            "provenance": self.provenance or {},
            "process_graph": self.process_graph or {},
            "scf": self.scf,
            "trace_id": self.trace_id,
            "created_at": _iso(self.created_at),
        }


class Baseline(Base):
    """Historical distribution for an asset/watch metric."""
    __tablename__ = "baselines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    watch_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("watches.id", ondelete="SET NULL"), nullable=True, index=True)
    metric: Mapped[str] = mapped_column(String(120), nullable=False)
    season_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    window_policy: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    stats: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "asset_id", "watch_id", "metric", "season_key", name="uq_baselines_scope"),
        Index("ix_baselines_asset_metric", "asset_id", "metric"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "asset_id": self.asset_id,
            "watch_id": self.watch_id,
            "metric": self.metric,
            "season_key": self.season_key,
            "window_policy": self.window_policy or {},
            "stats": self.stats or {},
            "sample_count": self.sample_count,
            "version": self.version,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


class EarthSignal(Base):
    """A meaningful condition detected from an observation."""
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    asset_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True, index=True)
    watch_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("watches.id", ondelete="SET NULL"), nullable=True, index=True)
    observation_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("observations.id", ondelete="SET NULL"), nullable=True, index=True)
    signal_type: Mapped[str] = mapped_column(String(80), nullable=False, default="metric")
    metric: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    delta: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    severity: Mapped[str] = mapped_column(String(40), nullable=False, default="info", index=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    geometry: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open", index=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_signals_user_status", "user_id", "status"),
        Index("ix_signals_watch_created", "watch_id", "created_at"),
        _status_check(SIGNAL, "ck_signals_status"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "asset_id": self.asset_id,
            "watch_id": self.watch_id,
            "observation_id": self.observation_id,
            "signal_type": self.signal_type,
            "metric": self.metric,
            "value": self.value,
            "baseline_value": self.baseline_value,
            "delta": self.delta,
            "severity": self.severity,
            # Serialized as 'coverage' (the honest interim label — a 1−nan data
            # coverage fraction, not calibrated confidence). DB column stays 'confidence'.
            "coverage": self.confidence,
            "geometry": self.geometry,
            "status": self.status,
            "explanation": self.explanation,
            "created_at": _iso(self.created_at),
            "acknowledged_at": _iso(self.acknowledged_at),
            "resolved_at": _iso(self.resolved_at),
        }


class Delivery(Base):
    """Delivery attempt for a signal notification."""
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.id", ondelete="CASCADE"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    # When a dispatcher took it. A sending row whose claim is old was abandoned mid-send.
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # A failed attempt waits until then before the next one.
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_deliveries_user_status", "user_id", "status"),
        _status_check(DELIVERY, "ck_deliveries_status"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "signal_id": self.signal_id,
            "channel": self.channel,
            "target": self.target,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "last_error": self.last_error,
            "sent_at": _iso(self.sent_at),
            "created_at": _iso(self.created_at),
            "next_attempt_at": _iso(self.next_attempt_at),
        }


class ResultCopy(Base):
    """The offsite copy of one saved result (openeo_ai/storage/result_copies.py)."""
    __tablename__ = "result_copies"

    save_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("saved_results.save_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # Bytes stored remotely, counted against the budgets without listing the bucket.
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # 1 is full resolution; 2 or 4 when the result was too large and stored coarser.
    downsample: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    # Bumped on every write, so a copy finishing after the row was queued again or deleted
    # doesn't overwrite that newer state.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        Index("ix_result_copies_status_next_attempt", "status", "next_attempt_at"),
        _status_check(RESULT_COPY, "ck_result_copies_status"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "bytes": self.bytes,
            "downsample": self.downsample,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "synced_at": _iso(self.synced_at),
            "next_attempt_at": _iso(self.next_attempt_at),
        }


class PlanTrajectory(Base):
    """A successfully executed query plan, kept so future similar queries can
    seed the planner with what worked before (retrieval-augmented planning)."""
    __tablename__ = "plan_trajectories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    tool_sequence: Mapped[List[str]] = mapped_column(JSON, default=list)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="execute_workflow")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_plan_trajectories_user_created", "user_id", "created_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "query_text": self.query_text,
            "tier": self.tier,
            "plan_json": self.plan_json,
            "tool_sequence": self.tool_sequence,
            "step_count": self.step_count,
            "succeeded": self.succeeded,
            "source": self.source,
            "created_at": _iso(self.created_at),
        }
