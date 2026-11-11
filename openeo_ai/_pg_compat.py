# ABOUTME: SQLite-API-compatible wrapper around psycopg2.
# Lets the existing batch + session module code (originally written for sqlite3)
# work against PostgreSQL with minimal diff. Used by sdk/sessions.py and batch/*.

"""
sqlite3 → psycopg2 compatibility shim for JonaAI.

The batch module and SessionManager were originally written against sqlite3.
Migrating to PostgreSQL would normally require rewriting every query (`?` →
`%s` placeholders, `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`, etc).
Instead, this shim provides a drop-in replacement: change one import line and
the rest of the code stays the same.

Usage:
    # Before:
    import sqlite3
    conn = sqlite3.connect(self.db_path)

    # After:
    from openeo_ai import _pg_compat as sqlite3
    conn = sqlite3.connect(self.db_path)  # db_path ignored, uses POSTGRES_* env

What gets translated automatically:
  - `?` placeholders → `%s` (psycopg2 format)
  - `INSERT OR IGNORE INTO ...` → `INSERT INTO ... ON CONFLICT DO NOTHING`
  - `PRAGMA ...` → no-op
  - `CREATE TABLE IF NOT EXISTS` → unchanged (PG supports it)
  - `CREATE INDEX IF NOT EXISTS` → unchanged (PG supports it)
  - `ALTER TABLE ... ADD COLUMN ...` → unchanged

What works automatically (no translation needed):
  - Both `row[0]` index access and `row['col_name']` dict access (DictCursor)
  - `cursor.fetchone()`, `cursor.fetchall()`, `cursor.rowcount`
  - `conn.execute(sql, params)` returns a cursor (sqlite3-style)
  - `conn.executescript(...)` for multi-statement schema definitions
  - `conn.commit()`, `conn.rollback()`, `conn.close()`
  - `with conn:` context manager
  - `conn.row_factory = sqlite3.Row` (no-op — DictCursor is always used)

What does NOT work (these would need code changes if used):
  - SQLite-specific functions like `julianday()`, `random()` (use PG equivalents)
  - `LAST_INSERT_ROWID()` (use `RETURNING id` clause instead)
  - `INSERT OR REPLACE INTO` (use `INSERT ... ON CONFLICT ... DO UPDATE`)

Connection details:
  - Reads POSTGRESQL_HOST/PORT/POSTGRES_USER/PASSWORD/DB from env at connect time
  - Password is URL-encoded in env (e.g. '%40' for '@'); decoded for psycopg2 kwargs
  - Each `connect()` call creates a fresh psycopg2 connection (no pooling here —
    let the Supabase pooler handle that)
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3 as _sqlite3
from typing import Any, Optional, Sequence
from urllib.parse import unquote

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ── sqlite3 module-level compat ────────────────────────────────────────


# Preserve sqlite3.Row semantics when the shim falls back to real SQLite for
# explicit in-memory/temp test databases. PostgreSQL connections still ignore
# row_factory because DictCursor already supports both index and dict access.
Row = _sqlite3.Row


# Re-export the most common error class so `except sqlite3.OperationalError` works.
OperationalError = psycopg2.OperationalError
IntegrityError = psycopg2.IntegrityError
DatabaseError = psycopg2.DatabaseError
Error = psycopg2.Error


# ── SQL translation ────────────────────────────────────────────────────

# Match `?` placeholders, but skip ones inside string literals.
# Simple heuristic: split on string boundaries, only replace outside strings.
_QUOTE_SPLIT = re.compile(r"('[^']*')")
_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_PRAGMA = re.compile(r"^\s*PRAGMA\b", re.IGNORECASE)


def _translate_sql(sql: str) -> str:
    """Translate SQLite SQL to PostgreSQL.

    Operations:
      - `?` outside string literals → `%s`
      - `INSERT OR IGNORE INTO` → `INSERT INTO ... ON CONFLICT DO NOTHING`
      - `PRAGMA *` → `SELECT 1` (no-op)
    """
    # PRAGMA is SQLite-only; turn into a no-op
    if _PRAGMA.match(sql):
        return "SELECT 1"

    # Translate INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING.
    has_insert_or_ignore = bool(_INSERT_OR_IGNORE.search(sql))
    if has_insert_or_ignore:
        sql = _INSERT_OR_IGNORE.sub("INSERT INTO", sql)

    # Replace `?` placeholders with `%s`, but only outside string literals.
    parts = _QUOTE_SPLIT.split(sql)
    for i in range(0, len(parts), 2):  # even indices are outside quotes
        parts[i] = parts[i].replace("?", "%s")
    sql = "".join(parts)

    if has_insert_or_ignore:
        # Append the conflict clause (works for any conflict — primary key or unique)
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    return sql


def _split_script(script: str) -> list[str]:
    """Split a multi-statement SQL script on semicolons, respecting string literals."""
    statements: list[str] = []
    buf: list[str] = []
    in_string = False
    in_dollar = False
    dollar_tag = ""
    i = 0
    while i < len(script):
        c = script[i]
        if not in_string and not in_dollar and c == "$":
            # Check for $$ or $tag$ dollar quotes
            end = script.find("$", i + 1)
            if end != -1:
                tag_candidate = script[i : end + 1]
                if re.match(r"^\$[A-Za-z_]*\$$", tag_candidate):
                    in_dollar = True
                    dollar_tag = tag_candidate
                    buf.append(tag_candidate)
                    i = end + 1
                    continue
        elif in_dollar:
            if script[i : i + len(dollar_tag)] == dollar_tag:
                in_dollar = False
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        elif c == "'" and not in_string:
            in_string = True
        elif c == "'" and in_string:
            in_string = False

        if c == ";" and not in_string and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(c)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


# ── Connection / Cursor wrappers ───────────────────────────────────────


class CompatCursor:
    """psycopg2 DictCursor with sqlite3.Cursor-compatible API."""

    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    def execute(self, sql: str, params: Sequence[Any] | dict | None = ()) -> "CompatCursor":
        translated = _translate_sql(sql)
        if params is None or (hasattr(params, "__len__") and len(params) == 0):
            self._cur.execute(translated)
        else:
            self._cur.execute(translated, tuple(params) if not isinstance(params, dict) else params)
        return self

    def executescript(self, script: str) -> "CompatCursor":
        for stmt in _split_script(script):
            translated = _translate_sql(stmt)
            self._cur.execute(translated)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size: int = 1):
        return self._cur.fetchmany(size)

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self) -> Optional[int]:
        # psycopg2 doesn't have lastrowid; use RETURNING in queries instead.
        return None

    def close(self) -> None:
        self._cur.close()

    def __iter__(self):
        return iter(self._cur)


class CompatConnection:
    """psycopg2 Connection with sqlite3.Connection-compatible API."""

    def __init__(self) -> None:
        host = os.environ["POSTGRESQL_HOST"]
        port = os.environ["POSTGRESQL_PORT"]
        user = os.environ["POSTGRES_USER"]
        password = unquote(os.environ["POSTGRES_PASSWORD"])  # decode URL escapes
        dbname = os.environ["POSTGRES_DB"]
        self._raw = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
            connect_timeout=10,
        )
        # Default to autocommit OFF (matches sqlite3 default — explicit commit() needed)
        self._raw.autocommit = False
        # row_factory is a no-op marker; we always use DictCursor
        self.row_factory: Any = None

    def execute(self, sql: str, params: Sequence[Any] | dict | None = ()) -> CompatCursor:
        cursor = self.cursor()
        cursor.execute(sql, params)
        return cursor

    def executescript(self, script: str) -> CompatCursor:
        cursor = self.cursor()
        cursor.executescript(script)
        return cursor

    def cursor(self, *args, **kwargs) -> CompatCursor:
        # Always use DictCursor so row['col'] and row[0] both work
        raw = self._raw.cursor(cursor_factory=psycopg2.extras.DictCursor)
        return CompatCursor(raw)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass

    def __enter__(self) -> "CompatConnection":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        # Note: sqlite3 keeps the connection open after context exit; we match that.


# ── Module-level connect() ─────────────────────────────────────────────


_PG_ENV_KEYS = (
    "POSTGRESQL_HOST",
    "POSTGRESQL_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)


def _postgres_env_configured() -> bool:
    return all(os.environ.get(key) for key in _PG_ENV_KEYS)


def uses_sqlite_backend(db_path: Optional[str] = None) -> bool:
    """Return true when connect() will honor db_path with real sqlite3."""
    return db_path is not None and (db_path == ":memory:" or not _postgres_env_configured())


def _sqlite_connect(db_path: str, **kwargs) -> _sqlite3.Connection:
    return _sqlite3.connect(db_path, **kwargs)


def connect(db_path: Optional[str] = None, **kwargs) -> CompatConnection | _sqlite3.Connection:
    """Drop-in replacement for sqlite3.connect().

    The db_path argument is accepted for API compatibility but ignored.
    Connection details come from POSTGRESQL_HOST/PORT/POSTGRES_USER/PASSWORD/DB.
    """
    if uses_sqlite_backend(db_path):
        return _sqlite_connect(db_path, **kwargs)

    if db_path is not None:
        logger.debug("_pg_compat.connect ignoring db_path=%s, using POSTGRES_* env", db_path)
    return CompatConnection()


# Type aliases for sqlite3.Connection / sqlite3.Cursor compatibility.
# Some calling code uses these in type annotations.
Connection = CompatConnection
Cursor = CompatCursor


# ── JSON column helper ────────────────────────────────────────────────


def json_loads(value: Any, default: Any = None) -> Any:
    """Safely decode a column value that may be a JSON string OR already-decoded.

    Legacy SQLite stored JSON as TEXT, so the batch/session code calls
    `json.loads(row["col"])`. PostgreSQL with JSONB auto-decodes to Python
    dict/list in psycopg2, so `json.loads(dict)` would crash.

    This helper handles both transparently:
      - str  → json.loads (legacy path)
      - dict → return as-is (PG JSONB path)
      - list → return as-is (PG JSONB path)
      - None/empty → return default (typically {} or [])

    Usage in row_to_response methods:
        from openeo_ai._pg_compat import json_loads
        spatial_extent = json_loads(row["spatial_extent"], default={})
    """
    import json as _json

    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return _json.loads(value.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return default
    if isinstance(value, str):
        try:
            return _json.loads(value)
        except (ValueError, _json.JSONDecodeError):
            return default
    return default
