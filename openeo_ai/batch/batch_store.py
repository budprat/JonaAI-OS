# ABOUTME: Batch job and batch item CRUD against PostgreSQL.
# Manages lifecycle of batch jobs, individual items, and their status transitions.

"""
Batch Job Store

Handles creation, status updates, and queries for batch jobs and their items.
Each batch job contains multiple items (one per location).

Uses the _pg_compat shim so the original SQLite-style queries (with `?`
placeholders) work unchanged against PostgreSQL.
"""

import uuid
import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from openeo_ai import _pg_compat as sqlite3  # PostgreSQL shim with sqlite3 API

from ..lifecycle import BATCH_ITEM, BATCH_JOB, IllegalTransition
from .models import (
    BatchStatus,
    TriggerType,
    BatchJobCreate,
    BatchJobResponse,
    BatchItemResponse,
    BatchResultSummary,
    ResourceEstimate,
    init_batch_db,
)


class BatchStore:
    """SQLite-backed store for batch jobs and items."""

    # Estimated seconds per analysis item (conservative for STAC queries)
    _EST_SECONDS_PER_ITEM = 30
    # Estimated MB per result file
    _EST_MB_PER_RESULT = 5.0

    def __init__(self, db_path: str = "data/openeo_ai.db"):
        self.db_path = db_path
        init_batch_db(db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── Batch Job CRUD ──

    def create_batch_job(
        self,
        data: BatchJobCreate,
        user_id: str,
        trigger_type: TriggerType = TriggerType.MANUAL,
        schedule_id: Optional[str] = None,
    ) -> BatchJobResponse:
        """Create a batch job with its items.

        Args:
            data: Batch job creation payload with locations.
            user_id: Authenticated user.
            trigger_type: How this batch was triggered.
            schedule_id: Associated schedule ID (if triggered by schedule).

        Returns:
            Created batch job response.
        """
        batch_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        est_duration = len(data.locations) * self._EST_SECONDS_PER_ITEM

        conn = self._conn()
        try:
            # Insert batch job
            conn.execute(
                """
                INSERT INTO batch_jobs
                    (id, template_id, name, status, trigger_type, schedule_id,
                     total_items, parameter_overrides, project_id, user_id,
                     created_at, estimated_duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    data.template_id,
                    data.name or f"Batch {now[:10]}",
                    BatchStatus.PENDING.value,
                    trigger_type.value,
                    schedule_id,
                    len(data.locations),
                    json.dumps(data.parameter_overrides),
                    data.project_id,
                    user_id,
                    now,
                    est_duration,
                ),
            )

            # Insert individual items
            for loc in data.locations:
                item_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO batch_items
                        (id, batch_job_id, location_name, spatial_extent,
                         location_metadata, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        batch_id,
                        loc.name,
                        json.dumps(loc.spatial_extent),
                        json.dumps(loc.metadata),
                        BatchStatus.PENDING.value,
                        now,
                    ),
                )

            conn.commit()
            return self.get_batch_job(batch_id)
        finally:
            conn.close()

    def get_batch_job(self, batch_id: str) -> Optional[BatchJobResponse]:
        """Load a batch job by ID."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM batch_jobs WHERE id = ?", (batch_id,)
            ).fetchone()
            if not row:
                return None
            return self._job_row_to_response(row)
        finally:
            conn.close()

    def list_batch_jobs(
        self,
        user_id: str,
        *,
        status: Optional[BatchStatus] = None,
        template_id: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[BatchJobResponse], int]:
        """List batch jobs with optional filters."""
        conn = self._conn()
        try:
            conditions = ["user_id = ?"]
            params: List[Any] = [user_id]

            if status:
                conditions.append("status = ?")
                params.append(status.value)
            if template_id:
                conditions.append("template_id = ?")
                params.append(template_id)
            if project_id:
                conditions.append("project_id = ?")
                params.append(project_id)

            where = " AND ".join(conditions)

            total = conn.execute(
                f"SELECT COUNT(*) FROM batch_jobs WHERE {where}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"""
                SELECT * FROM batch_jobs
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()

            return [self._job_row_to_response(r) for r in rows], total
        finally:
            conn.close()

    def cancel_batch_job(self, batch_id: str, user_id: str) -> bool:
        """Cancel a batch job and all its pending items."""
        conn = self._conn()
        try:
            # Verify ownership
            row = conn.execute(
                "SELECT id FROM batch_jobs WHERE id = ? AND user_id = ?",
                (batch_id, user_id),
            ).fetchone()
            if not row:
                return False

            now = datetime.utcnow().isoformat()
            job_sources = BATCH_JOB.sources(BatchStatus.CANCELLED.value)
            cursor = conn.execute(
                "UPDATE batch_jobs SET status = ?, completed_at = ? WHERE id = ? "
                f"AND status IN ({', '.join('?' for _ in job_sources)})",
                (BatchStatus.CANCELLED.value, now, batch_id, *job_sources),
            )
            if cursor.rowcount == 0:
                current = conn.execute(
                    "SELECT status FROM batch_jobs WHERE id = ?", (batch_id,)
                ).fetchone()
                raise IllegalTransition(BATCH_JOB, current["status"], BatchStatus.CANCELLED.value)
            # Running items too: their executor may be gone, and it can't overwrite this.
            item_sources = tuple(s for s in BATCH_ITEM.sources(BatchStatus.CANCELLED.value)
                                 if s != BatchStatus.CANCELLED.value)
            conn.execute(
                "UPDATE batch_items SET status = ?, completed_at = ? WHERE batch_job_id = ? "
                f"AND status IN ({', '.join('?' for _ in item_sources)})",
                (BatchStatus.CANCELLED.value, now, batch_id, *item_sources),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    # ── Batch Item Operations ──

    def get_batch_items(
        self,
        batch_id: str,
        *,
        status: Optional[BatchStatus] = None,
    ) -> List[BatchItemResponse]:
        """Get all items for a batch job."""
        conn = self._conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM batch_items WHERE batch_job_id = ? AND status = ? ORDER BY location_name",
                    (batch_id, status.value),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM batch_items WHERE batch_job_id = ? ORDER BY location_name",
                    (batch_id,),
                ).fetchall()

            return [self._item_row_to_response(r) for r in rows]
        finally:
            conn.close()

    def get_next_pending_items(self, batch_id: str, count: int = 1) -> List[Dict[str, Any]]:
        """Atomically claim the next N pending items for processing.

        Sets their status to QUEUED and returns their data.
        This prevents double-processing in concurrent workers.
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT id, location_name, spatial_extent, location_metadata
                FROM batch_items
                WHERE batch_job_id = ? AND status = ?
                ORDER BY rowid
                LIMIT ?
                """,
                (batch_id, BatchStatus.PENDING.value, count),
            ).fetchall()

            if not rows:
                return []

            now = datetime.utcnow().isoformat()
            item_ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(item_ids))

            conn.execute(
                f"UPDATE batch_items SET status = ?, started_at = ? WHERE id IN ({placeholders})",
                [BatchStatus.QUEUED.value, now] + item_ids,
            )
            conn.commit()

            return [
                {
                    "id": r["id"],
                    "location_name": r["location_name"],
                    "spatial_extent": json.loads(r["spatial_extent"]),
                    "location_metadata": json.loads(r["location_metadata"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def update_item_status(
        self,
        item_id: str,
        status: BatchStatus,
        *,
        openeo_job_id: Optional[str] = None,
        result_path: Optional[str] = None,
        result_summary: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Update a batch item's status and results. False if its state no longer allows it."""
        conn = self._conn()
        try:
            updates = ["status = ?"]
            params: List[Any] = [status.value]

            if openeo_job_id is not None:
                updates.append("openeo_job_id = ?")
                params.append(openeo_job_id)
            if result_path is not None:
                updates.append("result_path = ?")
                params.append(result_path)
            if result_summary is not None:
                updates.append("result_summary = ?")
                params.append(json.dumps(result_summary))
            if error_message is not None:
                updates.append("error_message = ?")
                params.append(error_message)

            if status == BatchStatus.RUNNING:
                updates.append("started_at = ?")
                params.append(datetime.utcnow().isoformat())
            elif status in (BatchStatus.COMPLETED, BatchStatus.FAILED):
                updates.append("completed_at = ?")
                params.append(datetime.utcnow().isoformat())

            if status == BatchStatus.FAILED:
                updates.append("retry_count = retry_count + 1")

            # Only from a state that allows this move: a cancelled item stays cancelled
            # even if its executor was still working on it.
            sources = BATCH_ITEM.sources(status.value)
            params.append(item_id)
            params.extend(sources)
            cursor = conn.execute(
                f"UPDATE batch_items SET {', '.join(updates)} WHERE id = ? "
                f"AND status IN ({', '.join('?' for _ in sources)})",
                params,
            )
            conn.commit()
            if cursor.rowcount == 0:
                return False

            # Update parent batch job counts
            batch_row = conn.execute(
                "SELECT batch_job_id FROM batch_items WHERE id = ?", (item_id,)
            ).fetchone()
            if batch_row:
                self._refresh_batch_counts(conn, batch_row["batch_job_id"])
                conn.commit()
            return True
        finally:
            conn.close()

    def _refresh_batch_counts(self, conn: sqlite3.Connection, batch_id: str) -> None:
        """Recount completed/failed items and update batch job status."""
        counts = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
            FROM batch_items WHERE batch_job_id = ?
            """,
            (batch_id,),
        ).fetchone()

        total = counts["total"]
        completed = counts["completed"]
        failed = counts["failed"]

        # Determine batch-level status
        if completed + failed == total:
            if failed == 0:
                batch_status = BatchStatus.COMPLETED.value
            elif completed == 0:
                batch_status = BatchStatus.FAILED.value
            else:
                batch_status = BatchStatus.PARTIALLY_COMPLETED.value
            now = datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE batch_jobs SET completed_items = ?, failed_items = ? WHERE id = ?",
                (completed, failed, batch_id),
            )
            # A cancelled batch keeps its status when its last items settle.
            sources = BATCH_JOB.sources(batch_status)
            conn.execute(
                "UPDATE batch_jobs SET status = ?, completed_at = ? WHERE id = ? "
                f"AND status IN ({', '.join('?' for _ in sources)})",
                (batch_status, now, batch_id, *sources),
            )
        else:
            conn.execute(
                "UPDATE batch_jobs SET completed_items = ?, failed_items = ? WHERE id = ?",
                (completed, failed, batch_id),
            )

    # ── Resource Estimation ──

    def estimate_resources(self, location_count: int) -> ResourceEstimate:
        """Estimate resources required for a batch run."""
        warnings = []
        if location_count > 100:
            warnings.append(
                f"Large batch ({location_count} locations). Consider splitting into smaller batches."
            )
        if location_count > 200:
            warnings.append(
                "Batch exceeds recommended maximum of 200. Rate limiting may apply."
            )

        return ResourceEstimate(
            total_items=location_count,
            estimated_duration_seconds=location_count * self._EST_SECONDS_PER_ITEM,
            estimated_data_volume_mb=location_count * self._EST_MB_PER_RESULT,
            estimated_api_calls=location_count * 3,  # create + start + poll
            warnings=warnings,
        )

    # ── Results Aggregation ──

    def get_batch_results(self, batch_id: str) -> Optional[BatchResultSummary]:
        """Aggregate results for a completed batch job."""
        conn = self._conn()
        try:
            job_row = conn.execute(
                "SELECT * FROM batch_jobs WHERE id = ?", (batch_id,)
            ).fetchone()
            if not job_row:
                return None

            # Get template name
            tpl_row = conn.execute(
                "SELECT name FROM workflow_templates WHERE id = ?",
                (job_row["template_id"],),
            ).fetchone()
            template_name = tpl_row["name"] if tpl_row else "Unknown"

            items = self.get_batch_items(batch_id)

            # Aggregate numeric stats from result_summary across items
            aggregated = self._aggregate_stats(items)

            # Check alert rules
            alerts = self._check_alerts(conn, job_row["template_id"], items, batch_id)

            return BatchResultSummary(
                batch_job_id=batch_id,
                template_name=template_name,
                total_locations=len(items),
                completed=sum(1 for i in items if i.status == BatchStatus.COMPLETED),
                failed=sum(1 for i in items if i.status == BatchStatus.FAILED),
                items=items,
                alerts=alerts,
                aggregated_stats=aggregated,
            )
        finally:
            conn.close()

    def _aggregate_stats(self, items: List[BatchItemResponse]) -> Dict[str, Any]:
        """Compute cross-location aggregate statistics."""
        numeric_values: Dict[str, List[float]] = {}

        for item in items:
            if item.result_summary:
                for key, val in item.result_summary.items():
                    if isinstance(val, (int, float)):
                        numeric_values.setdefault(key, []).append(val)

        stats = {}
        for key, values in numeric_values.items():
            if values:
                stats[key] = {
                    "min": min(values),
                    "max": max(values),
                    "mean": sum(values) / len(values),
                    "count": len(values),
                }
        return stats

    def _check_alerts(
        self,
        conn: sqlite3.Connection,
        template_id: str,
        items: List[BatchItemResponse],
        batch_job_id: str,
    ) -> List[Dict[str, Any]]:
        """Evaluate alert rules against batch results."""
        rules = conn.execute(
            "SELECT * FROM alert_rules WHERE template_id = ? AND is_active = 1",
            (template_id,),
        ).fetchall()

        if not rules:
            return []

        ops = {
            "lt": lambda a, b: a < b,
            "gt": lambda a, b: a > b,
            "lte": lambda a, b: a <= b,
            "gte": lambda a, b: a >= b,
            "eq": lambda a, b: a == b,
        }

        alerts = []
        now = datetime.utcnow().isoformat()

        for rule in rules:
            op_fn = ops.get(rule["operator"])
            if not op_fn:
                continue

            for item in items:
                if not item.result_summary:
                    continue
                metric_val = item.result_summary.get(rule["metric_path"])
                if metric_val is None or not isinstance(metric_val, (int, float)):
                    continue

                if op_fn(metric_val, rule["threshold_value"]):
                    alert_id = str(uuid.uuid4())
                    message = (
                        f"Alert: {rule['name']} - {item.location_name}: "
                        f"{rule['metric_path']}={metric_val} "
                        f"{rule['operator']} {rule['threshold_value']}"
                    )
                    alerts.append({
                        "id": alert_id,
                        "rule_name": rule["name"],
                        "location_name": item.location_name,
                        "metric_path": rule["metric_path"],
                        "metric_value": metric_val,
                        "threshold": rule["threshold_value"],
                        "operator": rule["operator"],
                        "message": message,
                    })

                    # Persist alert
                    conn.execute(
                        """
                        INSERT INTO alert_history
                            (id, alert_rule_id, batch_item_id, batch_job_id,
                             location_name, metric_value, threshold_value,
                             message, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            alert_id,
                            rule["id"],
                            item.id,
                            batch_job_id,
                            item.location_name,
                            metric_val,
                            rule["threshold_value"],
                            message,
                            now,
                        ),
                    )

            conn.commit()

        return alerts

    # ── Internal ──

    def _job_row_to_response(self, row: sqlite3.Row) -> BatchJobResponse:
        return BatchJobResponse(
            id=row["id"],
            user_id=row["user_id"],
            template_id=row["template_id"],
            name=row["name"],
            status=BatchStatus(row["status"]),
            total_items=row["total_items"],
            completed_items=row["completed_items"] or 0,
            failed_items=row["failed_items"] or 0,
            trigger_type=TriggerType(row["trigger_type"]),
            project_id=row["project_id"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            estimated_duration_seconds=row["estimated_duration_seconds"],
        )

    def _item_row_to_response(self, row: sqlite3.Row) -> BatchItemResponse:
        return BatchItemResponse(
            id=row["id"],
            batch_job_id=row["batch_job_id"],
            location_name=row["location_name"],
            spatial_extent=sqlite3.json_loads(row["spatial_extent"], default={}),
            status=BatchStatus(row["status"]),
            openeo_job_id=row["openeo_job_id"],
            result_path=row["result_path"],
            result_summary=sqlite3.json_loads(row["result_summary"]) if row["result_summary"] else None,
            error_message=row["error_message"],
            retry_count=row["retry_count"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )
