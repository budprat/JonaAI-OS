# ABOUTME: Schedule management and cron-like execution for recurring batch analyses.
# Handles schedule CRUD, next-run calculation, and the background polling loop.

"""
Batch Scheduler

Manages recurring analysis schedules:
- CRUD for schedule configurations
- Next-run-at calculation based on frequency
- Background polling loop that fires due schedules
- Pause/resume/cancel lifecycle
"""

import uuid
import json
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple, Callable, Awaitable

from openeo_ai import _pg_compat as sqlite3  # PostgreSQL shim with sqlite3 API

from ..lifecycle import SCHEDULE, IllegalTransition
from .models import (
    ScheduleCreate,
    ScheduleResponse,
    ScheduleFrequency,
    ScheduleStatus,
    BatchJobCreate,
    LocationInput,
    TriggerType,
    init_batch_db,
)

logger = logging.getLogger(__name__)


# ── Frequency to timedelta mapping ──

_FREQUENCY_DELTAS = {
    ScheduleFrequency.DAILY: timedelta(days=1),
    ScheduleFrequency.WEEKLY: timedelta(weeks=1),
    ScheduleFrequency.BIWEEKLY: timedelta(weeks=2),
    ScheduleFrequency.MONTHLY: timedelta(days=30),
    ScheduleFrequency.QUARTERLY: timedelta(days=90),
}


def _compute_next_run(
    frequency: ScheduleFrequency,
    last_run: Optional[datetime] = None,
) -> Optional[datetime]:
    """Compute the next run time based on frequency.

    For event-driven or manual frequencies, returns None (externally triggered).
    """
    if frequency == ScheduleFrequency.MANUAL:
        return None

    if frequency == ScheduleFrequency.ON_SATELLITE_PASS:
        # First practical implementation: poll acquisition-driven schedules on
        # a short cadence. The callback can inspect catalog availability and
        # decide whether to create work; true orbital prediction can replace
        # this without changing schedule persistence.
        interval = int(os.environ.get("SATELLITE_PASS_POLL_MINUTES", "360"))
        return (last_run or datetime.utcnow()) + timedelta(minutes=interval)

    base = last_run or datetime.utcnow()
    delta = _FREQUENCY_DELTAS.get(frequency)
    if delta:
        return base + delta
    return None


class ScheduleStore:
    """SQLite-backed CRUD for schedules."""

    def __init__(self, db_path: str = "data/openeo_ai.db"):
        self.db_path = db_path
        init_batch_db(db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def create_schedule(
        self,
        data: ScheduleCreate,
        user_id: str,
    ) -> ScheduleResponse:
        """Create a new recurring schedule."""
        schedule_id = str(uuid.uuid4())
        now = datetime.utcnow()
        now_iso = now.isoformat()

        # Compute first run time
        starts_at = data.starts_at or now_iso
        frequency = data.frequency
        next_run = _compute_next_run(frequency)
        next_run_iso = next_run.isoformat() if next_run else None

        # If starts_at is in the future, use that as next_run
        if data.starts_at:
            try:
                parsed_start = datetime.fromisoformat(data.starts_at)
                if parsed_start > now:
                    next_run_iso = data.starts_at
            except ValueError:
                pass

        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO schedules
                    (id, template_id, name, frequency, status, locations,
                     parameter_overrides, project_id, user_id,
                     max_runs, starts_at, ends_at, next_run_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule_id,
                    data.template_id,
                    data.name or f"Schedule {now_iso[:10]}",
                    frequency.value,
                    ScheduleStatus.ACTIVE.value,
                    json.dumps([loc.model_dump() for loc in data.locations]),
                    json.dumps(data.parameter_overrides),
                    data.project_id,
                    user_id,
                    data.max_runs,
                    starts_at,
                    data.ends_at,
                    next_run_iso,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
            return self.get_schedule(schedule_id)
        finally:
            conn.close()

    def get_schedule(self, schedule_id: str) -> Optional[ScheduleResponse]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_response(row)
        finally:
            conn.close()

    def list_schedules(
        self,
        user_id: str,
        *,
        status: Optional[ScheduleStatus] = None,
        limit: int = 50,
    ) -> List[ScheduleResponse]:
        conn = self._conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM schedules WHERE user_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, status.value, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schedules WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            return [self._row_to_response(r) for r in rows]
        finally:
            conn.close()

    def update_schedule_status(
        self, schedule_id: str, user_id: str, new_status: ScheduleStatus
    ) -> bool:
        """Pause, resume, or cancel a schedule."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT status FROM schedules WHERE id = ? AND user_id = ?", (schedule_id, user_id)
            ).fetchone()
            if not row:
                return False
            SCHEDULE.check(row["status"], new_status.value)
            now = datetime.utcnow().isoformat()
            sources = SCHEDULE.sources(new_status.value)
            cursor = conn.execute(
                "UPDATE schedules SET status = ?, updated_at = ? WHERE id = ? AND user_id = ? "
                f"AND status IN ({', '.join('?' for _ in sources)})",
                (new_status.value, now, schedule_id, user_id, *sources),
            )
            conn.commit()
            if cursor.rowcount == 0:
                # It moved in between, for example it expired.
                current = conn.execute("SELECT status FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
                raise IllegalTransition(SCHEDULE, current["status"], new_status.value)
            return True
        finally:
            conn.close()

    def get_due_schedules(self) -> List[Dict[str, Any]]:
        """Find all active schedules whose next_run_at <= now.

        Returns raw dicts with all fields needed to create batch jobs.
        """
        conn = self._conn()
        try:
            now = datetime.utcnow().isoformat()
            rows = conn.execute(
                """
                SELECT * FROM schedules
                WHERE status = ? AND next_run_at IS NOT NULL AND next_run_at <= ?
                """,
                (ScheduleStatus.ACTIVE.value, now),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def record_run(self, schedule_id: str, batch_job_id: str) -> None:
        """Record that a schedule has been executed.

        Updates runs_completed, last_run_at, last_batch_job_id, and next_run_at.
        Auto-expires if max_runs reached or ends_at passed.
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
            if not row:
                return

            now = datetime.utcnow()
            now_iso = now.isoformat()
            runs = (row["runs_completed"] or 0) + 1

            # A run keeps the schedule's status (a manual run of a paused one leaves it
            # paused), unless this run used it up.
            new_status = row["status"]
            used_up = bool(row["max_runs"] and runs >= row["max_runs"])
            if row["ends_at"]:
                try:
                    used_up = used_up or datetime.fromisoformat(row["ends_at"]) <= now
                except ValueError:
                    pass
            if used_up and SCHEDULE.can(new_status, ScheduleStatus.EXPIRED.value):
                new_status = ScheduleStatus.EXPIRED.value

            # Compute next run
            freq = ScheduleFrequency(row["frequency"])
            next_run = _compute_next_run(freq, now)
            next_run_iso = next_run.isoformat() if next_run else None

            # If expired, clear next_run
            if new_status == ScheduleStatus.EXPIRED.value:
                next_run_iso = None

            conn.execute(
                """
                UPDATE schedules
                SET runs_completed = ?, last_run_at = ?, last_batch_job_id = ?,
                    next_run_at = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (runs, now_iso, batch_job_id, next_run_iso, new_status, now_iso, schedule_id),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_schedule(self, schedule_id: str, user_id: str) -> bool:
        conn = self._conn()
        try:
            cursor = conn.execute(
                "DELETE FROM schedules WHERE id = ? AND user_id = ?",
                (schedule_id, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def _row_to_response(self, row: sqlite3.Row) -> ScheduleResponse:
        locations = sqlite3.json_loads(row["locations"], default=[])
        return ScheduleResponse(
            id=row["id"],
            user_id=row["user_id"],
            template_id=row["template_id"],
            name=row["name"],
            frequency=ScheduleFrequency(row["frequency"]),
            status=ScheduleStatus(row["status"]),
            locations_count=len(locations),
            runs_completed=row["runs_completed"] or 0,
            max_runs=row["max_runs"],
            next_run_at=row["next_run_at"],
            last_run_at=row["last_run_at"],
            created_at=row["created_at"],
        )


class SchedulerLoop:
    """Background asyncio task that polls for due schedules and fires batch jobs.

    Designed to run inside the FastAPI event loop. Checks every 60 seconds.
    """

    def __init__(
        self,
        schedule_store: ScheduleStore,
        on_schedule_due: Callable[[Dict[str, Any]], Awaitable[str]],
        poll_interval_seconds: int = 60,
    ):
        """
        Args:
            schedule_store: Store for reading due schedules.
            on_schedule_due: Async callback that receives a schedule row dict
                and returns the created batch_job_id.
            poll_interval_seconds: How often to check for due schedules.
        """
        self.store = schedule_store
        self.on_schedule_due = on_schedule_due
        self.poll_interval = poll_interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the scheduler polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler loop started (poll interval: %ds)", self.poll_interval)

    async def stop(self) -> None:
        """Stop the scheduler polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler loop stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                # The stores make blocking DB calls; keep them off the event loop.
                due_schedules = await asyncio.to_thread(self.store.get_due_schedules)
                for sched in due_schedules:
                    try:
                        batch_job_id = await self.on_schedule_due(sched)
                        await asyncio.to_thread(self.store.record_run, sched["id"], batch_job_id)
                        logger.info(
                            "Schedule %s fired -> batch job %s",
                            sched["id"],
                            batch_job_id,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to fire schedule %s", sched["id"]
                        )
            except Exception:
                logger.exception("Scheduler loop error")

            await asyncio.sleep(self.poll_interval)
