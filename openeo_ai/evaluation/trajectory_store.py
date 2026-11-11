"""Capture and persist agent tool call trajectories.

Trajectories record the complete sequence of tool invocations made by the AI
agent during a single user query. Tool calls accumulate in memory during the
query and are batch-persisted to SQLite on completion.

Usage:
    store = TrajectoryStore("data/openeo_ai.db")
    tid = store.start_trajectory("session-1", "Show NDVI for Berlin", "vegetation")
    store.record_tool_call(tid, ToolCall(
        tool_name="openeo_generate_graph",
        parameters={"description": "NDVI Berlin"},
        result_summary="Graph generated",
        success=True,
        duration_ms=150,
        timestamp="2024-01-01T00:00:00",
    ))
    store.finish_trajectory(tid, success=True)
"""

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    """One tool invocation within a trajectory."""

    tool_name: str                             # e.g. "openeo_generate_graph"
    parameters: Dict[str, Any] = field(        # Tool input args
        default_factory=dict
    )
    result_summary: Optional[str] = None       # First 500 chars of result
    success: bool = True                       # Whether the tool returned without error
    duration_ms: int = 0                       # Execution time in milliseconds
    timestamp: str = ""                        # ISO datetime


@dataclass
class Trajectory:
    """Complete tool calling sequence for one user query."""

    id: str                                    # UUID
    session_id: str                            # WebSocket session
    user_prompt: str                           # Original user query
    domain: str = ""                           # Classified domain (fire, vegetation, etc.)
    tool_calls: List[ToolCall] = field(        # Ordered sequence of tool invocations
        default_factory=list
    )
    final_success: bool = True                 # Whether the overall query succeeded
    total_duration_ms: int = 0                 # End-to-end time
    created_at: str = ""                       # ISO datetime


_RESULT_SUMMARY_MAX_LEN = 500

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trajectories (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_prompt TEXT NOT NULL,
    domain TEXT DEFAULT '',
    tool_calls TEXT NOT NULL,
    final_success INTEGER DEFAULT 1,
    total_duration_ms INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traj_session ON trajectories(session_id);
CREATE INDEX IF NOT EXISTS idx_traj_domain ON trajectories(domain);
CREATE INDEX IF NOT EXISTS idx_traj_created ON trajectories(created_at);
"""


class TrajectoryStore:
    """SQLite-backed store for agent tool call trajectories.

    Thread-safe. Tool calls accumulate in memory during a query and are
    batch-persisted to SQLite on finish_trajectory().
    """

    def __init__(self, db_path: str = "data/openeo_ai.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        # In-progress trajectories: id -> Trajectory (in memory until finished)
        self._active: Dict[str, Trajectory] = {}
        # Start timestamps for duration calculation
        self._start_times: Dict[str, float] = {}
        # For :memory: databases, reuse one connection (each new conn is empty)
        self._shared_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:")
            self._shared_conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a SQLite connection.

        For file-based databases: creates a new connection per call (thread-safe).
        For :memory: databases: reuses a shared connection.
        """
        if self._shared_conn is not None:
            return self._shared_conn
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        """Close connection unless it's the shared :memory: connection."""
        if conn is not self._shared_conn:
            conn.close()

    def _ensure_schema(self) -> None:
        """Create trajectories table if it doesn't exist."""
        conn = self._get_conn()
        try:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        finally:
            self._close_conn(conn)

    def start_trajectory(
        self, session_id: str, user_prompt: str, domain: str = ""
    ) -> str:
        """Begin recording a new trajectory. Returns trajectory ID."""
        tid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        trajectory = Trajectory(
            id=tid,
            session_id=session_id,
            user_prompt=user_prompt,
            domain=domain,
            tool_calls=[],
            created_at=now,
        )
        with self._lock:
            self._active[tid] = trajectory
            import time
            self._start_times[tid] = time.monotonic()
        return tid

    def record_tool_call(self, trajectory_id: str, tool_call: ToolCall) -> None:
        """Append a tool call to an in-progress trajectory."""
        # Truncate result_summary if too long
        if (tool_call.result_summary
                and len(tool_call.result_summary) > _RESULT_SUMMARY_MAX_LEN):
            tool_call.result_summary = (
                tool_call.result_summary[:_RESULT_SUMMARY_MAX_LEN]
            )
        with self._lock:
            traj = self._active.get(trajectory_id)
            if traj is not None:
                traj.tool_calls.append(tool_call)

    def finish_trajectory(self, trajectory_id: str, success: bool) -> None:
        """Mark trajectory as complete and persist to SQLite."""
        import time

        with self._lock:
            traj = self._active.pop(trajectory_id, None)
            start_time = self._start_times.pop(trajectory_id, None)

        if traj is None:
            return

        traj.final_success = success
        if start_time is not None:
            traj.total_duration_ms = int(
                (time.monotonic() - start_time) * 1000
            )

        # Serialize tool_calls to JSON
        tool_calls_json = json.dumps(
            [asdict(tc) for tc in traj.tool_calls],
            default=str,
        )

        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO trajectories
                   (id, session_id, user_prompt, domain, tool_calls,
                    final_success, total_duration_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    traj.id,
                    traj.session_id,
                    traj.user_prompt,
                    traj.domain,
                    tool_calls_json,
                    1 if traj.final_success else 0,
                    traj.total_duration_ms,
                    traj.created_at,
                ),
            )
            conn.commit()
        finally:
            self._close_conn(conn)

    def get_trajectory(self, trajectory_id: str) -> Optional[Trajectory]:
        """Load a trajectory by ID from SQLite."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM trajectories WHERE id = ?",
                (trajectory_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_trajectory(row)
        finally:
            self._close_conn(conn)

    def list_trajectories(
        self, domain: str = "", limit: int = 50
    ) -> List[Trajectory]:
        """List recent trajectories, optionally filtered by domain."""
        conn = self._get_conn()
        try:
            if domain:
                rows = conn.execute(
                    """SELECT * FROM trajectories
                       WHERE domain = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (domain, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM trajectories
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [self._row_to_trajectory(r) for r in rows]
        finally:
            self._close_conn(conn)

    @staticmethod
    def _row_to_trajectory(row: sqlite3.Row) -> Trajectory:
        """Convert a SQLite row to a Trajectory dataclass."""
        tool_calls_data = json.loads(row["tool_calls"])
        tool_calls = [
            ToolCall(
                tool_name=tc["tool_name"],
                parameters=tc.get("parameters", {}),
                result_summary=tc.get("result_summary"),
                success=tc.get("success", True),
                duration_ms=tc.get("duration_ms", 0),
                timestamp=tc.get("timestamp", ""),
            )
            for tc in tool_calls_data
        ]
        return Trajectory(
            id=row["id"],
            session_id=row["session_id"],
            user_prompt=row["user_prompt"],
            domain=row["domain"],
            tool_calls=tool_calls,
            final_success=bool(row["final_success"]),
            total_duration_ms=row["total_duration_ms"],
            created_at=row["created_at"],
        )
