# ABOUTME: Enhanced web interface for OpenEO AI Assistant with researcher-focused features.
# Includes workflow panels, suggestion chips, export capabilities, and quality metrics.

"""
OpenEO AI Enhanced Web Interface

Production-ready chat interface with:
- Natural language workflow generation
- Process graph visualization
- Quality metrics dashboard
- Export capabilities (notebooks, process graphs)
- Workflow history and session management
- Sustainability metrics
"""

import asyncio
import logging
from collections import deque
import json
import re
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse
import time

from dotenv import load_dotenv
load_dotenv()  # Load .env before any os.environ.get() calls

# Crash telemetry first, before the heavy imports, so a native crash anywhere after this
# leaves a traceback. At import, not under __main__, so it runs under uvicorn as well.
from .crash_telemetry import enable as _enable_crash_telemetry
_enable_crash_telemetry()
from datetime import datetime
from typing import Optional, Dict, Any, List

import hashlib
import secrets

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request, Depends, Cookie
from fastapi.responses import HTMLResponse, Response, JSONResponse, FileResponse
from openeo_ai.lifecycle import IllegalTransition
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .access_guard import AccessGuard
from .sdk.client import OpenEOAIClient, OpenEOAIConfig, TOOL_DEFINITIONS
from .html_template import HTML_TEMPLATE
from .supabase_auth import verify_supabase_token  # primary JWT verification
from .web_routes import router as routes_router
from .batch.routes import router as batch_router
from .api.profiles import router as profiles_router  # Phase 10
from .api.sharing import router as sharing_router  # Phase 11
from .api.community import router as community_router  # Phase 12
from .api.usage import router as usage_router  # Phase 14
from .api.observability import router as observability_router  # Phase 16
from .intelligence.routes import router as intelligence_router  # Phase 17
from .intelligence.api_v1 import router as intelligence_api_v1_router  # Phase 17 public controls


app = FastAPI(title="OpenEO AI Assistant", version="0.2.0")


@app.exception_handler(IllegalTransition)
async def _illegal_transition(_request: Request, exc: IllegalTransition):
    """A status move its lifecycle doesn't allow: the record is in the wrong state for it."""
    return JSONResponse(status_code=409, content={
        "detail": str(exc),
        "current": exc.current,
        "requested": exc.target,
        "allowed": exc.allowed,
    })

# Add rate limiting middleware: 120 requests/minute per IP
# (Each page load triggers 5-10 requests: auth, projects, saved-jobs, render-raster, etc.)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openeo_app.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware, max_requests=120, window_seconds=60)

# Security headers middleware
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Content-Security-Policy
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' ws: wss: https://*.supabase.co "
            "https://*.tile.openstreetmap.org https://nominatim.openstreetmap.org; "
            "frame-ancestors 'none'"
        )
        # HSTS: check X-Forwarded-Proto for reverse proxy setups
        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
        )
        if is_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Startup security checks
import logging as _startup_logging
_startup_logger = _startup_logging.getLogger(__name__)
if not os.environ.get("POSTGRES_PASSWORD") and os.environ.get("OPENEO_DEV_MODE", "").lower() != "true":
    _startup_logger.critical(
        "POSTGRES_PASSWORD is empty in non-dev mode. "
        "Set a strong password for production deployments."
    )

# Add CORS middleware — configure via CORS_ORIGINS env var (comma-separated)
# Default to localhost origins instead of wildcard for security
_cors_origins_raw = os.environ.get("CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    if _cors_origins_raw
    else ["http://localhost:3000", "http://localhost:8080"]
)
# Never combine allow_credentials=True with wildcard origins
_cors_credentials = "*" not in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Requested-With"],
)

# Track server start time for uptime calculation
_SERVER_START_TIME = datetime.utcnow()

# Prometheus-style counters (thread-safe via GIL for single-process uvicorn)
_metrics: Dict[str, int] = {
    "ws_queries_total": 0,
    "ws_queries_errors": 0,
    "ws_queries_stopped": 0,
    "tool_calls_total": 0,
    "tool_calls_errors": 0,
    "ws_connections_total": 0,
    "ws_disconnections_total": 0,
}

# WebSocket per-session rate limiting to prevent cost amplification attacks.
# Maps session_id -> list of message timestamps (last 60 seconds).
_ws_rate: Dict[str, list] = {}
_WS_MAX_MSGS_PER_MIN = 10

# Auth and HTTP routes are in separate modules
app.include_router(routes_router)

# Batch analysis API (templates, jobs, schedules, results)
app.include_router(batch_router)

# REST API v1 for programmatic access (enterprise/insurance integrations)
from .api.router import router as api_v1_router
app.include_router(api_v1_router)

# Phase 10-14 + 16: SaaS feature + observability routers
app.include_router(profiles_router)
app.include_router(sharing_router)
app.include_router(community_router)
app.include_router(usage_router)
app.include_router(observability_router)
app.include_router(intelligence_router)
app.include_router(intelligence_api_v1_router)


@app.on_event("startup")
async def _startup_init_langfuse():
    """Initialize Langfuse OTel tracing for Claude Agent SDK (Phase 16)."""
    try:
        from .langfuse_setup import init_langfuse
        if init_langfuse():
            print("[startup] Langfuse observability enabled")
        else:
            print("[startup] Langfuse observability disabled (no credentials)")
    except Exception as e:
        print(f"[startup] Langfuse init skipped: {e}")


@app.on_event("startup")
async def _startup_discover_ea_tools():
    """Discover Earth-Agent direct tools asynchronously at startup.

    asyncio.run() fails inside uvicorn's event loop, so direct ea_* tools
    can't be discovered during synchronous module import. This startup hook
    runs inside the event loop and populates the registry cache.
    """
    try:
        from .earth_agent.tool_registry import load_earth_agent_registry_async
        defs, index = await load_earth_agent_registry_async()
        if defs:
            print(f"[startup] Earth-Agent direct tools: {len(defs)} discovered")
        else:
            print("[startup] Earth-Agent direct tools: none (EA servers not running)")
    except Exception as e:
        print(f"[startup] Earth-Agent tool discovery skipped: {e}")


def _worker_enabled() -> bool:
    # Opt-out gate (flipped on-by-default 2026-07-10, NU decision): the worker
    # runs unless EARTH_INTELLIGENCE_WORKER is explicitly 0/false/no/off.
    # monitor_tools._worker_active mirrors this — keep them identical.
    return os.environ.get("EARTH_INTELLIGENCE_WORKER", "").lower() not in {"0", "false", "no", "off"}


@app.on_event("startup")
async def _startup_earth_intelligence_worker():
    """Start the persistent Earth Intelligence observation worker (on by default)."""
    if not _worker_enabled():
        print("[startup] Earth Intelligence worker disabled (EARTH_INTELLIGENCE_WORKER=false)")
        return
    try:
        from .intelligence.worker import EarthIntelligenceWorker

        worker = EarthIntelligenceWorker.from_env()
        app.state.earth_intelligence_worker = worker

        # Run the worker on a DEDICATED thread with its own event loop. The worker's
        # tick path makes synchronous SQLAlchemy calls; on the HTTP event loop a stalled
        # DB connection would freeze every request (the hang this replaces). The worker
        # holds no loop-bound state, so an isolated loop is safe. The manual /worker/tick
        # endpoint still runs tick_for_user on the main loop (rare; socket-bounded by the
        # engine keepalives).
        import threading

        def _run_worker_thread() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            app.state.earth_intelligence_worker_loop = loop
            try:
                loop.run_until_complete(worker.run_forever())
            except Exception as exc:  # noqa: BLE001
                print(f"[worker-thread] Earth Intelligence worker exited: {exc}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        thread = threading.Thread(
            target=_run_worker_thread, name="earth-intelligence-worker", daemon=True
        )
        app.state.earth_intelligence_worker_thread = thread
        thread.start()
        print(f"[startup] Earth Intelligence worker enabled (dedicated thread): {worker.worker_id}")
    except Exception as e:
        print(f"[startup] Earth Intelligence worker skipped: {e}")


def _scheduler_enabled() -> bool:
    # On by default, like the worker: schedules are stored, so they should fire.
    return os.environ.get("JONA_SCHEDULER", "").lower() not in {"0", "false", "no", "off"}


@app.on_event("startup")
async def _startup_scheduler():
    """Start the loop that fires due batch schedules (on unless JONA_SCHEDULER is off)."""
    if not _scheduler_enabled():
        print("[startup] Batch scheduler disabled (JONA_SCHEDULER=false)")
        return
    try:
        from .batch.routes import _get_schedule_store, fire_due_schedule
        from .batch.scheduler import SchedulerLoop

        loop = SchedulerLoop(_get_schedule_store(), fire_due_schedule)
        await loop.start()
        app.state.batch_scheduler = loop
        print("[startup] Batch scheduler enabled")
    except Exception as e:
        print(f"[startup] Batch scheduler skipped: {e}")


@app.on_event("shutdown")
async def _shutdown_scheduler():
    loop = getattr(app.state, "batch_scheduler", None)
    if loop is not None:
        await loop.stop()


@app.on_event("startup")
async def _startup_result_copies():
    """Start copying saved results offsite when JONA_RESULT_COPIES is set; off otherwise."""
    from .storage import result_copies

    started = result_copies.start_sync()
    if started:
        app.state.result_copy_sync = started[0]
        print("[startup] Offsite result copies enabled")


@app.on_event("shutdown")
async def _shutdown_result_copies():
    sync = getattr(app.state, "result_copy_sync", None)
    if sync is not None:
        sync.stop()


# Set while the server shuts down: runs cut off then end without a done, so after the
# restart their logs tell the client the analysis stopped early.
_shutting_down = False


@app.on_event("shutdown")
async def _shutdown_sdk_bridge():
    """Notify active WebSocket connections and disconnect SDK clients on shutdown."""
    global _shutting_down
    _shutting_down = True
    for sid, ws in list(connections.items()):
        try:
            await ws.send_json({"type": "server_restart", "content": "Server is restarting, please wait..."})
        except Exception:
            pass
    if hasattr(app.state, "sdk_bridge"):
        print("[Shutdown] Disconnecting all SDK sessions...", flush=True)
        await app.state.sdk_bridge.disconnect_all()
        print("[Shutdown] All SDK sessions disconnected.", flush=True)


@app.on_event("shutdown")
async def _shutdown_earth_intelligence_worker():
    """Stop the optional Earth Intelligence worker cleanly."""
    worker = getattr(app.state, "earth_intelligence_worker", None)
    thread = getattr(app.state, "earth_intelligence_worker_thread", None)
    if worker is None:
        return
    await worker.stop()  # sets _running=False; run_forever exits after its current sleep
    if thread is not None:
        # Best-effort join; the thread is a daemon, so process exit is clean regardless.
        thread.join(timeout=2.0)
    print("[Shutdown] Earth Intelligence worker stopped.", flush=True)

# Store active connections, sessions, and workflow history
connections: Dict[str, WebSocket] = {}
sessions: Dict[str, dict] = {}  # session_id -> {"messages": [...], "last_active": float}
workflow_history: Dict[str, List[dict]] = {}

# WebSocket heartbeat interval (seconds)
_WS_PING_INTERVAL = 25  # Send ping every 25s (below typical 60s proxy timeouts)

class _SafeWebSocket:
    """Wrapper around WebSocket that serializes sends with an asyncio.Lock.

    Prevents frame corruption when heartbeat and query stream send concurrently.
    All attribute access is delegated to the underlying WebSocket, so existing
    code (bridge, hooks) works unchanged.
    """

    def __init__(self, ws: Any):
        self._ws = ws
        self._send_lock = asyncio.Lock()

    async def send_json(self, data: dict, **kwargs) -> None:
        async with self._send_lock:
            await self._ws.send_json(data, **kwargs)

    async def send_text(self, data: str) -> None:
        async with self._send_lock:
            await self._ws.send_text(data)

    async def send_bytes(self, data: bytes) -> None:
        async with self._send_lock:
            await self._ws.send_bytes(data)

    def __getattr__(self, name: str) -> Any:
        # Delegate everything else (receive_json, accept, close, etc.) to real WS
        return getattr(self._ws, name)

# Session TTL: expire sessions inactive for more than 30 minutes
_SESSION_TTL_SECONDS = 1800  # 30 minutes
_SESSION_MAX_COUNT = 100  # Maximum concurrent sessions


def _cleanup_expired_sessions():
    """Remove expired sessions and workflow history to free memory and prevent unbounded growth."""
    import time as _time
    now = _time.monotonic()
    expired = [
        sid for sid, sdata in sessions.items()
        if now - sdata.get("last_active", 0) > _SESSION_TTL_SECONDS
    ]
    for sid in expired:
        del sessions[sid]
        # Also clean up workflow history for expired sessions
        workflow_history.pop(sid, None)
    # Clean up orphaned workflow_history entries not in sessions
    orphaned = [sid for sid in workflow_history if sid not in sessions]
    for sid in orphaned:
        del workflow_history[sid]
    # Agent sessions are ended by the bridge's own reaper, which skips running ones.
    if expired or orphaned:
        print(f"[Session cleanup] Removed {len(expired)} expired sessions, {len(orphaned)} orphaned workflows, {len(sessions)} remaining")



# How long a finished run stays available for a client that was away when it ended.
_RUN_RETENTION_SECONDS = 6 * 3600
# A run nobody is watching is stopped after this long, so a closed tab can't keep
# spending API credit indefinitely.
_RUN_ABANDON_SECONDS = 2 * 3600
_RUN_SEND_TIMEOUT_SECONDS = 10
# Streamed text arrives about every 50 ms, so this holds hours of it; bytes are the real cap.
_RUN_BUFFER_MAX_EVENTS = 200_000
_RUN_BUFFER_MAX_BYTES = 20 * 1024 * 1024

class _RedactTokens(logging.Filter):
    """Map images carry the sign-in token in their URL, which mustn't reach the logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                re.sub(r"([?&]token=)[^&\s]+", r"\1REDACTED", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactTokens())

_query_runs: Dict[str, "_QueryRun"] = {}
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _run_log_path(session_id: str) -> Optional[Path]:
    """Where a session's latest run is kept across restarts, if its id is safe as a filename."""
    if not _SAFE_SESSION_ID.match(session_id or ""):
        return None
    folder = Path(os.environ.get("JONA_RUN_LOG_DIR") or Path.home() / ".jona_runs")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder / f"{session_id}.jsonl"


def _prune_run_logs() -> None:
    folder = Path(os.environ.get("JONA_RUN_LOG_DIR") or Path.home() / ".jona_runs")
    if not folder.is_dir():
        return
    cutoff = time.time() - _RUN_RETENTION_SECONDS
    for path in folder.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


async def _recover_run(session_id: str, owner: Optional[str]) -> Optional["_QueryRun"]:
    """Rebuild a run from its log after a restart, so its owner still gets what it produced.

    A run with no final event was cut off, and the client is told so.
    """
    path = _run_log_path(session_id)
    _prune_run_logs()
    if path is None or not path.exists():
        return None
    try:
        lines = (await asyncio.to_thread(path.read_text)).splitlines()
        meta = json.loads(lines[0])
        if meta.get("owner") != owner:
            return None
        events = [json.loads(line) for line in lines[1:]]
    except (OSError, ValueError, IndexError):
        return None
    run = _QueryRun(session_id, owner)
    run.run_id = meta["run_id"]
    for event in events:
        run.seq = event["seq"]
        run._remember(event, len(json.dumps(event, default=str)))
    run._log_path = path
    # Not "last event": a trace event can follow done.
    run.finished = any(event.get("type") == "done" for event in events)
    if not run.finished:
        await run.send_json({
            "type": "error",
            "content": "The server restarted during this analysis, so it stopped here. Send it again to rerun it.",
        })
        await run.send_json({"type": "done"})
    finished = asyncio.get_running_loop().create_future()
    finished.set_result(None)
    run.task = finished
    asyncio.get_running_loop().call_later(_RUN_RETENTION_SECONDS, run._forget)
    return run

# dNBR from the burn-severity template was post minus pre until this date.
_DNBR_SIGN_FIXED_ON = "2026-11-06 18:00"
_DNBR_TITLE_RE = re.compile(r"\bdnbr\b|burn severity|fire severity|delta nbr", re.IGNORECASE)


def _dnbr_sign_warning(job: dict) -> Optional[str]:
    """Warn about a saved dNBR result from when the template had the sign reversed."""
    created = str(job.get("created_at") or "").replace("T", " ")[:16]
    if created and created < _DNBR_SIGN_FIXED_ON and _DNBR_TITLE_RE.search(job.get("title") or ""):
        return (
            f"Caution: this result was saved on {created[:10]}, when dNBR from the burn severity "
            "template had its sign reversed, so burned land may read as regrowth. Rerun it "
            "before relying on it."
        )
    return None


async def _close_quietly(socket: Any) -> None:
    try:
        await asyncio.wait_for(socket.close(code=1011), timeout=5)
    except Exception:
        pass


class _WsInbox:
    """Reads a connection in one background task, so a frame is never lost to a cancelled read.

    Consumers take frames from a queue, which is safe to wait on with a timeout. The
    frame that ends the connection is delivered as an exception after those before it.
    """

    def __init__(self, websocket: Any):
        self._websocket = websocket
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        while True:
            try:
                frame = await self._websocket.receive_json()
            except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
                print(f"[WS] Ignoring malformed message: {exc}")
                continue
            except Exception as exc:
                await self._queue.put(exc)
                return
            if isinstance(frame, dict):
                frame.pop("_counted", None)  # set by this server only
                await self._queue.put(frame)
            else:
                print(f"[WS] Ignoring non-object message: {type(frame).__name__}")

    async def get(self) -> dict:
        frame = await self._queue.get()
        if isinstance(frame, Exception):
            await self._queue.put(frame)  # stays readable for the next consumer
            raise frame
        return frame

    def close(self) -> None:
        self._task.cancel()


# What a run may still send after its done. In a normal run, _run_query sends the trace id
# the client attaches feedback to only once the bridge has returned.
_AFTER_DONE_TYPES = frozenset({"trace"})


class _QueryRun:
    """One query's event stream, independent of any single WebSocket connection.

    The bridge and hooks are handed this object as their websocket. Each event is
    numbered, buffered, and forwarded to whichever connection is attached, so a
    dropped connection neither cancels the query nor loses its output.

    A run ends with exactly one `done`, which the client treats as the end of the turn.
    Several paths send one (stop, the specialist flow, error handlers), so the run keeps
    the first, drops repeats, and sends one itself if its task ends without any.
    """

    def __init__(self, session_id: str, owner: Optional[str]):
        self.session_id = session_id
        self.owner = owner
        self.run_id = str(uuid.uuid4())
        self.seq = 0
        self.stop_event = asyncio.Event()
        self.task: Optional[asyncio.Task] = None
        self._events: deque = deque()
        self._buffered_bytes = 0
        # Every connection following this run: two tabs on one session both see it.
        self._sockets: list = []
        self._lock = asyncio.Lock()
        self._abandon_handle: Optional[asyncio.TimerHandle] = None
        self._delivered_seq = 0
        self._log_path: Optional[Path] = None
        self._logged_bytes = 0
        self.finished = False
        # Messages sent while this run was finishing. Kept here, not on the connection,
        # so they survive a drop and run once the client is back.
        self.pending_messages: list = []

    @property
    def fully_delivered(self) -> bool:
        """True if an attached connection accepted every event so far."""
        return self._delivered_seq >= self.seq

    def start(self, coro) -> None:
        try:
            _prune_run_logs()
            self._log_path = _run_log_path(self.session_id)
            if self._log_path:
                self._log_path.write_text(json.dumps({"run_id": self.run_id, "owner": self.owner}) + "\n")
                self._log_path.chmod(0o600)
        except OSError as exc:
            # A query shouldn't fail because its restart log couldn't be written.
            print(f"[WS] Run log unavailable: {exc}")
            self._log_path = None
        self.task = asyncio.create_task(self._until_done(coro))
        self.task.add_done_callback(self._on_task_done)

    async def _until_done(self, coro) -> None:
        try:
            await coro
        finally:
            if not self.finished and not _shutting_down:
                try:
                    await self.send_json({"type": "done"})
                except Exception as exc:  # noqa: BLE001
                    print(f"[WS] Could not end run {self.run_id[:8]}: {exc!r}")

    def attach_new(self, websocket: Any) -> None:
        """Attach the connection that started the run; nothing to replay yet."""
        self._sockets = [websocket]

    async def send_json(self, data: dict, **kwargs) -> None:
        if data.get("type") == "done":
            if self.finished:
                return
            self.finished = True
        elif self.finished and data.get("type") not in _AFTER_DONE_TYPES:
            # The turn already ended for the user (Stop sends done at once), but the agent
            # can still emit while it winds down. Shown, that output lands in the next turn.
            print(f"[WS] Run {self.run_id[:8]} dropped a {data.get('type')} event sent after done")
            return
        size = len(json.dumps(data, default=str))
        async with self._lock:
            self.seq += 1
            event = {**data, "run_id": self.run_id, "seq": self.seq}
            self._remember(event, size)
            self._log_event(event)
            for socket in list(self._sockets):
                try:
                    await asyncio.wait_for(
                        socket.send_json(event, **kwargs), timeout=_RUN_SEND_TIMEOUT_SECONDS
                    )
                    self._delivered_seq = self.seq
                except Exception:
                    # Close it, so the client notices, reconnects, and catches up from
                    # the buffer. A socket left open would look alive and receive nothing.
                    self.detach(socket)
                    asyncio.create_task(_close_quietly(socket))

    def _log_event(self, event: dict) -> None:
        # Past the cap the log stops growing, but its done is still written: without it, a run
        # that finished was reported after a restart as cut off by the restart.
        if self._log_path is None or (self._logged_bytes > _RUN_BUFFER_MAX_BYTES
                                      and event.get("type") != "done"):
            return
        try:
            line = json.dumps(event, default=str) + "\n"
            self._logged_bytes += len(line)
            with self._log_path.open("a") as log:
                log.write(line)
        except OSError as exc:
            print(f"[WS] Could not log run event: {exc}")
            self._log_path = None

    def _remember(self, event: dict, size: int) -> None:
        self._events.append((event, size))
        self._buffered_bytes += size
        while len(self._events) > 1 and (
            len(self._events) > _RUN_BUFFER_MAX_EVENTS
            or self._buffered_bytes > _RUN_BUFFER_MAX_BYTES
        ):
            _, dropped = self._events.popleft()
            self._buffered_bytes -= dropped

    async def attach(self, websocket: Any, last_seq: int) -> None:
        """Replay events after last_seq, then deliver live events to websocket."""
        async with self._lock:
            if self._abandon_handle:
                self._abandon_handle.cancel()
                self._abandon_handle = None
            if websocket not in self._sockets:
                self._sockets.append(websocket)
            try:
                if self._events and self._events[0][0]["seq"] > last_seq + 1:
                    await websocket.send_json({
                        "type": "text",
                        "content": "Earlier output from this run was too long to keep, so it starts partway through.",
                    })
                closed_after = self._last_seq_of("clarification_closed")
                for event, _ in self._events:
                    # A question that has since closed isn't asked again.
                    if event.get("type") == "clarification" and event["seq"] < closed_after:
                        continue
                    if event["seq"] > last_seq:
                        await asyncio.wait_for(
                            websocket.send_json(event), timeout=_RUN_SEND_TIMEOUT_SECONDS
                        )
                self._delivered_seq = self.seq
            except Exception:
                self.detach(websocket)
                raise

    def _last_seq_of(self, event_type: str) -> int:
        """The seq of the latest buffered event of event_type, or 0."""
        return max((event["seq"] for event, _ in self._events if event.get("type") == event_type), default=0)

    def detach(self, websocket: Any) -> None:
        if websocket not in self._sockets:
            return
        self._sockets.remove(websocket)
        if not self._sockets and self.task and not self.task.done() and not self._abandon_handle:
            self._abandon_handle = asyncio.get_running_loop().call_later(
                _RUN_ABANDON_SECONDS, self._abandon
            )

    def _abandon(self) -> None:
        self._abandon_handle = None
        if not self._sockets and self.task and not self.task.done():
            print(f"[WS] Stopping abandoned run {self.run_id[:8]}")
            self.stop_event.set()
            asyncio.get_running_loop().call_later(15, self.task.cancel)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if self._abandon_handle:
            self._abandon_handle.cancel()
            self._abandon_handle = None
        if not task.cancelled() and task.exception():
            print(f"[WS] Query task error: {task.exception()!r}")
        asyncio.get_running_loop().call_later(_RUN_RETENTION_SECONDS, self._forget)

    def _forget(self) -> None:
        # The log path is per session, so a newer run may own it by now.
        if _query_runs.get(self.session_id) is self:
            del _query_runs[self.session_id]
            if self._log_path:
                self._log_path.unlink(missing_ok=True)


# How long a stopped agent may take to wind down before it is cut off. Winding down lets
# it report what the turn cost; cutting it off loses that.
_STOP_DRAIN_SECONDS = 60


# The status step shown while a message waits for the run before it to end.
_QUEUED_STEP_ID = "queued-message"


def _queued_notice(run: "_QueryRun") -> dict:
    """A status step telling the user why their message hasn't started yet."""
    if run.stop_event.is_set():
        content = "Waiting for the stopped analysis to wind down. Yours starts right after."
    else:
        content = "Another analysis on this chat is still running. Yours starts when it finishes."
    return {"type": "thinking", "thinking_id": _QUEUED_STEP_ID, "thinking_type": "processing",
            "content": content}


def _stop_run(run: "_QueryRun", app_state: Any) -> None:
    """Stop a run. The turn ends for the user at once; the agent winds down behind it."""
    if run.stop_event.is_set():
        return
    run.stop_event.set()
    bridge = getattr(app_state, "sdk_bridge", None)
    if bridge is not None and hasattr(bridge, "stop_pending_clarification"):
        bridge.stop_pending_clarification(run.session_id)  # an open question would hold the run
    asyncio.create_task(_finish_stopped_run(run, app_state))


async def _finish_stopped_run(run: "_QueryRun", app_state: Any) -> None:
    await run.send_json({"type": "done"})
    try:
        await asyncio.wait_for(asyncio.shield(run.task), timeout=_STOP_DRAIN_SECONDS)
    except asyncio.TimeoutError:
        run.task.cancel()
        bridge = getattr(app_state, "sdk_bridge", None)
        if bridge is not None:
            # A fresh client is created on the next query.
            await bridge.disconnect_session(run.session_id)
        print(f"[WS] Run {run.run_id[:8]} didn't wind down after stop; cut off, its cost unrecorded")
    except Exception:  # noqa: BLE001
        pass  # the run's own error handling has reported it


async def _run_query(message: str, session_id: str, websocket, client, app_state,
                     stop_event: asyncio.Event,
                     active_project_id: str = None,
                     correlation_id: str = "",
                     auth_user_id: str = None,
                     auth_user_email: str = None) -> None:
    """Execute a query via the Claude Agent SDK bridge and stream results to WebSocket.

    Runs as an asyncio.Task so the caller can concurrently listen for stop messages.

    Phase 16: auth_user_id + auth_user_email are forwarded to Langfuse via
    propagate_attributes() so every trace is attributed to the authenticated
    user and grouped by WebSocket session.
    """
    import time

    _metrics["ws_queries_total"] += 1
    _cid = correlation_id or str(uuid.uuid4())[:8]

    _sdk_backend = os.environ.get("OPENEO_SDK_BACKEND", "claude_sdk")
    print(f"[_run_query] cid={_cid} backend={_sdk_backend}, session={session_id}")

    # Phase 16: wrap the whole query in propagate_attributes so Langfuse tags
    # every Claude Agent SDK span with user_id + session_id. Gracefully no-op
    # if Langfuse isn't installed or initialized.
    #
    # Langfuse's propagate_attributes rejects non-string metadata values with
    # a warning `Propagated attribute 'metadata.X' value is not a string.
    # Dropping value.` and drops them. Filter None values to keep the
    # propagated metadata clean and avoid any side-effect the drop might have
    # on the rest of the propagation.
    _lf_metadata = {
        k: str(v)
        for k, v in {
            "email": auth_user_email,
            "project_id": active_project_id,
            "correlation_id": _cid,
        }.items()
        if v is not None
    }
    try:
        from langfuse import propagate_attributes  # noqa: PLC0415
        _lf_ctx = propagate_attributes(
            user_id=auth_user_id or "anonymous",
            session_id=session_id,
            tags=["jonaai", "chat", _sdk_backend],
            metadata=_lf_metadata,
        )
    except Exception:
        # Langfuse not installed/initialized — use a no-op context manager
        from contextlib import nullcontext
        _lf_ctx = nullcontext()

    if _sdk_backend == "claude_sdk":
        try:
            from .sdk.claude_sdk_bridge import ClaudeSDKBridge
            from .langfuse_setup import (
                current_trace_id as _lf_current_trace_id,
            )

            if not hasattr(app_state, "sdk_bridge"):
                app_state.sdk_bridge = ClaudeSDKBridge(config=client.config)
            bridge = app_state.sdk_bridge

            with _lf_ctx:
                # Phase A naming/input/output now happens INSIDE
                # ClaudeSDKBridge.query via update_current_span — calling it
                # here would hit an inactive span and log the
                # "No active span in current context" warning Langfuse emits.
                # propagate_attributes() is still needed for user_id /
                # session_id / tags / metadata propagation to all SDK spans.
                await bridge.query(
                    prompt=message,
                    websocket=websocket,
                    session_id=session_id,
                    stop_event=stop_event,
                    active_project_id=active_project_id,
                    auth_user_id=auth_user_id,
                )

            # Phase D+F: the bridge stashed the live @observe trace_id on
            # the session dict at query entry — current_trace_id() here
            # would return None because the @observe span has already closed
            # by the time bridge.query() returns.
            _tid: Optional[str] = None
            _response_text: str = ""
            try:
                _entry = (
                    app_state.sdk_bridge._sessions.get(session_id, {})
                    if hasattr(app_state, "sdk_bridge") else {}
                )
                if _entry:
                    _tid = _entry.get("last_trace_id")
                    _response_text = _entry.get("last_response_text", "")
            except Exception:
                pass

            # Phase D: emit the trace_id back to the frontend so it can attach
            # user-feedback scores to the correct trace.
            if _tid:
                try:
                    await websocket.send_json({"type": "trace", "trace_id": _tid})
                except Exception:
                    pass

            # Phase F: fire LLM-as-judge asynchronously.
            try:
                if _response_text and _tid:
                    from .evals.judge import judge_response
                    asyncio.create_task(
                        judge_response(
                            trace_id=_tid,
                            query=message,
                            response=_response_text,
                            user_id=auth_user_id,
                        )
                    )
                    print(f"[WS] judge scheduled: trace_id={_tid[:16]} response_len={len(_response_text)}")
                else:
                    print(
                        f"[WS] judge skipped: tid={'set' if _tid else 'MISSING'} "
                        f"response_text={'set' if _response_text else 'MISSING'}"
                    )
            except Exception as _judge_err:
                print(f"[WS] judge scheduling error: {_judge_err}")
        except Exception as e:
            _metrics["ws_queries_errors"] += 1
            print(f"[WS] cid={_cid} SDK bridge error: {e}")
            import traceback
            traceback.print_exc()
            await websocket.send_json({
                "type": "error",
                "content": str(e)
            })
        return  # SDK path done



# HTML_TEMPLATE imported from .html_template
# (removed ~1,676 lines of inline HTML)


# Determine frontend dist directory
_FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"


# API Endpoints
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def get_interface():
    """Serve the React frontend or fallback HTML template."""
    index_path = _FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path, media_type="text/html")
    return HTML_TEMPLATE


async def _ws_heartbeat(websocket: Any, cancel_event: asyncio.Event):
    """Send periodic ping to keep WebSocket alive through proxies."""
    try:
        while not cancel_event.is_set():
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=_WS_PING_INTERVAL)
                break  # cancel_event was set
            except asyncio.TimeoutError:
                pass  # Timeout means it's time to send a ping
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                break  # Connection already closed
    except asyncio.CancelledError:
        pass


async def _replay_saved_visualizations(websocket: Any, result_path: str) -> None:
    """Replay a saved result's persisted viz bundle so Charts/Table/Report/Process
    tabs repopulate on "Load on Map". The map itself is emitted separately from the
    GeoTIFF (reliable), so only non-map visualizations + report sections are replayed.
    Best-effort: older saves without a bundle simply emit nothing here.
    """
    try:
        from .storage.job_archive import read_visualizations
        bundle = read_visualizations(result_path)
        if not bundle:
            return
        for viz in bundle.get("visualizations", []):
            if isinstance(viz, dict) and viz.get("type") != "map":
                # replay=True keeps these out of the running turn's flush bundle
                # (a mid-run "Load on Map" must not contaminate the new save).
                await websocket.send_json(
                    {"type": "visualization", "replay": True, "visualization": viz})
        report_sections = bundle.get("reportSections") or []
        if report_sections:
            await websocket.send_json({"type": "report_sections", "sections": report_sections})
    except Exception as _e:
        print(f"[WS] Failed to replay saved visualizations: {_e}")


async def _send_saved_job(websocket: Any, save_id: str, user_id: Optional[str]) -> bool:
    """Emit a saved result's map (from its GeoTIFF) and replay its viz bundle.

    Shared by the idle-loop and during-query load_saved_job handlers. Sends the
    not-found error itself and returns False; returns True after a successful
    send. Langfuse scoring stays at the idle call site on purpose.
    """
    from .storage.job_archive import get_job as _get_saved_job
    from .storage.result_access import can_read_saved_result
    # Same message for missing and not-yours, so ids can't be probed.
    _readable = bool(user_id) and await asyncio.to_thread(can_read_saved_result, save_id, user_id)
    if _readable:
        # Opening a lost result restores it from its offsite copy, off the event loop and
        # only after the access check.
        from .storage import job_archive as _archive

        await asyncio.to_thread(_archive.restore_saved_result, save_id)
    _job = _get_saved_job(save_id) if _readable else None
    if not _job:
        await websocket.send_json({
            "type": "error",
            # Not a run's error: a Load on Map that fails mid-run must not end the turn.
            "code": "saved_job_unavailable",
            "content": f"Saved job '{save_id}' not found"
        })
        return False
    _cmap = _job.get("colormap", "viridis")
    # Auto-detect better colormap from title if saved as viridis
    if _cmap == "viridis":
        from .sdk.viz_extractor import _detect_colormap_from_title
        _cmap = _detect_colormap_from_title(_job.get("title", ""))
    # Compute bounds from GeoTIFF if not stored in DB
    _bounds = _job.get("bounds")
    if not _bounds and Path(_job["result_path"]).exists():
        try:
            import rasterio
            with rasterio.open(_job["result_path"]) as _src:
                b = _src.bounds
                _bounds = [[b.bottom, b.left], [b.top, b.right]]
        except Exception:
            pass
    from urllib.parse import quote
    if not Path(_job["result_path"]).exists():
        await websocket.send_json({
            "type": "error",
            "code": "saved_job_unavailable",
            "content": f"The file for '{_job['title']}' isn't on this server. It may have been "
                       "created on another machine. Rerun the analysis to recreate it.",
        })
        return False
    _caution = _dnbr_sign_warning(_job)
    if _caution:
        await websocket.send_json({"type": "text", "content": _caution})
    await websocket.send_json({
        "type": "visualization",
        "replay": True,
        "visualization": {
            "type": "map",
            "save_id": save_id,
            "data": {
                "type": "raster",
                "url": f"/render-raster?path={quote(_job['result_path'], safe='')}&colormap={quote(_cmap, safe='')}",
                "bounds": _bounds,
                "colormap": _cmap,
                "opacity": 0.5,
                "vmin": _job.get("vmin"),
                "vmax": _job.get("vmax"),
                "source": _job["result_path"],
            },
            "title": _job["title"],
        }
    })
    # Replay persisted charts/tables/process-graph/report tabs
    await _replay_saved_visualizations(websocket, _job["result_path"])
    return True


def _session_store() -> Any:
    """Session records, available before the first query has created the bridge."""
    if hasattr(app.state, "sdk_bridge"):
        return app.state.sdk_bridge._session_manager
    from .sdk.sessions import SessionManager

    return SessionManager(OpenEOAIConfig().sqlite_path)


async def _session_owner(session_id: str) -> Optional[str]:
    """The account that owns session_id, or None if no record says."""
    try:
        row = await asyncio.to_thread(_session_store().get_session, session_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[WS] Session owner lookup failed: {exc!r}")
        return None
    return row.get("user_id") if row else None


def _ws_origin_allowed(origin: Optional[str], host: Optional[str]) -> bool:
    """Browsers send the page's origin; refuse connections opened from other sites.

    Clients other than browsers send none, and still have to authenticate.
    """
    if not origin:
        return True
    if origin in _cors_origins or "*" in _cors_origins:
        return True
    return bool(host) and urlparse(origin).netloc == host


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time chat."""
    if not _ws_origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    connection_id = str(uuid.uuid4())
    _metrics["ws_connections_total"] += 1

    # Deprecated: query-param token leaks to logs/Referer. Use message-based auth.
    ws_token = websocket.query_params.get("token", "")
    is_authenticated = False
    auth_user_id = None  # Track which user owns this session (Supabase user UUID from JWT 'sub')
    auth_user_email = None  # Optional: email from JWT for logging
    auth_expires_at = None
    if ws_token:
        import logging as _dep_logging
        _dep_logging.getLogger(__name__).warning(
            "[WS] DEPRECATED: token in query string. Use message-based auth instead."
        )
        _payload = verify_supabase_token(ws_token)
        if _payload:
            is_authenticated = True
            auth_user_id = _payload["sub"]  # Supabase user UUID
            auth_user_email = _payload.get("email")
            auth_expires_at = _payload.get("exp")
    auth_status = "authenticated" if is_authenticated else "unauthenticated"
    import logging as _ws_logging
    _ws_logger = _ws_logging.getLogger(__name__)
    _ws_logger.info(f"[WS] Client connected: {connection_id} ({auth_status})")

    # Send session info — frontend may override with stored session_id
    session_id = str(uuid.uuid4())
    await websocket.send_json({
        "type": "session",
        "session_id": session_id,
        "authenticated": is_authenticated
    })

    # Wrap WebSocket with send lock to prevent concurrent write corruption
    # IMPORTANT: Store the WRAPPED version in connections dict so shutdown
    # handler uses the send-lock-protected wrapper, not the raw socket.
    websocket = _SafeWebSocket(websocket)
    connections[connection_id] = websocket

    inbox = _WsInbox(websocket)

    # Start heartbeat to keep connection alive through proxies
    heartbeat_cancel = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _ws_heartbeat(websocket, heartbeat_cancel)
    )

    try:
        client = OpenEOAIClient()

        async def _watch_run(run: "_QueryRun") -> None:
            """Serve stop/saved-job/clarification messages while a run is active.

            Returns when the run ends, or when this connection is lost. Losing the
            connection deliberately leaves the run going; the client can reattach.
            """
            query_task = run.task
            while not query_task.done():
                try:
                    stop_data = await asyncio.wait_for(inbox.get(), timeout=0.5)
                    if stop_data.get("type") == "stop":
                        _metrics["ws_queries_stopped"] += 1
                        print(f"[WS] Stop requested for session {session_id}")
                        # The turn ends now; keep serving until the agent has wound
                        # down, so a message sent meanwhile queues behind it.
                        _stop_run(run, app.state)
                    elif stop_data.get("type") == "load_saved_job":
                        # Handle saved job load even while query is running
                        if not is_authenticated:
                            await websocket.send_json({"type": "error", "content": "Authentication required."})
                            continue
                        _sid = stop_data.get("save_id")
                        if _sid:
                            if await _send_saved_job(websocket, _sid, auth_user_id):
                                print(f"[WS] Loaded saved job {_sid} during query processing")
                    elif stop_data.get("type") == "clarification_response":
                        # AskUserQuestion answers only ever arrive mid-query;
                        # dropping them here forced the 120s clarification timeout.
                        _sdk_backend = os.environ.get("OPENEO_SDK_BACKEND", "claude_sdk")
                        if _sdk_backend == "claude_sdk" and hasattr(app.state, "sdk_bridge"):
                            app.state.sdk_bridge.resolve_clarification(
                                session_id, stop_data.get("answers", {}), stop_data.get("question_id")
                            )
                    elif stop_data.get("type") == "message":
                        # Next turn arrived while this turn's task finishes its
                        # tail — buffer for the main loop instead of dropping.
                        run.pending_messages.append(stop_data)
                        print(f"[WS] Buffered next-turn message during query tail (session {session_id[:12]})")
                        await websocket.send_json(_queued_notice(run))
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    # Handled by the endpoint; the run itself keeps going.
                    raise
                except Exception as e:
                    # Fatal socket error — stop listening
                    print(f"[WS] Connection lost during run {run.run_id[:8]}: {e!r}")
                    break

        while True:
            _last_run = _query_runs.get(session_id)
            if (_last_run and _last_run.pending_messages and _last_run.task.done()
                    and is_authenticated and _last_run.owner == auth_user_id):
                data = _last_run.pending_messages.pop(0)
                try:
                    await websocket.send_json({"type": "thinking", "thinking_id": _QUEUED_STEP_ID,
                                               "thinking_completed": True})
                except Exception:  # noqa: BLE001 — a closed socket is handled below
                    pass
            else:
                data = await inbox.get()

            # Ignore pong responses from frontend heartbeat
            if data.get("type") == "pong":
                continue

            # A tab following a run another tab started can still stop it.
            if data.get("type") == "stop":
                _active = _query_runs.get(session_id)
                if _active and is_authenticated and _active.owner == auth_user_id and not _active.task.done():
                    _stop_run(_active, app.state)
                continue

            # Message-based authentication (preferred over query param)
            if data.get("type") == "authenticate":
                msg_token = data.get("token", "")
                _payload = verify_supabase_token(msg_token) if msg_token else None
                if _payload:
                    is_authenticated = True
                    auth_user_id = _payload["sub"]  # Supabase user UUID
                    auth_user_email = _payload.get("email")
                    auth_expires_at = _payload.get("exp")
                    _ws_logger.info(f"[WS] Authenticated: user={auth_user_id[:8]}...")
                    await websocket.send_json({
                        "type": "auth_result",
                        "authenticated": True,
                        "user_id": auth_user_id,
                        "email": auth_user_email,
                    })
                else:
                    _client_ip = websocket.client.host if hasattr(websocket, 'client') and websocket.client else "unknown"
                    _ws_logger.warning(f"[WS] Failed auth attempt from {_client_ip} on connection {connection_id}")
                    await websocket.send_json({
                        "type": "auth_result",
                        "authenticated": False,
                        "error": "Invalid token"
                    })
                continue

            # Session restore: frontend sends stored session_id to resume
            if data.get("type") == "restore_session":
                if not is_authenticated:
                    await websocket.send_json({"type": "error", "content": "Authentication required."})
                    continue
                old_session_id = data.get("session_id")
                if old_session_id:
                    # Ownership check: never resume a session owned by a DIFFERENT
                    # real user — that would expose their conversation history (and,
                    # before the ContextVar ref fix, their tool data).
                    _owned = _session_store().get_session(old_session_id)
                    _owner = _owned.get("user_id") if _owned else None
                    if _owner and _owner != auth_user_id:
                        # Another user's, or from before sessions recorded their user, as a
                        # shared browser can hold. Carry on in this connection's new session,
                        # which the client then stores, rather than refusing on every connect.
                        await websocket.send_json({
                            "type": "session_restored", "session_id": session_id,
                            "has_history": False, "run_active": False,
                        })
                        continue
                    session_id = old_session_id
                    # Check if SDK session exists for resume
                    _sdk_backend = os.environ.get("OPENEO_SDK_BACKEND", "claude_sdk")
                    has_sdk_session = False
                    if _sdk_backend == "claude_sdk" and hasattr(app.state, "sdk_bridge"):
                        stored = app.state.sdk_bridge._session_manager.get_sdk_session_id(old_session_id)
                        has_sdk_session = stored is not None
                    # A run started on an earlier connection may still be going, or
                    # may have finished while the client was away.
                    run = _query_runs.get(session_id) or await _recover_run(session_id, auth_user_id)
                    if run and run.owner != auth_user_id:
                        run = None
                    if run:
                        _query_runs[session_id] = run
                    await websocket.send_json({
                        "type": "session_restored",
                        "session_id": session_id,
                        "has_history": has_sdk_session,
                        "run_active": bool(run and not run.task.done()),
                    })
                    print(f"[WS] Session restored: {session_id[:12]}... (SDK resume: {has_sdk_session})")

                    if run:
                        last_seq = data.get("last_seq") if data.get("run_id") == run.run_id else 0
                        last_seq = last_seq if isinstance(last_seq, int) else 0
                        running = not run.task.done()
                        # A client with no position in this run didn't see it live, but
                        # if it was fully delivered elsewhere, replaying re-renders old output.
                        _seen_elsewhere = data.get("run_id") != run.run_id and run.fully_delivered
                        if running or (last_seq < run.seq and not _seen_elsewhere):
                            await websocket.send_json({
                                "type": "run_resumed",
                                "run_id": run.run_id,
                                "running": running,
                            })
                            await run.attach(websocket, last_seq)
                            print(f"[WS] Reattached run {run.run_id[:8]} from seq {last_seq} (running={running})")
                            await _watch_run(run)
                continue

            # Clarification response: frontend answers Claude's question
            if data.get("type") == "clarification_response":
                if not is_authenticated:
                    await websocket.send_json({"type": "error", "content": "Authentication required."})
                    continue
                _sdk_backend = os.environ.get("OPENEO_SDK_BACKEND", "claude_sdk")
                if _sdk_backend == "claude_sdk" and hasattr(app.state, "sdk_bridge"):
                    answers = data.get("answers", {})
                    app.state.sdk_bridge.resolve_clarification(session_id, answers, data.get("question_id"))
                continue

            # Direct load of a saved job (no AI roundtrip)
            if data.get("type") == "load_saved_job":
                if not is_authenticated:
                    await websocket.send_json({"type": "error", "content": "Authentication required."})
                    continue
                # Phase E: emit implicit-load-on-map score on the current trace.
                # If the user is explicitly loading a result onto the map, the
                # analysis that produced it was useful — positive signal.
                try:
                    from .langfuse_setup import emit_score
                    _trace_id_for_score = data.get("trace_id")
                    emit_score(
                        name="implicit-load-on-map",
                        value=1.0,
                        trace_id=_trace_id_for_score,
                        comment=f"user loaded save_id={data.get('save_id')}",
                    )
                except Exception:
                    pass
                _save_id = data.get("save_id")
                if _save_id:
                    await _send_saved_job(websocket, _save_id, auth_user_id)
                continue

            if data.get("type") == "message":
                # Enforce authentication for query messages
                if not is_authenticated:
                    await websocket.send_json({
                        "type": "error",
                        "content": "Authentication required. Please log in to send queries."
                    })
                    continue

                # A login is checked when it's made, so check it hasn't expired since. The
                # client signs in again with a fresh token when it reconnects.
                if auth_expires_at and time.time() >= auth_expires_at:
                    # A queued message goes back on its run's queue and runs after the
                    # reconnect, so the client must not be asked to send it again too.
                    requeued = bool(_last_run and data.get("_counted") and _last_run.owner == auth_user_id)
                    if requeued:
                        _last_run.pending_messages.insert(0, data)
                    await websocket.send_json({
                        "type": "error",
                        "code": "auth_expired",
                        "requeued": requeued,
                        "content": ("Your sign-in expired. Reconnecting; your queued message will still run."
                                    if requeued else
                                    "Your sign-in expired. Reconnecting; please send that again."),
                    })
                    await websocket.close(code=4401)
                    break

                # Rate limit: max _WS_MAX_MSGS_PER_MIN queries per session per minute
                import time as _rl_time
                _now = _rl_time.time()
                # By user, so opening a new connection doesn't reset it.
                _rate_key = auth_user_id or session_id
                if _rate_key not in _ws_rate:
                    _ws_rate[_rate_key] = []
                _ws_rate[_rate_key] = [t for t in _ws_rate[_rate_key] if t > _now - 60]
                # A queued message was counted when it arrived.
                if not data.get("_counted") and len(_ws_rate[_rate_key]) >= _WS_MAX_MSGS_PER_MIN:
                    await websocket.send_json({
                        "type": "error",
                        "content": "Rate limit exceeded. Please wait before sending more queries."
                    })
                    continue
                if not data.get("_counted"):
                    _ws_rate[_rate_key].append(_now)
                data["_counted"] = True

                message = data.get("content", "")
                # Security: don't let client override session_id (set at connection
                # or via restore_session). Prevents session hijacking.
                # session_id is already bound to this connection.
                active_project_id = data.get("active_project_id") or None
                persona = data.get("persona") or None

                # Inject persona context into the query so the Decision Layer
                # frames responses for the right audience
                _PERSONA_FRAMES = {
                    "farmer": "[PERSONA: FARMER] Respond as if advising a farmer about their crops and land. Use agricultural terms, recommend irrigation/fertilization actions, reference growing seasons.",
                    "insurer": "[PERSONA: INSURER] Respond as if advising a crop insurance underwriter. Quantify risk probabilities, estimate potential losses, reference policy thresholds and claim triggers.",
                    "investor": "[PERSONA: INVESTOR] Respond as if advising an agricultural investment analyst. Focus on yield forecasts, asset performance metrics, ROI, and portfolio risk assessment.",
                    "government": "[PERSONA: GOVERNMENT] Respond as if advising a government environmental agency. Focus on regulatory compliance, environmental impact, land use policy, and public safety.",
                }
                if persona and persona in _PERSONA_FRAMES:
                    message = f"{_PERSONA_FRAMES[persona]}\n\n{message}"

                # Security: don't log query content (may contain sensitive locations/data)
                _ws_logger.info(f"[WS] Query received: session={session_id[:12]}... project={active_project_id} persona={persona}")
                _ws_logger.debug(f"[WS] Query content: {message[:50]}...")

                # Cleanup expired sessions periodically
                import time as _time
                _cleanup_expired_sessions()

                # Get or create session
                if session_id not in sessions:
                    sessions[session_id] = {"messages": [], "last_active": _time.monotonic()}
                sessions[session_id]["last_active"] = _time.monotonic()

                # Generate correlation_id for this query (for log tracing)
                correlation_id = str(uuid.uuid4())[:8]
                sessions[session_id]["correlation_id"] = correlation_id

                # Enforce max session count (evict oldest)
                if len(sessions) > _SESSION_MAX_COUNT:
                    oldest = min(sessions, key=lambda s: sessions[s].get("last_active", 0))
                    del sessions[oldest]
                    print(f"[Session cleanup] Evicted oldest session, {len(sessions)} remaining")

                # The bridge records the owner as each query starts, so another account
                # can't take a session over while its first answer is on its way.
                _owner = await _session_owner(session_id)
                # Read the session's run again after the last await: _last_run was read before
                # this tab waited for its message, and another tab may have started a run since.
                # Nothing below awaits before _query_runs[session_id] is set, so this check and
                # that claim can't interleave with another tab's.
                _last_run = _query_runs.get(session_id)
                if _owner and _owner != auth_user_id:
                    await websocket.send_json({
                        "type": "error",
                        "content": "This conversation belongs to another account. Start a new chat to continue.",
                    })
                    continue
                if _last_run and not _last_run.task.done() and _last_run.owner != auth_user_id:
                    await websocket.send_json({
                        "type": "error",
                        "content": "This conversation is busy in another account. Start a new chat to continue.",
                    })
                    continue

                # One run per session: a second tab's message waits its turn, and that tab
                # follows the run in progress rather than starting another on the same agent.
                if _last_run and not _last_run.task.done() and _last_run.owner == auth_user_id:
                    _last_run.pending_messages.append(data)
                    # Say why nothing is happening yet: after Stop the turn has ended for
                    # the user, but the agent can take up to _STOP_DRAIN_SECONDS to wind down.
                    await websocket.send_json(_queued_notice(_last_run))
                    await _last_run.attach(websocket, _last_run.seq)
                    await _watch_run(_last_run)
                    continue

                # Run query as background task, listen for stop concurrently
                # The run outlives this connection: its channel buffers events so a
                # reconnecting client can catch up (see _QueryRun).
                run = _QueryRun(session_id, auth_user_id)
                run.attach_new(websocket)
                if _last_run and _last_run.owner == auth_user_id:
                    run.pending_messages = _last_run.pending_messages
                    # Every tab on this session follows the next run, including the one
                    # whose queued message this may be.
                    run._sockets.extend(s for s in _last_run._sockets if s is not websocket)
                run.start(
                    _run_query(message, session_id, run, client, app.state, run.stop_event,
                               active_project_id=active_project_id, correlation_id=correlation_id,
                               auth_user_id=auth_user_id, auth_user_email=auth_user_email)
                )
                _query_runs[session_id] = run

                await _watch_run(run)

    except WebSocketDisconnect as e:
        print(f"[WS] Client disconnected: {connection_id} code={e.code}")
    except RuntimeError as e:
        if "disconnect" in str(e).lower():
            print(f"[WS] Client disconnected (runtime): {connection_id}")
        else:
            print(f"[WS] Runtime error: {e}")
            import traceback
            traceback.print_exc()
    except Exception as e:
        print(f"[WS] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _metrics["ws_disconnections_total"] += 1
        inbox.close()
        # Stop heartbeat
        heartbeat_cancel.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        # An active run keeps going without this connection; its clarification can
        # still be answered after a reconnect.
        _active_run = _query_runs.get(session_id)
        if _active_run:
            _active_run.detach(websocket)
        # Cancel any pending clarification futures so SDK doesn't hang 120s
        try:
            if hasattr(app.state, "sdk_bridge") and not (_active_run and not _active_run.task.done()):
                app.state.sdk_bridge.cancel_pending_clarification(session_id)
        except Exception:
            pass
        if connection_id in connections:
            del connections[connection_id]


@app.get("/api/health")
async def health_check():
    """Health check endpoint with detailed AI app system information.

    Moved from /health to /api/health because the ASGI dispatcher routes
    /health* to the OpenEO backend, making the AI app's /health unreachable.
    """
    now = datetime.utcnow()
    uptime_seconds = (now - _SERVER_START_TIME).total_seconds()

    # Determine model name from config if possible
    model_name = "unknown"
    try:
        from .sdk.client import OpenEOAIConfig
        model_name = OpenEOAIConfig().model
    except Exception:
        model_name = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")

    return {
        "status": "healthy",
        "uptime_seconds": round(uptime_seconds, 1),
        "active_sessions": len(sessions),
        "active_connections": len(connections),
        "model": model_name,
        "version": "0.2.0",
        "tools_count": len(TOOL_DEFINITIONS),
    }


@app.get("/metrics")
async def prometheus_metrics(request: Request):
    """Expose internal counters in Prometheus text exposition format, to this host only."""
    _proxied = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if _proxied or (request.client and request.client.host not in ("127.0.0.1", "::1")):
        raise HTTPException(status_code=404, detail="Not found")
    now = datetime.utcnow()
    uptime = (now - _SERVER_START_TIME).total_seconds()
    lines = [
        "# HELP jonaai_uptime_seconds Time since server start.",
        "# TYPE jonaai_uptime_seconds gauge",
        f"jonaai_uptime_seconds {uptime:.1f}",
        "# HELP jonaai_active_sessions Current active session count.",
        "# TYPE jonaai_active_sessions gauge",
        f"jonaai_active_sessions {len(sessions)}",
        "# HELP jonaai_active_connections Current WebSocket connections.",
        "# TYPE jonaai_active_connections gauge",
        f"jonaai_active_connections {len(connections)}",
    ]
    for key, val in _metrics.items():
        lines.append(f"# HELP jonaai_{key} Counter for {key.replace('_', ' ')}.")
        lines.append(f"# TYPE jonaai_{key} counter")
        lines.append(f"jonaai_{key} {val}")
    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


# Mount static assets from the React frontend build (must be after all API routes)
_assets_dir = _FRONTEND_DIST / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="static-assets")


@app.get("/{full_path:path}")
async def spa_catch_all(full_path: str):
    """Serve static files from dist root, or index.html for SPA routing."""
    # An unknown API path is an error, not a page: HTML here hides it from API clients.
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    # Resolve to prevent path traversal (../../.env etc.)
    static_file = (_FRONTEND_DIST / full_path).resolve()
    if full_path and static_file.is_relative_to(_FRONTEND_DIST) and static_file.is_file():
        return FileResponse(static_file)
    index_path = _FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse("<h1>Frontend not built</h1>", status_code=404)


# ── ASGI Dispatcher: route /openeo/* and /health* to OpenEO backend ──
# This replaces app.mount() which has route ordering issues with SPA catch-all.
_OPENEO_PREFIXES = ("/openeo/", "/health")

class _CombinedASGI:
    """ASGI dispatcher that routes by path prefix.

    Paths starting with /openeo/ or /health go to the OpenEO backend.
    Everything else goes to the main AI web interface.
    """

    def __init__(self, main_app, openeo_app):
        self.main = main_app
        self.openeo = openeo_app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if any(path.startswith(p) for p in _OPENEO_PREFIXES):
                await self.openeo(scope, receive, send)
                return
        await self.main(scope, receive, send)

try:
    from openeo_app.app import app as openeo_backend_app
    combined_app = AccessGuard(_CombinedASGI(app, openeo_backend_app))
    print("[startup] OpenEO backend routed via ASGI dispatcher (/openeo/*, /health*)")
except Exception as e:
    combined_app = AccessGuard(app)
    print(f"[startup] WARNING: Could not load OpenEO backend: {e}")

# Expose combined_app as the default ASGI entry point for uvicorn.
# Usage: uvicorn openeo_ai.web_interface:asgi_app --port 8080
# This ensures both the AI interface AND the OpenEO backend are served.
asgi_app = combined_app


def _default_bind_host() -> str:
    """Bind to loopback in dev mode; all interfaces otherwise. An explicit
    --host always overrides this."""
    dev = os.environ.get("OPENEO_DEV_MODE", "").lower() == "true"
    return "127.0.0.1" if dev else "0.0.0.0"


def run_server(host: Optional[str] = None, port: int = 5000):
    """Run the enhanced web server."""
    if host is None:
        host = _default_bind_host()
    print(f"\n{'='*60}")
    print("OpenEO AI Enhanced Web Interface")
    print(f"{'='*60}")
    print(f"\nOpen your browser at: http://localhost:{port}")
    print(f"\nFeatures:")
    print(f"  - Natural language query processing")
    print(f"  - Interactive maps and charts")
    print(f"  - Quality metrics dashboard")
    print(f"  - Workflow history")
    print(f"  - Export to Jupyter notebooks")
    print(f"\nAvailable tools: {len(TOOL_DEFINITIONS)}")
    print(f"\n{'='*60}\n")

    uvicorn.run(combined_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse

    # Crash telemetry was turned on at import (see openeo_ai/crash_telemetry.py): on a
    # development machine it writes /tmp/jonaai-backend-fault.log, as before.

    parser = argparse.ArgumentParser(description="OpenEO AI Enhanced Web Interface")
    parser.add_argument("--host", default=_default_bind_host(), help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port)
