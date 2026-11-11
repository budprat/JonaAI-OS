# ABOUTME: Background batch execution engine using asyncio.
# Processes batch items with configurable concurrency, retry logic, and progress tracking.

"""
Batch Execution Engine

Processes batch job items asynchronously with:
- Configurable concurrency (semaphore-based)
- Per-item retry logic (up to 3 retries with exponential backoff)
- Continue-on-failure semantics
- Progress tracking via batch_store status updates
- Rate limiting for STAC/data provider protection
"""

import asyncio
import json
import logging
import os
import copy
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Awaitable

from ..lifecycle import BATCH_JOB, openeo_outcome
from .models import BatchStatus, TriggerType, BatchJobCreate, LocationInput
from .batch_store import BatchStore
from .template_store import TemplateStore

logger = logging.getLogger(__name__)

# ── Configuration ──

# Max concurrent OpenEO job submissions
DEFAULT_MAX_CONCURRENCY = 3
# Max retries per item
MAX_RETRIES = 3
# Base delay for exponential backoff (seconds)
RETRY_BASE_DELAY = 5
# Rate limit: minimum seconds between job submissions
RATE_LIMIT_DELAY = 2.0


class BatchExecutor:
    """Executes batch jobs by farming items to the OpenEO backend.

    Integrates with the existing JobTools for actual OpenEO API calls.
    """

    def __init__(
        self,
        batch_store: BatchStore,
        template_store: TemplateStore,
        openeo_url: Optional[str] = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ):
        self.batch_store = batch_store
        self.template_store = template_store
        self.openeo_url = openeo_url or os.environ.get(
            "OPENEO_URL", "http://localhost:8080/openeo/1.1.0"
        )
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._active_jobs: Dict[str, asyncio.Task] = {}

    async def execute_batch(self, batch_id: str) -> None:
        """Execute all pending items in a batch job.

        This is the main entry point. Creates an asyncio Task that processes
        items concurrently up to max_concurrency.

        Args:
            batch_id: The batch job ID to execute.
        """
        batch = self.batch_store.get_batch_job(batch_id)
        if not batch:
            logger.error("Batch job %s not found", batch_id)
            return

        template = self.template_store.get_template(batch.template_id)
        if not template:
            logger.error("Template %s not found for batch %s", batch.template_id, batch_id)
            return

        # Mark batch as running, unless it was cancelled before it got here
        conn = self.batch_store._conn()
        try:
            sources = BATCH_JOB.sources(BatchStatus.RUNNING.value)
            cursor = conn.execute(
                "UPDATE batch_jobs SET status = ?, started_at = ? WHERE id = ? "
                f"AND status IN ({', '.join('?' for _ in sources)})",
                (BatchStatus.RUNNING.value, datetime.utcnow().isoformat(), batch_id, *sources),
            )
            conn.commit()
        finally:
            conn.close()
        if cursor.rowcount == 0:
            logger.info("Batch %s is no longer startable; skipping", batch_id)
            return

        # Increment template use count
        self.template_store.increment_use_count(batch.template_id)

        # Get parameter overrides from batch job
        from openeo_ai import _pg_compat  # for json_loads helper that handles JSONB
        conn = self.batch_store._conn()
        try:
            row = conn.execute(
                "SELECT parameter_overrides FROM batch_jobs WHERE id = ?", (batch_id,)
            ).fetchone()
            overrides = _pg_compat.json_loads(row["parameter_overrides"], default={}) if row else {}
        finally:
            conn.close()

        # Process items
        items = self.batch_store.get_batch_items(batch_id, status=BatchStatus.PENDING)
        # Also pick up items that are QUEUED (claimed but not yet running)
        queued = self.batch_store.get_batch_items(batch_id, status=BatchStatus.QUEUED)
        all_items = items + queued

        if not all_items:
            logger.info("No pending items for batch %s", batch_id)
            return

        logger.info(
            "Executing batch %s: %d items, concurrency=%d",
            batch_id,
            len(all_items),
            self.max_concurrency,
        )

        # Create tasks for each item
        tasks = [
            self._process_item(
                item_id=item.id,
                location_name=item.location_name,
                spatial_extent=item.spatial_extent,
                template=template,
                overrides=overrides,
                act_as_user=getattr(batch, "user_id", None),
            )
            for item in all_items
        ]

        # Execute with concurrency control
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Batch %s execution complete", batch_id)

    async def _process_item(
        self,
        item_id: str,
        location_name: str,
        spatial_extent: Dict[str, float],
        template: Any,
        overrides: Dict[str, Any],
        act_as_user: Optional[str] = None,
    ) -> None:
        """Process a single batch item with retry logic.

        Args:
            item_id: Batch item ID.
            location_name: Human name of the location.
            spatial_extent: Bounding box dict.
            template: WorkflowTemplateResponse with process_graph and parameters.
            overrides: Parameter overrides from the batch job.
        """
        async with self._semaphore:
            # Rate limit between submissions
            await asyncio.sleep(RATE_LIMIT_DELAY)

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if not self.batch_store.update_item_status(item_id, BatchStatus.RUNNING):
                        logger.info("Item %s was cancelled; not submitting it", item_id)
                        return

                    # Build the parameterized process graph for this location
                    item_overrides = {**overrides, "spatial_extent": spatial_extent}
                    process_graph = TemplateStore.apply_parameters(
                        template.process_graph,
                        template.parameters,
                        item_overrides,
                    )

                    # Submit to OpenEO via JobTools
                    job_result = await self._submit_openeo_job(
                        title=f"{template.name} - {location_name}",
                        process_graph=process_graph,
                        act_as_user=act_as_user,
                    )

                    openeo_job_id = job_result.get("id")
                    if not openeo_job_id:
                        raise RuntimeError(
                            f"No job ID returned: {json.dumps(job_result)}"
                        )

                    # Poll for completion
                    final_status = await self._poll_job(openeo_job_id, act_as_user=act_as_user)

                    outcome = openeo_outcome(final_status.get("status"))
                    if outcome == "finished":
                        # Fetch results
                        result_info = await self._fetch_results(openeo_job_id, act_as_user=act_as_user)
                        result_summary = self._extract_summary(result_info)

                        self.batch_store.update_item_status(
                            item_id,
                            BatchStatus.COMPLETED,
                            openeo_job_id=openeo_job_id,
                            result_path=result_info.get("path"),
                            result_summary=result_summary,
                        )
                        logger.info(
                            "Item %s (%s) completed: job=%s",
                            item_id,
                            location_name,
                            openeo_job_id,
                        )
                        return  # Success, exit retry loop

                    elif outcome == "failed":
                        error_msg = final_status.get(
                            "error_message", "OpenEO job failed"
                        )
                        raise RuntimeError(error_msg)

                    elif outcome == "cancelled":
                        self.batch_store.update_item_status(
                            item_id,
                            BatchStatus.CANCELLED,
                            openeo_job_id=openeo_job_id,
                            error_message="The analysis job was cancelled",
                        )
                        return

                    else:
                        # Still running when polling gave up. It has no result, so it isn't
                        # complete; retrying would start a second job, so stop here.
                        self.batch_store.update_item_status(
                            item_id,
                            BatchStatus.FAILED,
                            openeo_job_id=openeo_job_id,
                            error_message="Timed out waiting for the analysis job; it may still finish",
                        )
                        return

                except asyncio.CancelledError:
                    self.batch_store.update_item_status(
                        item_id, BatchStatus.CANCELLED, error_message="The batch was cancelled"
                    )
                    raise
                except Exception as e:
                    logger.warning(
                        "Item %s (%s) attempt %d/%d failed: %s",
                        item_id,
                        location_name,
                        attempt,
                        MAX_RETRIES,
                        str(e),
                    )
                    if attempt < MAX_RETRIES:
                        delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                        await asyncio.sleep(delay)
                    else:
                        self.batch_store.update_item_status(
                            item_id,
                            BatchStatus.FAILED,
                            error_message=str(e),
                        )
                        logger.error(
                            "Item %s (%s) permanently failed after %d attempts",
                            item_id,
                            location_name,
                            MAX_RETRIES,
                        )

    async def _submit_openeo_job(
        self, title: str, process_graph: Dict[str, Any], act_as_user: Optional[str] = None
    ) -> Dict[str, Any]:
        """Submit a job to the OpenEO backend.

        Uses the existing JobTools infrastructure.
        """
        from ..tools.job_tools import JobTools

        tools = JobTools(openeo_url=self.openeo_url, act_as_user=act_as_user)
        result = await tools.create(
            title=title,
            description="Batch analysis item",
            process_graph=process_graph,
        )

        job_id = result.get("id")
        if job_id:
            start_result = await tools.start(job_id)
            return start_result

        return result

    async def _poll_job(
        self,
        job_id: str,
        timeout_seconds: int = 300,
        poll_interval: int = 10,
        act_as_user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Poll an OpenEO job until completion or timeout."""
        from ..tools.job_tools import JobTools

        tools = JobTools(openeo_url=self.openeo_url, act_as_user=act_as_user)
        elapsed = 0

        while elapsed < timeout_seconds:
            try:
                status = await tools.get_status(job_id)
                job_status = status.get("status", "unknown")
                if openeo_outcome(job_status) != "pending":
                    return status
            except Exception as e:
                logger.warning("Poll error for job %s: %s", job_id, e)

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        return {"id": job_id, "status": "running", "note": "poll timeout"}

    async def _fetch_results(self, job_id: str, act_as_user: Optional[str] = None) -> Dict[str, Any]:
        """Fetch results for a completed OpenEO job."""
        from ..tools.job_tools import JobTools

        tools = JobTools(openeo_url=self.openeo_url, act_as_user=act_as_user)
        return await tools.get_results(job_id)

    def _extract_summary(self, result_info: Dict[str, Any]) -> Dict[str, Any]:
        """Extract numeric summary stats from result metadata.

        The summary is stored in batch_items.result_summary for aggregation.
        """
        summary: Dict[str, Any] = {}

        if "statistics" in result_info and result_info["statistics"]:
            summary.update(result_info["statistics"])

        if "bounds" in result_info and result_info["bounds"]:
            summary["bounds"] = result_info["bounds"]

        summary["format"] = result_info.get("format", "unknown")
        summary["size_bytes"] = result_info.get("size_bytes", 0)

        return summary

    # ── Lifecycle ──

    def start_batch_async(self, batch_id: str) -> asyncio.Task:
        """Start batch execution as a background asyncio Task.

        Returns the task so callers can optionally await or track it.
        """
        task = asyncio.create_task(
            self.execute_batch(batch_id),
            name=f"batch-{batch_id}",
        )
        self._active_jobs[batch_id] = task

        # Cleanup callback
        def _on_done(t: asyncio.Task) -> None:
            self._active_jobs.pop(batch_id, None)
            if t.exception():
                logger.error("Batch %s task failed: %s", batch_id, t.exception())

        task.add_done_callback(_on_done)
        return task

    def get_active_batch_ids(self) -> List[str]:
        """List batch IDs currently being processed."""
        return list(self._active_jobs.keys())

    async def cancel_active_batch(self, batch_id: str) -> bool:
        """Cancel a running batch execution task."""
        task = self._active_jobs.get(batch_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True
        return False
