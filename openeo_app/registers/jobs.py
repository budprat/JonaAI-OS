"""Extended JobsRegister with execution capabilities.

Includes:
- Thread-safe job management with locks
- Automatic cleanup of completed job threads
- Timeout protection for long-running jobs
"""

import contextvars
import io
import json
import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, wait as futures_wait
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from openeo_fastapi.api.models import JobsRequest
from openeo_fastapi.api.types import Error, Status
from openeo_fastapi.client.auth import Authenticator, User
from openeo_fastapi.client.jobs import Job, JobsRegister
from openeo_fastapi.client.psql.engine import get, modify

from openeo_app.core.job_notes import start_job_notes
from openeo_app.execution.executor import ProcessGraphExecutor
from openeo_app.storage.results import ResultStorage

logger = logging.getLogger(__name__)

# Jobs beyond this wait for a slot. Set OPENEO_MAX_CONCURRENT_JOBS to fit the host.
def _max_concurrent_jobs() -> int:
    """A value that doesn't parse keeps the default; zero or less still means one at a time."""
    try:
        return max(1, int(os.environ.get("OPENEO_MAX_CONCURRENT_JOBS", "4")))
    except ValueError:
        return 4


_JOB_SLOTS = threading.BoundedSemaphore(_max_concurrent_jobs())

# How long a synchronous job (POST /result) may run, and wait for a slot first. The same 300 s
# as ProcessGraphExecutor.execute's own timeout, which can't fire here: it uses SIGALRM, and
# FastAPI runs this route in a worker thread, where signals can't be set.
_SYNC_JOB_TIMEOUT_SECONDS = 300


def _service_memory_peak_mb() -> Optional[int]:
    """Highest memory this service has used, for judging the job size limit against reality."""
    try:
        return int(Path("/sys/fs/cgroup/memory.peak").read_text()) // (1024 * 1024)
    except (OSError, ValueError):
        return None


def _return_memory_to_system() -> None:
    """Hand a finished job's freed memory back. Otherwise the allocator keeps it for this
    process, and on a small host the next job starts near the service's memory cap."""
    import ctypes
    import gc

    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


class _JobCancelledError(Exception):
    """Internal exception raised when a job is cancelled via its cancellation event."""
    pass


# The moves a job's status may make. A finished, failed or cancelled job can be started
# again. Every status write goes through _move_job_status, which applies the move only if
# the job is still in a state that allows it, so two writers racing can't overwrite each
# other (for example a cancel arriving just as the job finishes). The same idea, for this
# app's own records, lives in openeo_ai/lifecycle.py.
JOB_MOVES = {
    "created": {"queued"},
    "queued": {"running", "canceled", "error"},
    "running": {"finished", "error", "canceled"},
    "finished": {"queued"},
    "error": {"queued"},
    "canceled": {"queued"},
}


def _job_sources(target: str) -> tuple:
    """Statuses a job may be in to move to target."""
    return tuple(sorted(state for state, targets in JOB_MOVES.items() if target in targets))


def _move_job_status(job_id, target: str) -> bool:
    """Set a job's status if its current status allows the move. False if it didn't."""
    from openeo_fastapi.client.psql.engine import get_engine
    from sqlalchemy import text

    sources = _job_sources(target)
    names = [f"s{i}" for i in range(len(sources))]
    statement = text(
        f"UPDATE jobs SET status = :target WHERE job_id = :job_id "
        f"AND status IN ({', '.join(':' + name for name in names)}) RETURNING job_id"
    )
    with get_engine().begin() as connection:
        row = connection.execute(
            statement, {"target": target, "job_id": str(job_id), **dict(zip(names, sources))}
        ).first()
    return row is not None


class ExecutableJobsRegister(JobsRegister):
    """
    Extended JobsRegister with actual execution capabilities.

    This register overrides the stub methods (501 responses) with
    real implementations using the ProcessGraphExecutor.
    """

    def __init__(
        self,
        settings,
        links,
        executor: ProcessGraphExecutor,
        storage: Optional[ResultStorage] = None,
    ):
        """
        Initialize the ExecutableJobsRegister.

        Args:
            settings: Application settings
            links: API links
            executor: ProcessGraphExecutor instance for running process graphs
            storage: ResultStorage instance for saving outputs
        """
        super().__init__(settings, links)
        self.executor = executor
        self.storage = storage or ResultStorage()

        # Thread-safe job tracking
        self._running_jobs = {}  # Track running async jobs
        self._cancel_events = {}  # Cancellation tokens per job
        self._jobs_lock = threading.Lock()  # Lock for _running_jobs and _cancel_events access

        # Job execution settings
        self._job_timeout_seconds = int(
            os.environ.get("JOB_TIMEOUT_SECONDS", "3600")  # 1 hour default
        )

        # Start background cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_completed_jobs_loop,
            daemon=True,
        )
        self._cleanup_thread.start()

        # Recover orphaned jobs on startup (zombie "running" jobs from crashed server)
        self._recover_orphaned_jobs()

        logger.info(f"ExecutableJobsRegister initialized (timeout={self._job_timeout_seconds}s)")

    def _recover_orphaned_jobs(self):
        """Fail jobs that were queued or running when the server stopped.

        Their threads died with the old process, so nothing will ever finish them, and
        start and delete both refuse a job that looks queued or running. This assumes one
        server process: a second one starting would fail the first one's live jobs.
        """
        try:
            from openeo_fastapi.client.psql.engine import get_engine
            from sqlalchemy.orm import sessionmaker
            from sqlalchemy import text

            db = sessionmaker(get_engine())
            with db.begin() as session:
                result = session.execute(
                    text("UPDATE jobs SET status = 'error' WHERE status IN ('queued', 'running') RETURNING job_id")
                )
                orphaned = result.fetchall()
                if orphaned:
                    logger.warning(
                        f"[STARTUP] Recovered {len(orphaned)} orphaned queued or running jobs → 'error': "
                        f"{[str(r[0]) for r in orphaned]}"
                    )
                else:
                    logger.info("[STARTUP] No orphaned jobs found")
        except Exception as e:
            logger.error(f"[STARTUP] Failed to recover orphaned jobs: {e}")

    def _require_owner(self, job_id: uuid.UUID, user: User) -> None:
        """Refuse access to another user's job. Missing jobs are left to the caller."""
        job = get(get_model=Job, primary_key=job_id)
        if job is not None and str(job.user_id) != str(user.user_id):
            raise HTTPException(
                status_code=403,
                detail=Error(code="Forbidden", message="Not authorized to access this job."),
            )

    def get_job(self, job_id: uuid.UUID, user: User = Depends(Authenticator.validate)):
        self._require_owner(job_id, user)
        return super().get_job(job_id, user)

    def update_job(
        self,
        job_id: uuid.UUID,
        body: JobsRequest,
        user: User = Depends(Authenticator.validate),
    ):
        self._require_owner(job_id, user)
        # The upstream update writes back the whole job it read, status included, so while
        # the job runs it would overwrite the status the job's thread sets. OpenEO locks
        # running jobs against changes anyway.
        job = get(get_model=Job, primary_key=job_id)
        if job is not None and job.status in (Status.queued, Status.running):
            raise HTTPException(
                status_code=400,
                detail=Error(code="JobLocked", message=f"Job is {job.status.value}; cancel it before changing it."),
            )
        return super().update_job(job_id, body, user)

    def estimate(self, job_id: uuid.UUID, user: User = Depends(Authenticator.validate)):
        self._require_owner(job_id, user)
        return super().estimate(job_id, user)

    def process_sync_job(
        self,
        body: JobsRequest = JobsRequest(),
        user: User = Depends(Authenticator.validate),
    ) -> Response:
        """
        Execute a synchronous job and return results immediately.

        POST /openeo/1.1.0/result

        Args:
            body: The job request containing the process graph
            user: The authenticated user

        Returns:
            Response with computation results
        """
        logger.info(f"Processing sync job for user {user.user_id}")

        try:
            # Extract process graph
            if body.process is None or body.process.process_graph is None:
                raise HTTPException(
                    status_code=400,
                    detail=Error(
                        code="ProcessGraphMissing",
                        message="No process graph provided.",
                    ),
                )

            process_graph = body.process.process_graph

            logger.debug(f"Executing process graph: {list(process_graph.keys())}")

            result = self._execute_sync_in_slot(process_graph)

            # Determine output format from save_result node
            output_format = self._get_output_format(process_graph)
            logger.info(f"Output format: {output_format}")

            # Return appropriate response
            return self._create_response(result, output_format)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Sync job execution failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=400,
                detail=Error(
                    code="ProcessingError",
                    message=str(e),
                ),
            )

    def _execute_sync_in_slot(self, process_graph: dict):
        """Run a synchronous job in one of the job slots, with a working timeout.

        The job waits for a slot like a batch job does, and is refused with 503 if none frees
        up within the timeout. It then runs in its own thread so the request can end at the
        timeout. A thread can't be stopped, so the slot is released only when the computation
        actually finishes, keeping memory bounded by the slot count.
        """
        timeout = _SYNC_JOB_TIMEOUT_SECONDS
        if not _JOB_SLOTS.acquire(timeout=timeout):
            raise HTTPException(
                status_code=503,
                detail=Error(code="ServiceUnavailable",
                             message=f"All job slots stayed busy for {timeout} s. Try again later."),
            )
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openeo-sync-job")
        try:
            future = pool.submit(self.executor.execute, process_graph=process_graph, parameters={})
        except BaseException:
            _JOB_SLOTS.release()
            raise
        future.add_done_callback(lambda _: _JOB_SLOTS.release())
        pool.shutdown(wait=False)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            raise TimeoutError(
                f"Process graph execution timed out after {timeout}s. "
                f"Try reducing spatial extent or temporal range."
            ) from None

    def start_job(
        self,
        job_id: uuid.UUID,
        user: User = Depends(Authenticator.validate),
    ) -> Response:
        """
        Start batch job processing.

        POST /openeo/1.1.0/jobs/{job_id}/results

        Args:
            job_id: The job identifier
            user: The authenticated user

        Returns:
            Response with status 202 (Accepted)
        """
        logger.info(f"Starting batch job {job_id} for user {user.user_id}")

        # Get job from database
        job = get(get_model=Job, primary_key=job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail=Error(
                    code="JobNotFound",
                    message=f"Job {job_id} not found.",
                ),
            )

        # Verify ownership
        if str(job.user_id) != str(user.user_id):
            raise HTTPException(
                status_code=403,
                detail=Error(
                    code="Forbidden",
                    message="Not authorized to access this job.",
                ),
            )

        # Queue it only if it isn't already queued or running. Checked and set in one
        # statement, so two start requests can't both launch a thread.
        if not _move_job_status(job_id, Status.queued.value):
            raise HTTPException(
                status_code=400,
                detail=Error(
                    code="JobAlreadyStarted",
                    message=f"Job is already {job.status.value}.",
                ),
            )

        # Log job start
        self.storage.save_log(
            job_id=job_id,
            level="info",
            message="Job queued for execution",
        )

        # Create cancellation token for this job
        cancel_event = threading.Event()

        # Start background execution in a thread (with lock)
        # IMPORTANT: Pass job_id, not job object - SQLAlchemy session issues
        # Register thread and cancel event BEFORE starting to prevent race
        # where cancel_job() is called before registration completes.
        thread = threading.Thread(
            target=self._execute_job_background,
            args=(job.job_id, cancel_event),
            daemon=True,
            name=f"job-{job.job_id}",
        )

        with self._jobs_lock:
            self._running_jobs[str(job.job_id)] = {
                "thread": thread,
                "started_at": datetime.now(),
            }
            self._cancel_events[str(job.job_id)] = cancel_event

        thread.start()

        return Response(
            status_code=202,
            headers={
                "OpenEO-Identifier": str(job_id),
            },
        )

    def _execute_job_background(self, job_id: uuid.UUID, cancel_event: threading.Event = None):
        """Run a job once a slot is free, so concurrent jobs can't exhaust memory.

        A job cancelled while waiting still runs through _run_job, which records the
        cancellation. Its timeout starts only when it actually begins.
        """
        if cancel_event is None:
            cancel_event = threading.Event()
        acquired = False
        while not acquired and not cancel_event.is_set():
            acquired = _JOB_SLOTS.acquire(timeout=1.0)
        try:
            self._run_job(job_id, cancel_event)
        finally:
            if acquired:
                _JOB_SLOTS.release()

    def _run_job(self, job_id: uuid.UUID, cancel_event: threading.Event = None):
        """
        Execute job in background with enhanced status tracking and timeout.

        Tracks:
        - started_at: When job execution began
        - finished_at: When job completed (success or error)
        - error_message: User-friendly error description on failure

        Features:
        - Timeout enforcement via ThreadPoolExecutor
        - Cancellation support via threading.Event
        - Dask garbage collection after execution

        Args:
            job_id: The job ID to execute (fetched fresh from DB)
            cancel_event: Threading event that signals cancellation when set
        """
        import sys
        import traceback
        from ..core.exceptions import classify_error
        import time
        import gc

        if cancel_event is None:
            cancel_event = threading.Event()

        start_time = time.time()
        logger.info(f"[THREAD] Starting async execution of job {job_id}")
        print(f"[THREAD] Starting job {job_id}", file=sys.stderr, flush=True)

        # Fetch job fresh from database (avoid SQLAlchemy detached session issues)
        job = get(get_model=Job, primary_key=job_id)
        if job is None:
            logger.error(f"Job {job_id} not found in database")
            return

        # The computation. A thread can't be stopped, so a job that times out or is cancelled
        # records its status at once and the finally below waits for this before returning,
        # keeping the caller's job slot until the work really ends.
        future = None
        try:
            # Check cancellation before starting
            if cancel_event.is_set():
                raise _JobCancelledError("Job cancelled before execution started")

            if not _move_job_status(job_id, Status.running.value):
                # Cancelled while it waited for a slot.
                raise _JobCancelledError("Job cancelled before execution started")

            self.storage.save_log(
                job_id=job_id,
                level="info",
                message="Job execution started",
                data={
                    "started_at": datetime.now().isoformat(),
                    "timeout_seconds": self._job_timeout_seconds,
                },
            )

            # Execute process graph with timeout enforcement
            process_graph = job.process.process_graph

            load_notes = start_job_notes()

            # Use ThreadPoolExecutor to enforce timeout. Not a `with` block: its exit waits for
            # the computation, which held a timed-out job at 'running' until the work ended.
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(
                    contextvars.copy_context().run,
                    self.executor.execute,
                    process_graph=process_graph,
                    parameters={},
                )
                # Poll for completion, checking cancellation periodically
                poll_interval = 1.0  # Check cancellation every second
                elapsed = 0.0
                while True:
                    if cancel_event.is_set():
                        future.cancel()
                        raise _JobCancelledError("Job cancelled by user")
                    try:
                        result = future.result(timeout=poll_interval)
                        break  # Got result
                    except FuturesTimeoutError:
                        elapsed += poll_interval
                        if elapsed >= self._job_timeout_seconds:
                            future.cancel()
                            raise TimeoutError(
                                f"Job execution exceeded timeout of {self._job_timeout_seconds} seconds"
                            )
            finally:
                pool.shutdown(wait=False)

            # Check cancellation after execution
            if cancel_event.is_set():
                raise _JobCancelledError("Job cancelled after execution")

            # Determine output format
            output_format = self._get_output_format(process_graph)

            # Save results
            result_path = self.storage.save_result(
                job_id=job_id,
                data=result,
                format=output_format,
                notes=load_notes,
            )

            # Calculate duration
            duration_seconds = time.time() - start_time

            self.storage.save_log(
                job_id=job_id,
                level="info",
                message=f"Results saved to {result_path}",
                data={"result_path": str(result_path)},
            )

            if not _move_job_status(job_id, Status.finished.value):
                # A cancel landed while the results were being saved; the cancel wins.
                self.storage.delete_result(job_id)
                raise _JobCancelledError("Job cancelled as it finished")

            self.storage.save_log(
                job_id=job_id,
                level="info",
                message="Job completed successfully",
                data={
                    "finished_at": datetime.now().isoformat(),
                    "duration_seconds": round(duration_seconds, 2),
                    "service_memory_peak_mb": _service_memory_peak_mb(),
                },
            )

            logger.info(f"Job {job_id} completed successfully in {duration_seconds:.1f}s")

            # Clean up result from memory
            del result
            gc.collect()

        except _JobCancelledError as e:
            duration_seconds = time.time() - start_time
            logger.info(f"Job {job_id} cancelled after {duration_seconds:.1f}s")

            _move_job_status(job_id, Status.canceled.value)

            self.storage.save_log(
                job_id=job_id,
                level="info",
                message=str(e),
                data={
                    "finished_at": datetime.now().isoformat(),
                    "duration_seconds": round(duration_seconds, 2),
                    "error_type": "cancelled",
                },
            )

            self.storage.delete_result(job_id)
            gc.collect()

        except TimeoutError as e:
            duration_seconds = time.time() - start_time
            logger.error(f"Job {job_id} timed out after {duration_seconds:.1f}s")

            _move_job_status(job_id, Status.error.value)

            self.storage.save_log(
                job_id=job_id,
                level="error",
                message=f"Job timed out after {self._job_timeout_seconds} seconds",
                data={
                    "finished_at": datetime.now().isoformat(),
                    "duration_seconds": round(duration_seconds, 2),
                    "error_type": "timeout",
                },
            )

            gc.collect()

        except Exception as e:
            duration_seconds = time.time() - start_time
            logger.error(
                f"[JOB FAILED] Job {job_id} failed after {duration_seconds:.1f}s: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            # Print to stderr for immediate visibility (grep-friendly tag)
            print(
                f"[JOB FAILED] {job_id} | {type(e).__name__}: {e} | {duration_seconds:.1f}s",
                file=sys.stderr,
            )

            # Classify error for user-friendly message
            error_info = classify_error(e)

            # Update status to error with completion timestamp
            _move_job_status(job_id, Status.error.value)

            # Log error with classification
            self.storage.save_log(
                job_id=job_id,
                level="error",
                message=f"Job failed: {error_info['user_message']}",
                data={
                    "finished_at": datetime.now().isoformat(),
                    "duration_seconds": round(duration_seconds, 2),
                    "error_type": error_info["error_type"],
                    "technical_message": error_info["technical_message"],
                    "details": error_info["details"],
                },
            )

            gc.collect()

        finally:
            # Clean up cancellation event
            with self._jobs_lock:
                self._cancel_events.pop(str(job_id), None)
            if future is not None and not future.done():
                logger.info(f"Job {job_id} ended; holding its slot until the computation stops")
                futures_wait([future])
            _return_memory_to_system()

    def _cleanup_completed_jobs_loop(self):
        """
        Background loop to clean up completed job threads.

        Runs every 60 seconds to remove finished threads from _running_jobs,
        preventing memory leaks from accumulated thread references.
        """
        cleanup_interval = 60  # seconds
        while True:
            time.sleep(cleanup_interval)
            self._cleanup_completed_jobs()

    def _cleanup_completed_jobs(self):
        """Remove completed threads from _running_jobs and stale cancel events."""
        with self._jobs_lock:
            completed = []
            for job_id, job_info in self._running_jobs.items():
                thread = job_info["thread"]
                if not thread.is_alive():
                    completed.append(job_id)

            for job_id in completed:
                del self._running_jobs[job_id]
                self._cancel_events.pop(job_id, None)

            if completed:
                logger.debug(f"Cleaned up {len(completed)} completed job threads")

    def get_running_jobs_count(self) -> int:
        """Get count of currently running jobs (for monitoring)."""
        with self._jobs_lock:
            return sum(
                1 for job_info in self._running_jobs.values()
                if job_info["thread"].is_alive()
            )

    def get_results(
        self,
        job_id: uuid.UUID,
        user: User = Depends(Authenticator.validate),
    ) -> Response:
        """
        Get results of a completed job.

        GET /openeo/1.1.0/jobs/{job_id}/results

        Args:
            job_id: The job identifier
            user: The authenticated user

        Returns:
            Response with job results
        """
        logger.info(f"Getting results for job {job_id}")

        # Get job
        job = get(get_model=Job, primary_key=job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail=Error(
                    code="JobNotFound",
                    message=f"Job {job_id} not found.",
                ),
            )

        # Verify ownership
        if str(job.user_id) != str(user.user_id):
            raise HTTPException(
                status_code=403,
                detail=Error(
                    code="Forbidden",
                    message="Not authorized to access this job.",
                ),
            )

        # Check status
        if job.status != Status.finished:
            if job.status == Status.error:
                raise HTTPException(
                    status_code=424,
                    detail=Error(
                        code="JobFailed",
                        message="Job execution failed. Check logs for details.",
                    ),
                )
            elif job.status in [Status.running, Status.queued]:
                raise HTTPException(
                    status_code=400,
                    detail=Error(
                        code="JobNotFinished",
                        message=f"Job is still {job.status}.",
                    ),
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=Error(
                        code="JobNotStarted",
                        message="Job has not been started.",
                    ),
                )

        # Get result file
        result_path = self.storage.get_result(job_id)

        if result_path is None or not result_path.exists():
            raise HTTPException(
                status_code=404,
                detail=Error(
                    code="ResultNotFound",
                    message="Result file not found.",
                ),
            )

        return FileResponse(
            path=str(result_path),
            filename=result_path.name,
            media_type=self._get_media_type(result_path),
        )

    def logs(
        self,
        job_id: uuid.UUID,
        user: User = Depends(Authenticator.validate),
    ):
        """
        Get job execution logs.

        GET /openeo/1.1.0/jobs/{job_id}/logs

        Args:
            job_id: The job identifier
            user: The authenticated user

        Returns:
            Dict with logs and links
        """
        logger.info(f"Getting logs for job {job_id}")

        # Get job
        job = get(get_model=Job, primary_key=job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail=Error(
                    code="JobNotFound",
                    message=f"Job {job_id} not found.",
                ),
            )

        # Verify ownership
        if str(job.user_id) != str(user.user_id):
            raise HTTPException(
                status_code=403,
                detail=Error(
                    code="Forbidden",
                    message="Not authorized to access this job.",
                ),
            )

        # Get logs from storage
        log_entries = self.storage.get_logs(job_id)

        # Format logs for OpenEO response
        logs = []
        for entry in log_entries:
            logs.append({
                "id": str(uuid.uuid4()),
                "code": entry.get("level", "info").upper(),
                "level": entry.get("level", "info"),
                "message": entry.get("message", ""),
                "time": entry.get("timestamp"),
                "data": entry.get("data"),
            })

        return {"logs": logs, "links": []}

    def cancel_job(
        self,
        job_id: uuid.UUID,
        user: User = Depends(Authenticator.validate),
    ) -> Response:
        """
        Cancel a running job.

        DELETE /openeo/1.1.0/jobs/{job_id}/results

        Args:
            job_id: The job identifier
            user: The authenticated user

        Returns:
            Response with status 204 (No Content)
        """
        logger.info(f"Cancelling job {job_id}")

        # Get job
        job = get(get_model=Job, primary_key=job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail=Error(
                    code="JobNotFound",
                    message=f"Job {job_id} not found.",
                ),
            )

        # Verify ownership
        if str(job.user_id) != str(user.user_id):
            raise HTTPException(
                status_code=403,
                detail=Error(
                    code="Forbidden",
                    message="Not authorized to access this job.",
                ),
            )

        # Check if running
        if job.status not in [Status.running, Status.queued]:
            raise HTTPException(
                status_code=400,
                detail=Error(
                    code="JobNotRunning",
                    message=f"Job is not running (status: {job.status}).",
                ),
            )

        # Signal cancellation to the running thread
        with self._jobs_lock:
            cancel_event = self._cancel_events.get(str(job_id))

        if cancel_event is not None:
            cancel_event.set()
            logger.info(f"Cancellation signal sent to job {job_id}")
        elif _move_job_status(job_id, Status.canceled.value):
            # No thread holds it, for example after a restart; cancel it here.
            self.storage.delete_result(job_id)
        else:
            # It finished between the check above and now, so there's nothing to cancel,
            # and its results stay.
            raise HTTPException(
                status_code=400,
                detail=Error(code="JobNotRunning", message="Job finished before it could be cancelled."),
            )

        self.storage.save_log(
            job_id=job_id,
            level="info",
            message="Job cancelled by user",
        )

        logger.info(f"Cancel requested for job {job_id}")

        return Response(status_code=204)

    def delete_job(
        self,
        job_id: uuid.UUID,
        user: User = Depends(Authenticator.validate),
    ) -> Response:
        """
        Delete a job and its results.

        DELETE /openeo/1.1.0/jobs/{job_id}

        Args:
            job_id: The job identifier
            user: The authenticated user

        Returns:
            Response with status 204 (No Content)
        """
        logger.info(f"Deleting job {job_id}")

        # Get job
        job = get(get_model=Job, primary_key=job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail=Error(
                    code="JobNotFound",
                    message=f"Job {job_id} not found.",
                ),
            )

        # Verify ownership
        if str(job.user_id) != str(user.user_id):
            raise HTTPException(
                status_code=403,
                detail=Error(
                    code="Forbidden",
                    message="Not authorized to access this job.",
                ),
            )

        # Cannot delete running jobs
        if job.status in [Status.running, Status.queued]:
            raise HTTPException(
                status_code=400,
                detail=Error(
                    code="JobLocked",
                    message="Cannot delete a running job. Cancel it first.",
                ),
            )

        # Delete results and logs
        self.storage.delete_result(job_id)
        self.storage.clear_logs(job_id)

        # Actually delete the job from the database
        try:
            from openeo_fastapi.client.psql.engine import delete as db_delete
            db_delete(delete_model=Job, primary_key=job_id)
            logger.info(f"Job {job_id} deleted from database")
        except Exception as e:
            # Fallback: mark as canceled if delete fails
            logger.warning(f"DB delete failed for job {job_id}, marking canceled: {e}")
            job.status = Status.canceled
            modify(modify_object=job)

        return Response(status_code=204)

    # Helper methods

    def _get_output_format(self, process_graph: dict) -> str:
        """
        Extract output format from save_result node.

        Args:
            process_graph: The process graph dict

        Returns:
            Output format string (default: GTiff)
        """
        for node_id, node in process_graph.items():
            if isinstance(node, dict):
                if node.get("process_id") == "save_result":
                    args = node.get("arguments", {})
                    return args.get("format", "GTiff")

        return "GTiff"

    def _create_response(self, data, output_format: str) -> Response:
        """
        Create appropriate HTTP response for data.

        Args:
            data: The computation result
            output_format: The output format

        Returns:
            FastAPI Response object
        """
        if output_format in ["GTiff", "GeoTiff"]:
            return self._return_geotiff(data)
        elif output_format == "netCDF":
            return self._return_netcdf(data)
        elif output_format == "JSON":
            return self._return_json(data)
        elif output_format == "PNG":
            return self._return_png(data)
        else:
            # Default to GeoTIFF
            return self._return_geotiff(data)

    def _return_geotiff(self, data) -> Response:
        """Return data as GeoTIFF.

        Handles multi-dimensional arrays by:
        1. Selecting first time slice if time dimension exists
        2. Squeezing singleton dimensions
        3. Ensuring proper 2D or 3D (bands, y, x) shape for rasterio
        """
        import rioxarray  # noqa: F401
        import numpy as np

        if hasattr(data, "rio"):
            buffer = io.BytesIO()

            # Handle time dimension — GeoTIFF only supports 2D/3D (bands, y, x)
            if "time" in data.dims or "t" in data.dims:
                time_dim = "time" if "time" in data.dims else "t"
                n_times = data[time_dim].size
                if n_times > 1:
                    logger.warning(
                        f"GeoTIFF does not support time dimension: {n_times} time steps found, "
                        f"returning FIRST time slice only. Use 'netCDF' format to preserve all time steps, "
                        f"or add reduce_dimension(dimension='time', reducer=median) to your process graph."
                    )
                data = data.isel({time_dim: 0})

            # Handle 4D+ arrays by squeezing singleton dimensions
            if data.ndim > 3:
                data = data.squeeze()

            # If still >3D, select first element of extra dimensions
            while data.ndim > 3:
                for dim in data.dims:
                    if dim not in ("x", "y", "latitude", "longitude", "bands"):
                        logger.warning(f"Reducing dimension '{dim}' to first element")
                        data = data.isel({dim: 0})
                        break
                else:
                    first_dim = data.dims[0]
                    data = data.isel({first_dim: 0})

            # Ensure CRS
            if data.rio.crs is None:
                data = data.rio.write_crs("EPSG:4326")

            # Validate data
            if data.size == 0:
                raise HTTPException(
                    status_code=500,
                    detail=Error(
                        code="EmptyResult",
                        message="Result data is empty.",
                    ),
                )

            data.rio.to_raster(buffer, driver="GTiff")
            buffer.seek(0)

            return StreamingResponse(
                buffer,
                media_type="image/tiff",
                headers={
                    "Content-Disposition": "attachment; filename=result.tif"
                },
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=Error(
                    code="FormatError",
                    message="Cannot convert result to GeoTIFF.",
                ),
            )

    def _return_netcdf(self, data) -> Response:
        """Return data as NetCDF."""
        import xarray as xr

        buffer = io.BytesIO()

        if isinstance(data, xr.DataArray):
            data = data.to_dataset(name="data")

        data.to_netcdf(buffer)
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="application/x-netcdf",
            headers={
                "Content-Disposition": "attachment; filename=result.nc"
            },
        )

    def _return_json(self, data) -> Response:
        """Return data as JSON."""
        import xarray as xr

        if isinstance(data, (xr.DataArray, xr.Dataset)):
            result = data.to_dict()
        elif hasattr(data, "tolist"):
            result = data.tolist()
        elif isinstance(data, (dict, list)):
            result = data
        else:
            result = str(data)

        return Response(
            content=json.dumps(result, default=str),
            media_type="application/json",
        )

    def _return_png(self, data) -> Response:
        """Return data as PNG image."""
        import numpy as np
        from PIL import Image

        buffer = io.BytesIO()

        # Get numpy array
        if hasattr(data, "values"):
            arr = data.values
        else:
            arr = np.array(data)

        # Handle time dimension
        if arr.ndim > 3:
            arr = arr[0]  # Take first time slice

        # Normalize to 0-255.
        # For RGB stacks, stretch each channel independently using robust percentiles
        # so one bright cloud band does not flatten the whole image into gray mush.
        def _stretch_channel(channel):
            finite = np.isfinite(channel)
            if not finite.any():
                return np.zeros_like(channel, dtype=np.uint8)
            low, high = np.nanpercentile(channel[finite], [2, 98])
            if high <= low:
                low = np.nanmin(channel[finite])
                high = np.nanmax(channel[finite])
            if high <= low:
                return np.zeros_like(channel, dtype=np.uint8)
            scaled = np.clip((channel - low) / (high - low), 0.0, 1.0)
            scaled = np.nan_to_num(scaled, nan=0.0, posinf=1.0, neginf=0.0)
            return (scaled * 255).astype(np.uint8)

        # Create image
        if arr.ndim == 2:
            arr = _stretch_channel(arr)
            img = Image.fromarray(arr, mode="L")
        elif arr.ndim == 3:
            if arr.shape[0] in [1, 3, 4]:
                arr = np.moveaxis(arr, 0, -1)
            if arr.shape[-1] == 1:
                arr = _stretch_channel(arr[:, :, 0])
                img = Image.fromarray(arr, mode="L")
            elif arr.shape[-1] in (3, 4):
                channels = [_stretch_channel(arr[:, :, idx]) for idx in range(arr.shape[-1])]
                arr = np.stack(channels, axis=-1)
                img = Image.fromarray(arr, mode="RGB" if arr.shape[-1] == 3 else "RGBA")
            else:
                arr = _stretch_channel(arr[:, :, 0])
                img = Image.fromarray(arr, mode="L")
        else:
            raise HTTPException(
                status_code=500,
                detail=Error(
                    code="FormatError",
                    message=f"Cannot convert {arr.ndim}D array to PNG.",
                ),
            )

        img.save(buffer, format="PNG")
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="image/png",
            headers={
                "Content-Disposition": "attachment; filename=result.png"
            },
        )

    def _get_media_type(self, path: Path) -> str:
        """Get media type from file extension."""
        suffix = path.suffix.lower()
        return {
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".nc": "application/x-netcdf",
            ".json": "application/json",
            ".png": "image/png",
        }.get(suffix, "application/octet-stream")
