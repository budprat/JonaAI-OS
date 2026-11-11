# ABOUTME: API authentication middleware for Jona AI REST API v1.
# Supports API key auth (X-API-Key header) and JWT Bearer tokens.
# Includes rate limiting per client with tiered quotas.

"""
Authentication and authorization for the Jona AI REST API.

Supports two authentication modes:
1. API Key: Header `X-API-Key: jona_sk_live_...` (server-to-server)
2. JWT Bearer: Header `Authorization: Bearer eyJ...` (user-scoped)

Rate limiting tiers:
- free:       100 requests/day,   2 concurrent analyses
- pro:      10,000 requests/day,  10 concurrent analyses
- enterprise: 100,000 requests/day, 50 concurrent analyses
"""

import hashlib
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, Header, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from .schemas import ProblemDetail, RateLimitTier

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────

_JWT_SECRET = os.environ.get("JONA_JWT_SECRET", "")
_DEV_MODE = os.environ.get("OPENEO_DEV_MODE", "false").lower() == "true"
# What a signed-in person may do through this API, and so grant to a key they create.
SIGNED_IN_SCOPES = ("analyze", "results", "templates", "batch", "assets:read", "watches:read", "evidence:read", "signals:read")
if not _JWT_SECRET:
    if _DEV_MODE:
        _JWT_SECRET = secrets.token_hex(32)
        logger.warning(
            "JONA_JWT_SECRET not set — using random secret (dev mode). "
            "Tokens will be invalidated on restart."
        )
    else:
        logger.critical(
            "JONA_JWT_SECRET is not set in production mode. "
            "Set a strong secret via: export JONA_JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        )
        raise SystemExit("FATAL: JONA_JWT_SECRET required in production. Aborting.")
_JWT_ALGORITHM = "HS256"
_ACCESS_TOKEN_TTL = int(os.environ.get("JONA_ACCESS_TOKEN_TTL", "3600"))  # 1 hour
_REFRESH_TOKEN_TTL = int(os.environ.get("JONA_REFRESH_TOKEN_TTL", "604800"))  # 7 days

# Rate limit tiers: {tier: (requests_per_day, concurrent_analyses)}
RATE_LIMIT_TIERS: Dict[str, Tuple[int, int]] = {
    "free": (100, 2),
    "pro": (10_000, 10),
    "enterprise": (100_000, 50),
}

# Phase 9: _api_keys moved to PostgreSQL (api_keys table). The functions below
# use the openeo_ai._pg_compat shim to query that table with the existing
# sqlite3-style API.
#
# _refresh_tokens stays in-memory for the legacy /api/v1 JWT refresh flow (this
# is the OLD Jona API token system that pre-dates Supabase Auth). Migrating it
# to DB is a separate future phase — for now, refresh tokens just don't survive
# restarts on the /api/v1 surface. The user-facing auth flow (frontend) uses
# Supabase Auth which has its own refresh mechanism.
#
# _rate_counters stays in-memory (per-process counter, acceptable short-lived
# state; full tier enforcement lives in Phase 14 api/usage.py).
_rate_counters: Dict[str, Dict[str, Any]] = {}
_refresh_tokens: Dict[str, Dict[str, Any]] = {}  # token_hash -> {user_id, expires_at}

# Security schemes
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer_scheme = HTTPBearer(auto_error=False)


# ── JWT helpers ───────────────────────────────────────────────────────

def _create_jwt(payload: Dict[str, Any]) -> str:
    """Create an HS256 JWT signed with the app secret."""
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _decode_jwt(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT, or return None if invalid/expired.

    Catches only jwt.PyJWTError (bad signature, expiry, malformed) — a
    misconfigured _JWT_SECRET must still crash loudly rather than silently
    reject every token.
    """
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


# ── API Key Management ────────────────────────────────────────────────

def _hash_key(key: str) -> str:
    """Hash an API key for storage (never store plaintext)."""
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(
    name: str,
    user_id: str,
    scopes: list,
    tier: str = "free",
    expires_in_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a new API key, persist to DB, and return metadata + plaintext key.

    The plaintext raw_key is shown exactly ONCE (in the return value) and
    never stored. Only the sha256 hash is persisted.
    """
    import json as _json
    from openeo_ai import _pg_compat

    key_id = f"key_{uuid.uuid4().hex[:12]}"
    raw_key = f"jona_sk_live_{secrets.token_hex(24)}"
    key_hash = _hash_key(raw_key)
    now = datetime.utcnow()
    expires_at = now + timedelta(days=expires_in_days) if expires_in_days else None

    conn = _pg_compat.connect()
    try:
        conn.execute(
            """
            INSERT INTO api_keys
                (id, key_hash, name, user_id, prefix, scopes, tier, is_active,
                 created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?::jsonb, ?, true, ?, ?)
            """,
            (key_id, key_hash, name, user_id, raw_key[:16], _json.dumps(scopes),
             tier, now, expires_at),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": key_id,
        "name": name,
        "user_id": user_id,
        "prefix": raw_key[:16],
        "scopes": scopes,
        "tier": tier,
        "created_at": now,
        "expires_at": expires_at,
        "last_used_at": None,
        "is_active": True,
        "key": raw_key,
    }


def revoke_api_key(key_id: str, user_id: str) -> bool:
    """Revoke an API key by ID (soft delete: is_active = false)."""
    from openeo_ai import _pg_compat

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "UPDATE api_keys SET is_active = false WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_api_keys(user_id: str) -> list:
    """List all active API keys for a user (metadata only, no secret values)."""
    from openeo_ai import _pg_compat

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT id, name, prefix, scopes, tier, is_active,
                   created_at, expires_at, last_used_at
            FROM api_keys
            WHERE user_id = ? AND is_active = true
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _validate_api_key(raw_key: str) -> Optional[Dict[str, Any]]:
    """Validate an API key by its plaintext form and return metadata.

    Looks up by sha256 hash, checks is_active + expiration, and updates
    last_used_at on success.
    """
    from openeo_ai import _pg_compat

    key_hash = _hash_key(raw_key)
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT id, user_id, name, prefix, scopes, tier, is_active,
                   created_at, expires_at, last_used_at
            FROM api_keys
            WHERE key_hash = ?
            """,
            (key_hash,),
        )
        row = cursor.fetchone()
        if not row or not row["is_active"]:
            return None
        if row["expires_at"] and datetime.utcnow() > row["expires_at"].replace(tzinfo=None):
            # Auto-revoke expired keys
            conn.execute(
                "UPDATE api_keys SET is_active = false WHERE id = ?", (row["id"],)
            )
            conn.commit()
            return None
        # Update last_used_at (fire-and-forget; keep same conn for atomicity)
        conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (datetime.utcnow(), row["id"]),
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


# ── Rate Limiting ─────────────────────────────────────────────────────

def _get_rate_limit(client_id: str, tier: str = "free") -> Dict[str, Any]:
    """Get or create rate limit counter for a client."""
    now = time.time()
    day_start = now - (now % 86400)  # Start of current UTC day

    counter = _rate_counters.get(client_id)
    if not counter or counter["window_start"] < day_start:
        counter = {
            "count": 0,
            "window_start": day_start,
            "concurrent": 0,
            "tier": tier,
        }
        _rate_counters[client_id] = counter

    return counter


def check_rate_limit(client_id: str, tier: str = "free") -> Tuple[bool, Dict[str, Any]]:
    """Check if request is within rate limits. Returns (allowed, info)."""
    max_requests, max_concurrent = RATE_LIMIT_TIERS.get(tier, RATE_LIMIT_TIERS["free"])
    counter = _get_rate_limit(client_id, tier)

    remaining = max_requests - counter["count"]
    reset_at = datetime.utcfromtimestamp(counter["window_start"] + 86400)

    info = {
        "tier": tier,
        "limit": max_requests,
        "remaining": max(0, remaining),
        "reset_at": reset_at.isoformat(),
        "concurrent_limit": max_concurrent,
        "concurrent_active": counter["concurrent"],
    }

    if counter["count"] >= max_requests:
        return False, info

    counter["count"] += 1
    info["remaining"] = max(0, remaining - 1)
    return True, info


def increment_concurrent(client_id: str) -> None:
    """Track a new concurrent analysis."""
    counter = _rate_counters.get(client_id)
    if counter:
        counter["concurrent"] = counter.get("concurrent", 0) + 1


def decrement_concurrent(client_id: str) -> None:
    """Release a concurrent analysis slot."""
    counter = _rate_counters.get(client_id)
    if counter and counter.get("concurrent", 0) > 0:
        counter["concurrent"] -= 1


# ── FastAPI Dependencies ──────────────────────────────────────────────

class AuthenticatedClient:
    """Represents an authenticated API client with rate limit info."""

    def __init__(
        self,
        client_id: str,
        user_id: str,
        tier: str = "free",
        scopes: Optional[list] = None,
        auth_method: str = "api_key",
    ):
        self.client_id = client_id
        self.user_id = user_id
        self.tier = tier
        self.scopes = scopes or ["analyze", "results"]
        self.auth_method = auth_method

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes or "*" in self.scopes


async def get_authenticated_client(
    request: Request,
    api_key: Optional[str] = Security(_api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> AuthenticatedClient:
    """Authenticate the request via API key or JWT Bearer token.

    This is a FastAPI dependency injected into protected endpoints.
    """
    # Try API key first
    if api_key:
        meta = _validate_api_key(api_key)
        if meta:
            return AuthenticatedClient(
                client_id=meta["id"],
                user_id=meta["user_id"],
                tier=meta.get("tier", "free"),
                scopes=meta.get("scopes", []),
                auth_method="api_key",
            )
        raise HTTPException(
            status_code=401,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/invalid-api-key",
                title="Invalid API Key",
                status=401,
                detail="The provided API key is invalid, expired, or revoked.",
            ).model_dump(),
        )

    # Try JWT Bearer token
    if bearer:
        payload = _decode_jwt(bearer.credentials)
        if payload:
            return AuthenticatedClient(
                client_id=payload.get("sub", "unknown"),
                user_id=payload.get("sub", "unknown"),
                tier=payload.get("tier", "free"),
                scopes=payload.get("scopes", ["analyze", "results"]),
                auth_method="jwt",
            )
        # Fall through to Supabase verification below. The frontend and many
        # server-to-server users present Supabase JWTs, which are intentionally
        # not signed with JONA_JWT_SECRET.

    # Also check Supabase JWT (primary auth path for v1 API)
    # Replaces the legacy admin token check from web_auth.py
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        from ..supabase_auth import verify_supabase_token
        token = auth_header[7:]
        sb_payload = verify_supabase_token(token)
        if sb_payload:
            user_id = sb_payload["sub"]
            return AuthenticatedClient(
                client_id=user_id,
                user_id=user_id,
                tier=sb_payload.get("app_metadata", {}).get("tier", "free"),
                scopes=sb_payload.get("app_metadata", {}).get("scopes", list(SIGNED_IN_SCOPES)),
                auth_method="supabase_jwt",
            )
        raise HTTPException(
            status_code=401,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/invalid-token",
                title="Invalid or Expired Token",
                status=401,
                detail="The Bearer token is invalid, expired, or malformed.",
            ).model_dump(),
        )

    raise HTTPException(
        status_code=401,
        detail=ProblemDetail(
            type="https://api.jonaai.com/errors/authentication-required",
            title="Authentication Required",
            status=401,
            detail="Provide an API key via X-API-Key header or a JWT via Authorization: Bearer header.",
        ).model_dump(),
        headers={"WWW-Authenticate": 'Bearer realm="Jona AI API", ApiKey realm="Jona AI API"'},
    )


def require_scope(scope: str):
    """Factory for scope-checking dependencies."""
    async def _check(client: AuthenticatedClient = Depends(get_authenticated_client)):
        if not client.has_scope(scope):
            raise HTTPException(
                status_code=403,
                detail=ProblemDetail(
                    type="https://api.jonaai.com/errors/insufficient-scope",
                    title="Insufficient Permissions",
                    status=403,
                    detail=f"This endpoint requires the '{scope}' scope. Your key has: {client.scopes}",
                ).model_dump(),
            )
        return client
    return _check


def add_rate_limit_headers(response, info: Dict[str, Any]) -> None:
    """Add standard rate limit headers to a response."""
    response.headers["X-RateLimit-Limit"] = str(info["limit"])
    response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
    response.headers["X-RateLimit-Reset"] = info["reset_at"]
    response.headers["X-RateLimit-Tier"] = info["tier"]
