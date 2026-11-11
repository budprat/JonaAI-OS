# ABOUTME: Python SDK client for the Jona AI REST API.
# Provides a clean interface for programmatic Earth observation analysis.
# Supports both sync and async usage, with retry logic and streaming progress.

"""
Jona AI Python SDK

A developer-friendly client for the Jona AI Earth observation REST API.

Usage:
    from jona_ai import JonaClient

    # Quick start
    client = JonaClient(api_key="jona_sk_live_...")
    result = client.analyze("Calculate NDVI for Kerala, India, monsoon 2024")
    print(result.summary)
    result.download("output.tif")

    # Async usage
    async with JonaClient(api_key="...", async_mode=True) as client:
        job = await client.submit("Calculate NDVI for Kerala")
        result = await job.wait()

    # Batch analysis
    locations = [
        {"id": "kerala", "name": "Kerala", "bbox": {"west": 76, "south": 8, "east": 77.5, "north": 12.5}},
        {"id": "karnataka", "name": "Karnataka", "bbox": {"west": 74, "south": 11.5, "east": 78.5, "north": 18.5}},
    ]
    batch = client.batch_analyze("NDVI monsoon 2024", locations)
    batch.wait()
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Union
from urllib.parse import urljoin

logger = logging.getLogger("jona_ai.sdk")


class JonaAPIError(Exception):
    """Error returned by the Jona AI API."""

    def __init__(self, status_code: int, title: str, detail: str = "", error_type: str = ""):
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.error_type = error_type
        super().__init__(f"[{status_code}] {title}: {detail}")


class JonaTimeoutError(JonaAPIError):
    """Analysis timed out."""

    def __init__(self, job_id: str, timeout: int):
        super().__init__(408, "Analysis Timeout", f"Job {job_id} did not complete within {timeout}s")
        self.job_id = job_id


class JonaRateLimitError(JonaAPIError):
    """Rate limit exceeded."""

    def __init__(self, retry_after: str = ""):
        super().__init__(429, "Rate Limit Exceeded", f"Retry after: {retry_after}")
        self.retry_after = retry_after


@dataclass
class AnalysisResult:
    """Result of a completed Jona AI analysis."""

    job_id: str
    status: str
    query: str
    summary: Optional[str] = None
    process_graph: Optional[Dict[str, Any]] = None
    visualizations: List[Dict[str, Any]] = field(default_factory=list)
    quality_metrics: Optional[Dict[str, Any]] = None
    data_urls: Dict[str, str] = field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = None
    execution_time_ms: Optional[int] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None

    @property
    def quality_grade(self) -> Optional[str]:
        """Get the quality grade (A-F) if available."""
        if self.quality_metrics:
            return self.quality_metrics.get("grade")
        return None

    @property
    def quality_score(self) -> Optional[float]:
        """Get the overall quality score (0-1) if available."""
        if self.quality_metrics:
            return self.quality_metrics.get("overall_score") or self.quality_metrics.get("overallScore")
        return None

    def download(self, path: str, format: str = "geotiff") -> str:
        """Download result data to a local file.

        Args:
            path: Local file path to save to.
            format: Output format (geotiff, csv, json).

        Returns:
            Absolute path to the downloaded file.
        """
        # This method is populated by the client when creating the result
        if hasattr(self, "_client") and self._client:
            return self._client._download_result(self.job_id, path, format)
        raise JonaAPIError(500, "Client not available", "Result was not created through a JonaClient")


@dataclass
class JobHandle:
    """Handle to a running analysis job for polling and status checks."""

    job_id: str
    status: str = "queued"
    progress: Optional[float] = None
    current_step: Optional[str] = None
    _client: Optional[Any] = field(default=None, repr=False)

    def poll(self) -> "JobHandle":
        """Poll for updated status."""
        if self._client:
            updated = self._client.get_job_status(self.job_id)
            self.status = updated.get("status", self.status)
            self.progress = updated.get("progress")
            self.current_step = updated.get("current_step")
        return self

    def wait(
        self,
        timeout: int = 300,
        poll_interval: float = 2.0,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> AnalysisResult:
        """Block until the job completes or times out.

        Args:
            timeout: Maximum wait time in seconds.
            poll_interval: Seconds between status polls.
            on_progress: Optional callback(progress_pct, step_description).

        Returns:
            AnalysisResult on success.

        Raises:
            JonaTimeoutError: If job doesn't complete within timeout.
            JonaAPIError: If job fails.
        """
        start = time.time()
        while time.time() - start < timeout:
            self.poll()

            if on_progress and self.progress is not None:
                on_progress(self.progress, self.current_step or "")

            if self.status == "completed":
                if self._client:
                    return self._client._get_result(self.job_id)
                break

            if self.status == "failed":
                raise JonaAPIError(500, "Analysis Failed", f"Job {self.job_id} failed")

            if self.status == "cancelled":
                raise JonaAPIError(409, "Analysis Cancelled", f"Job {self.job_id} was cancelled")

            time.sleep(poll_interval)

        raise JonaTimeoutError(self.job_id, timeout)

    def cancel(self) -> bool:
        """Cancel the running analysis."""
        if self._client:
            return self._client.cancel_job(self.job_id)
        return False

    def stream_events(self) -> Iterator[Dict[str, Any]]:
        """Stream Server-Sent Events for real-time progress."""
        if self._client:
            yield from self._client._stream_events(self.job_id)


class JonaClient:
    """Python SDK client for the Jona AI REST API.

    Args:
        api_key: API key for authentication (X-API-Key header).
        base_url: Base URL of the Jona AI API.
        token: JWT token for authentication (alternative to api_key).
        timeout: Default HTTP timeout in seconds.
        max_retries: Maximum number of retries for transient failures.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "http://localhost:8080",
        token: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = None

    def _get_session(self):
        """Lazy-initialize HTTP session."""
        if self._session is None:
            try:
                import httpx
                headers = {"User-Agent": "JonaAI-Python-SDK/1.0"}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key
                elif self.token:
                    headers["Authorization"] = f"Bearer {self.token}"
                self._session = httpx.Client(
                    base_url=self.base_url,
                    headers=headers,
                    timeout=self.timeout,
                )
            except ImportError:
                import urllib.request
                logger.warning("httpx not installed, falling back to urllib (limited functionality)")
                self._session = None
        return self._session

    def close(self):
        """Close the HTTP session."""
        if self._session and hasattr(self._session, "close"):
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Core request method ───────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        stream: bool = False,
    ) -> Any:
        """Make an authenticated API request with retry logic."""
        session = self._get_session()
        url = f"{self.base_url}{path}"

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                if session:
                    response = session.request(
                        method, path, json=json_body, params=params
                    )

                    # Rate limit handling
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After", "60")
                        if attempt < self.max_retries:
                            wait = min(int(retry_after) if retry_after.isdigit() else 60, 300)
                            logger.warning(f"Rate limited. Waiting {wait}s (attempt {attempt + 1}/{self.max_retries})")
                            time.sleep(wait)
                            continue
                        raise JonaRateLimitError(retry_after)

                    # Server errors: retry with backoff
                    if response.status_code >= 500:
                        if attempt < self.max_retries:
                            wait = 2 ** attempt
                            logger.warning(f"Server error {response.status_code}. Retrying in {wait}s")
                            time.sleep(wait)
                            continue

                    # Parse error response
                    if response.status_code >= 400:
                        try:
                            error_data = response.json()
                            if isinstance(error_data, dict) and "detail" in error_data:
                                detail = error_data["detail"]
                                if isinstance(detail, dict):
                                    raise JonaAPIError(
                                        response.status_code,
                                        detail.get("title", "API Error"),
                                        detail.get("detail", ""),
                                        detail.get("type", ""),
                                    )
                            raise JonaAPIError(response.status_code, "API Error", str(error_data))
                        except (json.JSONDecodeError, ValueError):
                            raise JonaAPIError(response.status_code, "API Error", response.text)

                    return response.json()

                else:
                    # Fallback: urllib (limited, no retry)
                    import urllib.request
                    req = urllib.request.Request(url, method=method)
                    if self.api_key:
                        req.add_header("X-API-Key", self.api_key)
                    elif self.token:
                        req.add_header("Authorization", f"Bearer {self.token}")
                    req.add_header("Content-Type", "application/json")
                    if json_body:
                        req.data = json.dumps(json_body).encode()
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        return json.loads(resp.read())

            except (JonaAPIError, JonaRateLimitError):
                raise
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(f"Request failed: {e}. Retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise JonaAPIError(0, "Connection Error", str(e))

    # ── Authentication ────────────────────────────────────────────────

    @classmethod
    def from_credentials(
        cls,
        username: str,
        password: str,
        base_url: str = "http://localhost:8080",
    ) -> "JonaClient":
        """Create a client by authenticating with username/password.

        Args:
            username: Account username.
            password: Account password.
            base_url: API base URL.

        Returns:
            Authenticated JonaClient instance.
        """
        client = cls(base_url=base_url)
        result = client._request("POST", "/api/v1/auth/token", json_body={
            "username": username,
            "password": password,
        })
        client.token = result["access_token"]
        # Update session headers
        if client._session:
            client._session.headers["Authorization"] = f"Bearer {client.token}"
        return client

    # ── Analysis ──────────────────────────────────────────────────────

    def analyze(
        self,
        query: str,
        bbox: Optional[Dict[str, float]] = None,
        temporal_extent: Optional[Dict[str, str]] = None,
        timeout: int = 120,
        on_progress: Optional[Callable] = None,
        **kwargs,
    ) -> AnalysisResult:
        """Submit a query and wait for results (convenience method).

        This combines submit + poll + get_result into a single call.

        Args:
            query: Natural language analysis query.
            bbox: Bounding box dict with west/south/east/north.
            temporal_extent: Dict with start/end dates.
            timeout: Maximum wait time in seconds.
            on_progress: Optional callback(progress_pct, step_description).
            **kwargs: Additional parameters (collection, output_format, etc.)

        Returns:
            AnalysisResult with data and quality metrics.

        Example:
            result = client.analyze(
                "Show NDVI for Kerala during monsoon 2024",
                bbox={"west": 76, "south": 8, "east": 77.5, "north": 12.5},
                temporal_extent={"start": "2024-06-01", "end": "2024-09-30"},
            )
            print(f"Quality: {result.quality_grade}")
        """
        job = self.submit(query, bbox=bbox, temporal_extent=temporal_extent, **kwargs)
        return job.wait(timeout=timeout, on_progress=on_progress)

    def submit(
        self,
        query: str,
        bbox: Optional[Dict[str, float]] = None,
        temporal_extent: Optional[Dict[str, str]] = None,
        webhook_url: Optional[str] = None,
        project_id: Optional[str] = None,
        output_format: str = "geotiff",
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> JobHandle:
        """Submit an analysis query asynchronously.

        Returns immediately with a JobHandle for polling.

        Args:
            query: Natural language analysis query.
            bbox: Optional bounding box.
            temporal_extent: Optional date range.
            webhook_url: URL to POST results to when done.
            project_id: Project to associate results with.
            output_format: Desired output format.
            metadata: Arbitrary metadata to attach.

        Returns:
            JobHandle for tracking progress.
        """
        body: Dict[str, Any] = {"query": query, "output_format": output_format}
        if bbox:
            body["bbox"] = bbox
        if temporal_extent:
            body["temporal_extent"] = temporal_extent
        if webhook_url:
            body["webhook_url"] = webhook_url
        if project_id:
            body["project_id"] = project_id
        if metadata:
            body["metadata"] = metadata

        result = self._request("POST", "/api/v1/analyze", json_body=body)
        handle = JobHandle(
            job_id=result["job_id"],
            status=result.get("status", "queued"),
            _client=self,
        )
        return handle

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get the current status of a job."""
        return self._request("GET", f"/api/v1/analyze/{job_id}")

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job."""
        try:
            self._request("DELETE", f"/api/v1/analyze/{job_id}")
            return True
        except JonaAPIError:
            return False

    def _get_result(self, job_id: str) -> AnalysisResult:
        """Get the full result for a completed job."""
        data = self.get_job_status(job_id)
        result = AnalysisResult(
            job_id=job_id,
            status=data.get("status", "completed"),
            query=data.get("query", ""),
            summary=data.get("result_summary"),
            process_graph=data.get("process_graph"),
            visualizations=data.get("visualizations", []),
            quality_metrics=data.get("quality_metrics"),
            data_urls=data.get("data_urls", {}),
            metadata=data.get("metadata"),
            execution_time_ms=data.get("execution_time_ms"),
            created_at=data.get("created_at"),
            completed_at=data.get("completed_at"),
        )
        result._client = self
        return result

    def _download_result(self, job_id: str, path: str, format: str = "geotiff") -> str:
        """Download result data to a local file."""
        session = self._get_session()
        if session:
            response = session.get(
                f"/api/v1/results/{job_id}/data",
                params={"format": format},
            )
            if response.status_code != 200:
                raise JonaAPIError(response.status_code, "Download Failed", response.text)
            dest = Path(path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.content)
            return str(dest.resolve())
        raise JonaAPIError(500, "Download Failed", "HTTP client not available")

    def _stream_events(self, job_id: str) -> Iterator[Dict[str, Any]]:
        """Stream SSE events from a running job."""
        session = self._get_session()
        if session:
            with session.stream("GET", f"/api/v1/analyze/{job_id}/events") as response:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError:
                            pass

    # ── Templates ─────────────────────────────────────────────────────

    def list_templates(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """List available workflow templates."""
        params = {}
        if category:
            params["category"] = category
        return self._request("GET", "/api/v1/templates", params=params)

    def execute_template(
        self,
        template_id: str,
        parameters: Dict[str, Any],
        output_format: str = "geotiff",
        webhook_url: Optional[str] = None,
    ) -> JobHandle:
        """Execute a workflow template."""
        result = self._request(
            "POST",
            f"/api/v1/templates/{template_id}/execute",
            json_body={
                "parameters": parameters,
                "output_format": output_format,
                "webhook_url": webhook_url,
            },
        )
        return JobHandle(
            job_id=result["job_id"],
            status=result.get("status", "queued"),
            _client=self,
        )

    # ── Batch ─────────────────────────────────────────────────────────

    def batch_analyze(
        self,
        query: str,
        locations: List[Dict[str, Any]],
        temporal_extent: Optional[Dict[str, str]] = None,
        output_format: str = "geotiff",
        webhook_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a batch analysis for multiple locations.

        Args:
            query: Natural language query applied to each location.
            locations: List of dicts with id, name, and bbox keys.
            temporal_extent: Optional date range.
            output_format: Desired output format.
            webhook_url: URL to POST batch results to.

        Returns:
            Batch status dict with batch_id.
        """
        return self._request("POST", "/api/v1/batch", json_body={
            "query": query,
            "locations": locations,
            "temporal_extent": temporal_extent,
            "output_format": output_format,
            "webhook_url": webhook_url,
        })

    def get_batch_status(self, batch_id: str) -> Dict[str, Any]:
        """Get the status of a batch analysis."""
        return self._request("GET", f"/api/v1/batch/{batch_id}")

    # ── API Key Management ────────────────────────────────────────────

    def create_api_key(self, name: str, scopes: Optional[List[str]] = None) -> Dict[str, Any]:
        """Create a new API key."""
        body: Dict[str, Any] = {"name": name}
        if scopes:
            body["scopes"] = scopes
        return self._request("POST", "/api/v1/auth/keys", json_body=body)

    def list_api_keys(self) -> List[Dict[str, Any]]:
        """List all API keys."""
        return self._request("GET", "/api/v1/auth/keys")

    def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        try:
            self._request("DELETE", f"/api/v1/auth/keys/{key_id}")
            return True
        except JonaAPIError:
            return False
