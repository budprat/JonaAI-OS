# ABOUTME: In-memory job manager for the Jona AI REST API v1.
# Tracks async analysis jobs, manages lifecycle, and dispatches webhook callbacks.

"""
Async job manager for Jona AI analysis requests.

Handles the submit -> queue -> run -> complete/fail lifecycle.
Jobs are stored in-memory with an optional persistence layer.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# In-memory job store (production: Redis or PostgreSQL)
_jobs: Dict[str, Dict[str, Any]] = {}
_MAX_JOBS = 10_000


class JobManager:
    """Manages analysis job lifecycle."""

    @staticmethod
    def create_job(
        query: str,
        client_id: str,
        user_id: str,
        bbox: Optional[dict] = None,
        temporal_extent: Optional[dict] = None,
        collection: Optional[str] = None,
        output_format: str = "geotiff",
        webhook_url: Optional[str] = None,
        project_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        template_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new analysis job and return its metadata."""
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        now = datetime.utcnow()

        job = {
            "job_id": job_id,
            "query": query,
            "client_id": client_id,
            "user_id": user_id,
            "status": "queued",
            "progress": 0.0,
            "current_step": None,
            "steps_completed": 0,
            "steps_total": None,
            "bbox": bbox,
            "temporal_extent": temporal_extent,
            "collection": collection,
            "output_format": output_format,
            "webhook_url": webhook_url,
            "project_id": project_id,
            "metadata": metadata,
            "template_id": template_id,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "result_summary": None,
            "process_graph": None,
            "visualizations": [],
            "quality_metrics": None,
            "data_urls": {},
            "save_id": None,
            "execution_time_ms": None,
            # Internal
            "_task": None,
            "_stop_event": None,
        }

        # Evict oldest jobs if at capacity
        if len(_jobs) >= _MAX_JOBS:
            oldest_key = min(
                (k for k, v in _jobs.items() if v["status"] in ("completed", "failed", "cancelled")),
                key=lambda k: _jobs[k]["created_at"],
                default=None,
            )
            if oldest_key:
                del _jobs[oldest_key]

        _jobs[job_id] = job
        logger.info(f"Created job {job_id} for client {client_id}: {query[:60]}")
        return job

    @staticmethod
    def get_job(job_id: str) -> Optional[Dict[str, Any]]:
        """Get job by ID."""
        return _jobs.get(job_id)

    @staticmethod
    def get_job_for_client(job_id: str, client_id: str) -> Optional[Dict[str, Any]]:
        """Get job by ID, scoped to the requesting client."""
        job = _jobs.get(job_id)
        if job and job["client_id"] == client_id:
            return job
        return None

    @staticmethod
    def update_job(job_id: str, **updates) -> Optional[Dict[str, Any]]:
        """Update job fields."""
        job = _jobs.get(job_id)
        if not job:
            return None
        for key, value in updates.items():
            if key in job and not key.startswith("_"):
                job[key] = value
        return job

    @staticmethod
    def cancel_job(job_id: str, client_id: str) -> bool:
        """Cancel a running or queued job."""
        job = _jobs.get(job_id)
        if not job or job["client_id"] != client_id:
            return False
        if job["status"] not in ("queued", "running"):
            return False

        job["status"] = "cancelled"
        job["completed_at"] = datetime.utcnow()

        # Signal stop to the running task
        stop_event = job.get("_stop_event")
        if stop_event and isinstance(stop_event, asyncio.Event):
            stop_event.set()

        task = job.get("_task")
        if task and isinstance(task, asyncio.Task) and not task.done():
            task.cancel()

        logger.info(f"Cancelled job {job_id}")
        return True

    @staticmethod
    def list_jobs_for_client(
        client_id: str,
        limit: int = 20,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List jobs for a client, newest first."""
        jobs = [
            j for j in _jobs.values()
            if j["client_id"] == client_id
            and (status is None or j["status"] == status)
        ]
        jobs.sort(key=lambda j: j["created_at"], reverse=True)
        return jobs[:limit]

    @staticmethod
    def to_status_response(job: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal job dict to API status response."""
        job_id = job["job_id"]
        links = {
            "self": f"/api/v1/analyze/{job_id}",
        }
        if job["status"] in ("queued", "running"):
            links["cancel"] = f"/api/v1/analyze/{job_id}"
            links["events"] = f"/api/v1/analyze/{job_id}/events"
        if job["status"] == "completed":
            links["data"] = f"/api/v1/results/{job_id}/data"
            links["report"] = f"/api/v1/results/{job_id}/report"
            links["process_graph"] = f"/api/v1/results/{job_id}/process-graph"
            links["metrics"] = f"/api/v1/results/{job_id}/metrics"

        return {
            "job_id": job_id,
            "status": job["status"],
            "progress": job.get("progress"),
            "current_step": job.get("current_step"),
            "steps_completed": job.get("steps_completed", 0),
            "steps_total": job.get("steps_total"),
            "created_at": job["created_at"].isoformat() if job["created_at"] else None,
            "started_at": job["started_at"].isoformat() if job.get("started_at") else None,
            "completed_at": job["completed_at"].isoformat() if job.get("completed_at") else None,
            "error": job.get("error"),
            "result_summary": job.get("result_summary"),
            "links": links,
        }

    @staticmethod
    def to_result_response(job: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal job dict to full result response."""
        job_id = job["job_id"]
        links = {
            "self": f"/api/v1/analyze/{job_id}",
            "data": f"/api/v1/results/{job_id}/data",
            "report": f"/api/v1/results/{job_id}/report",
            "process_graph": f"/api/v1/results/{job_id}/process-graph",
            "metrics": f"/api/v1/results/{job_id}/metrics",
        }

        return {
            "job_id": job_id,
            "status": job["status"],
            "query": job["query"],
            "process_graph": job.get("process_graph"),
            "result_summary": job.get("result_summary"),
            "visualizations": job.get("visualizations", []),
            "quality_metrics": job.get("quality_metrics"),
            "data_urls": job.get("data_urls", {}),
            "metadata": job.get("metadata"),
            "created_at": job["created_at"].isoformat() if job["created_at"] else None,
            "completed_at": job["completed_at"].isoformat() if job.get("completed_at") else None,
            "execution_time_ms": job.get("execution_time_ms"),
            "links": links,
        }


# Module-level convenience
job_manager = JobManager()
