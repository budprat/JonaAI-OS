# ABOUTME: Session management with PostgreSQL persistence for conversation context.
# Handles session creation, retrieval, update, and cleanup operations.

"""
Session management for OpenEO AI Assistant.

Manages chat sessions with persistence to PostgreSQL via the _pg_compat shim.
The shim translates SQLite-style queries (`?` placeholders, `INSERT OR IGNORE`)
to PostgreSQL syntax, so the original SQLite-era code works unchanged.

Schema is now owned by Alembic (`add_ai_tables` + `phase6_sqlite_to_pg`):
  ai_sessions(id UUID PK, user_id, title, context JSON, messages JSON,
              created_at, updated_at, is_active, sdk_session_id)

The original SQLite SessionManager used 'updated_at' instead of 'updated_at';
queries here have been adapted to use the canonical column name.
"""

import uuid
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from openeo_ai import _pg_compat as sqlite3  # PostgreSQL shim with sqlite3 API


def _to_dict(value: Any) -> Dict[str, Any]:
    """Normalize a JSON column value to a dict.

    Handles both psycopg2 (auto-decodes JSON columns to dict) and the legacy
    SQLite path (stored JSON as TEXT). Returns empty dict for None/empty.
    """
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _isoformat(value: Any) -> str:
    """Normalize a timestamp column value to ISO 8601 string.

    Handles both psycopg2 (returns datetime objects from timestamp columns) and
    the legacy SQLite path (stored as TEXT).
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class SessionManager:
    """
    Manage AI chat sessions with SQLite persistence.

    Provides session creation, retrieval, update, and deletion
    with context storage for conversation continuity.
    """

    def __init__(self, db_path: str = "data/openeo_ai.db"):
        """
        Initialize SessionManager.

        Args:
            db_path: Legacy SQLite path. Ignored — connection now uses POSTGRES_*
                     environment variables via the _pg_compat shim. Kept in the
                     signature for backward compatibility with existing callers.
        """
        self.db_path = db_path
        self._conn = None  # Reserved for future per-instance connection caching
        if sqlite3.uses_sqlite_backend(db_path):
            if db_path == ":memory:":
                self._conn = sqlite3.connect(db_path)
                self._init_sqlite_db(self._conn)
            else:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(db_path)
                try:
                    self._init_sqlite_db(conn)
                finally:
                    conn.close()

    def _get_connection(self):
        """Get a fresh PostgreSQL connection (via _pg_compat shim)."""
        if self._conn is not None:
            return self._conn
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _init_sqlite_db(conn) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                context TEXT DEFAULT '{}',
                messages TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                sdk_session_id TEXT
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ai_sessions)").fetchall()}
        missing_columns = {
            "user_id": "ALTER TABLE ai_sessions ADD COLUMN user_id TEXT DEFAULT 'default'",
            "title": "ALTER TABLE ai_sessions ADD COLUMN title TEXT",
            "context": "ALTER TABLE ai_sessions ADD COLUMN context TEXT DEFAULT '{}'",
            "messages": "ALTER TABLE ai_sessions ADD COLUMN messages TEXT DEFAULT '[]'",
            "created_at": "ALTER TABLE ai_sessions ADD COLUMN created_at TEXT DEFAULT ''",
            "updated_at": "ALTER TABLE ai_sessions ADD COLUMN updated_at TEXT DEFAULT ''",
            "is_active": "ALTER TABLE ai_sessions ADD COLUMN is_active INTEGER DEFAULT 1",
            "sdk_session_id": "ALTER TABLE ai_sessions ADD COLUMN sdk_session_id TEXT",
        }
        for column_name, ddl in missing_columns.items():
            if column_name not in columns:
                conn.execute(ddl)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_ai_sessions_user_id ON ai_sessions(user_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_ai_sessions_user_updated ON ai_sessions(user_id, updated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_ai_sessions_sdk_session_id ON ai_sessions(sdk_session_id)"
        )
        conn.commit()

    def ensure_session(self, session_id: str, user_id: str = "default") -> str:
        """Ensure a session exists with the given ID. Creates if missing.

        Args:
            session_id: Specific session ID to use (from frontend)
            user_id: User identifier

        Returns:
            The session_id
        """
        existing = self.get_session(session_id)
        if existing:
            return session_id
        now = datetime.utcnow().isoformat()
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO ai_sessions (id, user_id, created_at, updated_at, context)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, user_id, now, now, json.dumps({}))
            )
            conn.commit()
        finally:
            if self._conn is None:
                conn.close()
        return session_id

    def create_session(self, user_id: str) -> str:
        """
        Create a new session.

        Args:
            user_id: User identifier

        Returns:
            New session ID (UUID string)
        """
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO ai_sessions (id, user_id, created_at, updated_at, context)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, user_id, now, now, json.dumps({}))
            )
            conn.commit()
        finally:
            if self._conn is None:
                conn.close()

        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve session by ID.

        Args:
            session_id: Session identifier

        Returns:
            Session dict or None if not found
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM ai_sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()

            if row:
                result = {
                    "id": str(row["id"]),  # UUID → str for JSON serialization
                    "user_id": row["user_id"],
                    "created_at": _isoformat(row["created_at"]),
                    "updated_at": _isoformat(row["updated_at"]),
                    "context": _to_dict(row["context"]),
                    "sdk_session_id": row["sdk_session_id"] if "sdk_session_id" in row.keys() else None,
                }
                return result
            return None
        finally:
            if self._conn is None:
                conn.close()

    def update_session(self, session_id: str, user_id: str) -> bool:
        """
        Update session updated_at timestamp.

        Args:
            session_id: Session identifier
            user_id: User identifier (for verification)

        Returns:
            True if updated, False if not found
        """
        now = datetime.utcnow().isoformat()

        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE ai_sessions
                SET updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (now, session_id, user_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            if self._conn is None:
                conn.close()

    def update_context(self, session_id: str, context: Dict[str, Any]) -> bool:
        """
        Update session context.

        Args:
            session_id: Session identifier
            context: Context data to store

        Returns:
            True if updated, False if not found
        """
        now = datetime.utcnow().isoformat()

        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE ai_sessions
                SET context = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(context), now, session_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            if self._conn is None:
                conn.close()

    def list_sessions(
        self,
        user_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List sessions for a user.

        Args:
            user_id: User identifier
            limit: Maximum number of sessions to return

        Returns:
            List of session dicts
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM ai_sessions
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit)
            )

            return [
                {
                    "id": str(row["id"]),  # UUID → str
                    "user_id": row["user_id"],
                    "created_at": _isoformat(row["created_at"]),
                    "updated_at": _isoformat(row["updated_at"]),
                    "context": _to_dict(row["context"]),
                }
                for row in cursor.fetchall()
            ]
        finally:
            if self._conn is None:
                conn.close()

    def set_sdk_session_id(self, session_id: str, sdk_session_id: str) -> bool:
        """Store the Claude SDK session ID for resume support.

        Args:
            session_id: Our session identifier
            sdk_session_id: The SDK's session ID from ResultMessage

        Returns:
            True if updated, False if session not found
        """
        now = datetime.utcnow().isoformat()
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE ai_sessions
                SET sdk_session_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (sdk_session_id, now, session_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            if self._conn is None:
                conn.close()

    def get_sdk_session_id(self, session_id: str) -> Optional[str]:
        """Get the SDK session ID for a given session.

        Args:
            session_id: Our session identifier

        Returns:
            SDK session ID string, or None if not found/not set
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT sdk_session_id FROM ai_sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        finally:
            if self._conn is None:
                conn.close()

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session.

        Args:
            session_id: Session identifier

        Returns:
            True if deleted, False if not found
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM ai_sessions WHERE id = ?",
                (session_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            if self._conn is None:
                conn.close()

    def clear_all_sdk_session_ids(self) -> int:
        """Clear all SDK session IDs from the database.

        Called on server startup to prevent resuming stale SDK sessions
        whose subprocess contexts no longer exist.

        Returns:
            Number of sessions updated
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "UPDATE ai_sessions SET sdk_session_id = NULL WHERE sdk_session_id IS NOT NULL"
            )
            conn.commit()
            return cursor.rowcount
        finally:
            if self._conn is None:
                conn.close()

    def delete_older_than(self, threshold: datetime) -> int:
        """
        Delete sessions older than threshold.

        Args:
            threshold: Delete sessions with updated_at before this time

        Returns:
            Number of deleted sessions
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM ai_sessions WHERE updated_at < ?",
                (threshold.isoformat(),)
            )
            conn.commit()
            return cursor.rowcount
        finally:
            if self._conn is None:
                conn.close()
