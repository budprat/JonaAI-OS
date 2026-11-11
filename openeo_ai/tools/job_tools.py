# ABOUTME: Job management tools for batch processing operations.
# Create, start, monitor status, and retrieve results from OpenEO jobs.

"""
Job management tools for OpenEO AI Assistant.

Provides tools for creating, starting, monitoring, and
retrieving results from batch jobs.
"""

import json
import base64
import logging
import os
import time
import httpx
from typing import Any, Dict, Optional

from openeo_app.core.paths import result_root

logger = logging.getLogger(__name__)

# Wall-clock budget for openeo_create_job's status polling. Real jobs on this
# backend run 78-261s (observed live); a 120s budget ended turns before their
# jobs finished, so downstream steps saw "no results" for jobs that succeeded.
_CREATE_JOB_POLL_BUDGET_S = 300


class JobTools:
    """Job management tools."""

    def __init__(self, openeo_url: str, auth_token: Optional[str] = None, act_as_user: Optional[str] = None):
        """
        Initialize job tools.

        Args:
            openeo_url: Base URL of OpenEO API
            auth_token: Optional auth token (Basic or Bearer)
        """
        self.openeo_url = openeo_url.rstrip("/")
        self._client = None
        self._act_as_user = act_as_user

        # Get auth from explicit token or environment variable
        if auth_token:
            self.auth_header = auth_token
        elif os.environ.get("OPENEO_AUTH_TOKEN"):
            self.auth_header = f"Bearer {os.environ['OPENEO_AUTH_TOKEN']}"
        elif os.environ.get("OPENEO_DEV_MODE", "").lower() == "true":
            # Dev mode: use OPENEO_DEV_PASSWORD from env, never hardcoded
            dev_password = os.environ.get("OPENEO_DEV_PASSWORD", "")
            if not dev_password:
                logger.warning(
                    "OPENEO_DEV_MODE is true but OPENEO_DEV_PASSWORD is not set. "
                    "Set OPENEO_AUTH_TOKEN or OPENEO_DEV_PASSWORD for auth."
                )
            dev_creds = base64.b64encode(f"dev:{dev_password}".encode()).decode("utf-8")
            self.auth_header = f"Basic {dev_creds}"
        else:
            # No auth configured — log critical warning, use empty bearer
            # (will fail at server side, which is the correct behavior)
            logger.critical(
                "No authentication configured for OpenEO API. "
                "Set OPENEO_AUTH_TOKEN or OPENEO_DEV_PASSWORD env var."
            )
            self.auth_header = "Bearer unconfigured"

    async def _act_as_current_user(self, request: httpx.Request) -> None:
        """Log in as the current user, so OpenEO stores and enforces job ownership.

        Applies only to the dev Basic login; an explicit token is left alone. With no
        user (scripts, tests) the configured identity is used unchanged. Background work
        has no chat session, so it passes act_as_user instead.
        """
        from ..sdk.user_context import get_current_user_id

        user_id = self._act_as_user or get_current_user_id()
        if not user_id or not self.auth_header.startswith("Basic "):
            return
        if ":" in user_id:
            # Basic auth ends the username at the first colon, which would be someone else.
            raise ValueError("User id can't be used as an OpenEO login.")
        _, _, password = base64.b64decode(self.auth_header[6:]).decode("utf-8").partition(":")
        credentials = base64.b64encode(f"{user_id}:{password}".encode()).decode("utf-8")
        request.headers["Authorization"] = f"Basic {credentials}"

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-initialize HTTP client with auth."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={"Authorization": self.auth_header},
                event_hooks={"request": [self._act_as_current_user]},
            )
        return self._client

    async def close(self):
        """Close the HTTP client to release connection pool resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _create_job_request(
        self,
        title: str,
        description: str,
        process_graph: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send job creation request to API."""
        response = await self.client.post(
            f"{self.openeo_url}/jobs",
            json={
                "title": title,
                "description": description,
                "process": {"process_graph": process_graph}
            }
        )
        response.raise_for_status()

        # Get job ID from Location header or response body
        location = response.headers.get("Location", "")
        job_id = None
        if location:
            job_id = location.split("/")[-1]
        if not job_id:
            try:
                job_id = response.json().get("id")
            except Exception:
                pass
        if not job_id:
            raise ValueError("Job created but no job ID returned (no Location header or JSON body)")

        return {"id": job_id, "status": "created"}

    async def create(
        self,
        title: str,
        description: str,
        process_graph: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new batch job.

        Args:
            title: Job title
            description: Job description
            process_graph: OpenEO process graph

        Returns:
            Job info dict with id and status

        Raises:
            ValueError: If process_graph is empty
        """
        if not process_graph:
            raise ValueError("process_graph cannot be empty")

        return await self._create_job_request(title, description, process_graph)

    async def _start_job_request(self, job_id: str) -> Dict[str, Any]:
        """Send job start request to API with retry logic."""
        import asyncio

        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                # Small delay to ensure job is persisted
                if attempt > 0:
                    await asyncio.sleep(1)

                response = await self.client.post(
                    f"{self.openeo_url}/jobs/{job_id}/results"
                )

                if response.status_code == 404:
                    # Job might not be persisted yet, retry
                    last_error = f"Job {job_id} not found (attempt {attempt + 1}/{max_retries})"
                    continue

                response.raise_for_status()
                return {"id": job_id, "status": "running"}

            except httpx.HTTPStatusError as e:
                last_error = str(e)
                if e.response.status_code != 404:
                    raise

        return {"id": job_id, "status": "error", "error": last_error}

    async def start(self, job_id: str) -> Dict[str, Any]:
        """
        Start a queued batch job.

        Args:
            job_id: Job identifier

        Returns:
            Job info dict with updated status
        """
        return await self._start_job_request(job_id)

    async def _list_jobs_request(self, limit: int = 50) -> Dict[str, Any]:
        """List all batch jobs from the API."""
        response = await self.client.get(f"{self.openeo_url}/jobs")
        response.raise_for_status()
        data = response.json()
        jobs = data.get("jobs", [])
        # Return compact summary
        summary = []
        for j in jobs[:limit]:
            summary.append({
                "id": j.get("id"),
                "title": j.get("title", "Untitled"),
                "status": j.get("status", "unknown"),
                "created": j.get("created", ""),
            })
        return {"count": len(jobs), "jobs": summary}

    async def list_jobs(self, limit: int = 50, status: Optional[str] = None) -> Dict[str, Any]:
        """List batch jobs, optionally filtered by status."""
        result = await self._list_jobs_request(limit=limit)
        if status:
            result["jobs"] = [j for j in result["jobs"] if j["status"] == status]
            result["count"] = len(result["jobs"])
        return result

    async def _get_status_request(
        self, job_id: str, timeout=httpx.USE_CLIENT_DEFAULT
    ) -> Dict[str, Any]:
        """Get job status from API."""
        response = await self.client.get(
            f"{self.openeo_url}/jobs/{job_id}", timeout=timeout
        )
        response.raise_for_status()
        return response.json()

    async def _get_logs_request(self, job_id: str) -> list:
        """Fetch job logs from API."""
        try:
            response = await self.client.get(f"{self.openeo_url}/jobs/{job_id}/logs")
            response.raise_for_status()
            return response.json().get("logs", [])
        except Exception:
            return []

    async def get_status(
        self, job_id: str, timeout=httpx.USE_CLIENT_DEFAULT
    ) -> Dict[str, Any]:
        """
        Get job status, enriched with error details when status is error.

        Args:
            job_id: Job identifier
            timeout: Per-request httpx timeout override (poll loops pass a
                short one so a stalled request cannot burn the client default)

        Returns:
            Job status dict. When status is "error", includes:
            - error_message: User-friendly error description
            - error_logs: List of error-level log entries
        """
        result = await self._get_status_request(job_id, timeout=timeout)

        # When job has errored, fetch logs to provide error details
        if result.get("status") == "error":
            logs = await self._get_logs_request(job_id)
            error_logs = [
                log for log in logs
                if log.get("level") in ("error", "ERROR")
            ]
            if error_logs:
                # Primary error message from the last error log
                result["error_message"] = error_logs[-1].get("message", "Unknown error")
                # Include all error logs for detail
                result["error_logs"] = error_logs
            else:
                result["error_message"] = "Job failed. No detailed error logs available."
                result["error_logs"] = []

        return result

    async def _get_results_request(
        self,
        job_id: str,
        output_path: str
    ) -> Dict[str, Any]:
        """Get job results path."""
        import os
        from pathlib import Path

        # Results are stored at {result root}/results/{job_id}/
        results_base = result_root()
        job_dir = Path(results_base) / "results" / job_id

        # Check for result file
        result_path = None
        for ext in [".tif", ".nc", ".json", ".png"]:
            candidate = job_dir / f"result{ext}"
            if candidate.exists():
                result_path = candidate
                break

        # Check metadata for custom filename
        metadata_path = job_dir / "metadata.json"
        metadata = {}
        if metadata_path.exists():
            import json
            with open(metadata_path) as f:
                metadata = json.load(f)
                if not result_path:
                    result_file = metadata.get("result_file")
                    if result_file:
                        candidate = job_dir / result_file
                        if candidate.exists():
                            result_path = candidate

        if result_path and result_path.exists():
            # Get bounds from the GeoTIFF if available
            bounds = None
            if result_path.suffix in [".tif", ".tiff"]:
                try:
                    import rasterio
                    with rasterio.open(result_path) as src:
                        bounds = list(src.bounds)  # [west, south, east, north]
                except Exception:
                    pass

            return {
                "output_path": str(result_path),
                "path": str(result_path),
                "file_path": str(result_path),
                "format": metadata.get("format", "GTiff"),
                "size_bytes": result_path.stat().st_size,
                "bounds": bounds,
                "statistics": metadata.get("statistics"),
                "validation": metadata.get("validation"),
                "_instruction": f"IMPORTANT: Use this exact file_path for all subsequent tools: {result_path}"
            }
        else:
            # Result not ready yet - check job status
            try:
                status_response = await self.client.get(f"{self.openeo_url}/jobs/{job_id}")
                status_response.raise_for_status()
                job_status = status_response.json()
                return {
                    "error": f"Job results not ready. Status: {job_status.get('status', 'unknown')}",
                    "status": job_status.get("status"),
                    "job_id": job_id
                }
            except Exception as e:
                return {
                    "error": f"Results not found for job {job_id}",
                    "job_id": job_id
                }

    async def get_results(
        self,
        job_id: str,
        output_path: str = "/tmp"
    ) -> Dict[str, Any]:
        """
        Get job results.

        Args:
            job_id: Job identifier
            output_path: Directory to save results

        Returns:
            Result info dict with path
        """
        return await self._get_results_request(job_id, output_path)


def create_job_tools(config) -> Dict[str, Any]:
    """Create job tools dict for Claude SDK."""
    tools = JobTools(openeo_url=config.openeo_url)

    async def _create_job(args: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio

        # Step 1: Create
        result = await tools.create(
            title=args["title"],
            description=args.get("description", ""),
            process_graph=args["process_graph"]
        )
        job_id = result.get("id")
        if not job_id:
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        # Step 2: Auto-start
        start_result = await tools.start(job_id)
        if start_result.get("status") == "error":
            return {"content": [{"type": "text", "text": json.dumps(start_result)}]}

        # Step 3: Poll status wall-clock-bounded — a stalled status request
        # must consume the budget, not extend it (an iteration-count loop
        # with slow polls starved downstream specialists for minutes)
        deadline = time.monotonic() + _CREATE_JOB_POLL_BUDGET_S
        while time.monotonic() < deadline:
            await asyncio.sleep(10)
            try:
                status = await tools.get_status(job_id, timeout=10.0)
                job_status = status.get("status", "unknown")
                if job_status in ("finished", "completed"):
                    # Reason: Auto-fetch results so extract_visualization can
                    # detect output_path and create map visualization automatically.
                    # Without this, the job returns only {status: "finished"} and
                    # no map is displayed until the user separately calls get_results.
                    try:
                        results = await tools.get_results(job_id)
                        if results.get("output_path"):
                            status.update(results)
                    except Exception:
                        pass
                    # Capture workflow pattern from successful job
                    try:
                        from ..batch.template_store import TemplateStore
                        db_path = getattr(config, "sqlite_path", "data/openeo_ai.db")
                        store = TemplateStore(db_path)
                        store.capture_pattern(
                            process_graph=args["process_graph"],
                            source_query=args.get("title", ""),
                        )
                    except Exception:
                        pass  # Never block job results for pattern learning
                    return {"content": [{"type": "text", "text": json.dumps(status)}]}
                elif job_status in ("error", "canceled"):
                    return {"content": [{"type": "text", "text": json.dumps(status)}]}
            except Exception as poll_err:
                logger.warning(f"Job {job_id} poll error (will retry): {poll_err}")

        # Timed out — return last known status with timeout indication
        try:
            status = await tools.get_status(job_id)
        except Exception:
            status = {"id": job_id, "status": "running"}
        status["_polling_timeout"] = True
        status["_note"] = (
            f"Job polling timed out after {_CREATE_JOB_POLL_BUDGET_S}s. Job may "
            f"still be running. Use openeo_get_job_status with job_id='{job_id}' "
            f"to check later."
        )
        return {"content": [{"type": "text", "text": json.dumps(status)}]}

    async def _start_job(args: Dict[str, Any]) -> Dict[str, Any]:
        result = await tools.start(args["job_id"])
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _get_job_status(args: Dict[str, Any]) -> Dict[str, Any]:
        result = await tools.get_status(args["job_id"])
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _get_results(args: Dict[str, Any]) -> Dict[str, Any]:
        result = await tools.get_results(
            args["job_id"],
            args.get("output_path", "/tmp")
        )
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _list_jobs(args: Dict[str, Any]) -> Dict[str, Any]:
        result = await tools.list_jobs(
            limit=args.get("limit", 50),
            status=args.get("status"),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _result_load(args: Dict[str, Any]) -> Dict[str, Any]:
        """Load a previous job's result for chaining into subsequent tools."""
        from pathlib import Path

        job_id = args["job_id"]

        # Locate the result file
        results_base = result_root()
        job_dir = Path(results_base) / "results" / job_id

        if not job_dir.exists():
            return {
                "content": [{"type": "text", "text": f"Error: No results directory found for job {job_id}. Ensure the job has completed successfully."}]
            }

        # Find result file
        result_path = None
        for ext in [".tif", ".tiff", ".nc", ".json", ".png"]:
            candidate = job_dir / f"result{ext}"
            if candidate.exists():
                result_path = candidate
                break

        # Check metadata for custom filename
        metadata_path = job_dir / "metadata.json"
        metadata = {}
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)
                if not result_path:
                    result_file = metadata.get("result_file")
                    if result_file:
                        candidate = job_dir / result_file
                        if candidate.exists():
                            result_path = candidate

        if not result_path or not result_path.exists():
            return {
                "content": [{"type": "text", "text": f"Error: No result file found in {job_dir}. Job may still be running."}]
            }

        # Extract metadata from the result file
        file_info = {
            "file_path": str(result_path),
            "job_id": job_id,
            "format": result_path.suffix.lstrip("."),
            "size_bytes": result_path.stat().st_size,
        }

        if result_path.suffix in (".tif", ".tiff"):
            try:
                import rasterio
                with rasterio.open(result_path) as ds:
                    file_info["bands"] = ds.count
                    file_info["band_names"] = list(ds.descriptions) if any(ds.descriptions) else [f"Band {i+1}" for i in range(ds.count)]
                    file_info["crs"] = str(ds.crs) if ds.crs else None
                    file_info["bounds"] = list(ds.bounds)
                    file_info["resolution"] = list(ds.res)
                    file_info["width"] = ds.width
                    file_info["height"] = ds.height
            except Exception as e:
                file_info["metadata_error"] = str(e)

        summary_lines = [
            f"Loaded result from job {job_id}:",
            f"  File: {result_path}",
            f"  Format: {file_info['format']}",
            f"  Size: {file_info['size_bytes']:,} bytes",
        ]
        if "bands" in file_info:
            summary_lines.append(f"  Bands: {file_info['bands']} ({', '.join(file_info.get('band_names', []))})")
        if "crs" in file_info:
            summary_lines.append(f"  CRS: {file_info['crs']}")
        if "bounds" in file_info:
            b = file_info["bounds"]
            summary_lines.append(f"  Bounds: [{b[0]:.4f}, {b[1]:.4f}, {b[2]:.4f}, {b[3]:.4f}]")
        summary_lines.append("")
        summary_lines.append("Use file_path in subsequent tools (result_correlation, result_difference, raster_statistics, etc.) for chaining.")

        return {
            "content": [
                {"type": "text", "text": "\n".join(summary_lines)},
                {"type": "text", "text": json.dumps(file_info)},
            ]
        }

    return {
        "openeo_list_jobs": _list_jobs,
        "openeo_create_job": _create_job,
        "openeo_start_job": _start_job,
        "openeo_get_job_status": _get_job_status,
        "openeo_get_results": _get_results,
        "result_load": _result_load,
    }
