# ABOUTME: Supabase JWT verification for the AI web interface.
# Replaces the legacy single-admin web_auth.py with multi-user Supabase Auth.
# Verifies JWTs issued by Supabase Auth (GoTrue) using the project's JWT secret.

"""
Supabase Auth integration for JonaAI.

Provides JWT verification, FastAPI dependencies, and user identity extraction
for both HTTP routes and WebSocket connections.

Token flow:
    1. User logs in via @supabase/auth-ui-react on the frontend
    2. Supabase issues a JWT (access_token) signed with SUPABASE_JWT_SECRET
    3. Frontend sends token in `Authorization: Bearer <token>` header
       OR via `?token=<token>` query param (img/Leaflet endpoints)
       OR via WebSocket message `{type: "authenticate", token: "..."}`
    4. Backend verifies signature + expiration with verify_supabase_token()
    5. Authenticated requests get the JWT payload (sub = user UUID, email, etc.)

Environment variables:
    SUPABASE_JWT_SECRET: HS256 secret from Supabase Dashboard → API → JWT Settings
    SUPABASE_URL:        Project URL (used for issuer validation, optional)

References:
    https://supabase.com/docs/guides/auth/jwts
    https://github.com/supabase/auth (GoTrue)
"""

import logging
import os
from typing import Any, Dict, Optional

import jwt  # PyJWT (already in requirements-local.txt)
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
# Algorithms we accept. Supabase 2025+ moved projects to asymmetric ES256/RS256
# via JWKS; legacy projects still issue HS256 tokens via the shared JWT secret.
# We try asymmetric (JWKS) first, fall back to symmetric (shared secret).
_ASYMMETRIC_ALGORITHMS = ["ES256", "RS256", "EdDSA"]
_SYMMETRIC_ALGORITHM = "HS256"
_JWT_AUDIENCE = "authenticated"  # Supabase default audience for logged-in users

# Lazy-initialized JWKS client. Created on first verify call and cached process-
# wide. PyJWKClient internally caches signing keys with a 24h TTL and retries
# on kid-miss (handles Supabase key rotation automatically).
_jwks_client: Optional["jwt.PyJWKClient"] = None


def _get_jwks_client() -> Optional["jwt.PyJWKClient"]:
    """Return a cached PyJWKClient pointed at Supabase's JWKS endpoint.

    Returns None if SUPABASE_URL is not set or the endpoint is unreachable.
    """
    global _jwks_client
    if _jwks_client is not None:
        return _jwks_client
    if not SUPABASE_URL:
        return None
    try:
        jwks_url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
        logger.info(f"[supabase-auth] JWKS client initialized: {jwks_url}")
        return _jwks_client
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[supabase-auth] JWKS init failed: {exc}")
        return None


if not SUPABASE_JWT_SECRET and not SUPABASE_URL:
    if os.environ.get("OPENEO_DEV_MODE", "").lower() == "true":
        logger.warning(
            "Neither SUPABASE_JWT_SECRET nor SUPABASE_URL is set — "
            "Supabase auth will reject all tokens. Set SUPABASE_URL to enable "
            "JWKS-based ES256/RS256 verification (recommended) or "
            "SUPABASE_JWT_SECRET for legacy HS256 projects."
        )
    else:
        logger.critical(
            "Neither SUPABASE_JWT_SECRET nor SUPABASE_URL is set in production mode. "
            "All authenticated requests will fail."
        )


# ── Core verification ──────────────────────────────────────────────────

def email_allowed(email: Optional[str]) -> bool:
    """True if this deployment accepts this account. With JONA_ALLOWED_EMAILS set, only those.

    Supabase signups are a dashboard setting, so an open signup alone must not grant entry.
    """
    allowed = {e.strip().lower() for e in os.environ.get("JONA_ALLOWED_EMAILS", "").split(",") if e.strip()}
    return not allowed or (email or "").strip().lower() in allowed


def verify_supabase_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a Supabase JWT, and that this deployment accepts its account."""
    payload = _verified_payload(token)
    return payload if payload and email_allowed(payload.get("email")) else None


def _verified_payload(token: str) -> Optional[Dict[str, Any]]:
    """Verify a Supabase JWT and return the payload, or None if invalid.

    Validates:
        - Signature via JWKS (ES256/RS256, asymmetric) OR
          SUPABASE_JWT_SECRET (HS256, legacy symmetric)
        - Token expiration (exp claim)
        - Audience claim (aud = "authenticated")

    Args:
        token: The raw JWT string (without "Bearer " prefix).

    Returns:
        Decoded payload dict on success (contains: sub, email, role, exp, iat, ...).
        None on any failure (invalid signature, expired, malformed, missing secret).
    """
    if not token:
        return None

    # Determine algorithm from the token header so we pick the right path.
    # Falls back to trying JWKS first, then HS256.
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")
    except Exception:  # noqa: BLE001
        alg = ""

    # Path 1: asymmetric via JWKS (Supabase 2025+ projects)
    if alg in _ASYMMETRIC_ALGORITHMS or (not alg and SUPABASE_URL):
        jwks_client = _get_jwks_client()
        if jwks_client is not None:
            try:
                signing_key = jwks_client.get_signing_key_from_jwt(token)
                payload = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=_ASYMMETRIC_ALGORITHMS,
                    audience=_JWT_AUDIENCE,
                )
                return payload
            except jwt.ExpiredSignatureError:
                logger.info("[supabase-auth] JWT expired — frontend needs to refresh session")
                return None
            except jwt.InvalidAudienceError:
                logger.warning("[supabase-auth] JWT has wrong audience (expected 'authenticated')")
                return None
            except jwt.InvalidSignatureError:
                logger.warning("[supabase-auth] JWT signature mismatch (JWKS path)")
                return None
            except jwt.PyJWKClientError as exc:
                logger.warning(f"[supabase-auth] JWKS signing key error: {exc}")
                # Fall through to HS256 attempt below in case the token is symmetric
            except jwt.InvalidTokenError as exc:
                logger.info(f"[supabase-auth] JWKS path rejected token: {type(exc).__name__}: {exc}")
                # Fall through
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[supabase-auth] JWKS verification error: {exc}")
                # Fall through

    # Path 2: symmetric HS256 (legacy projects with shared JWT secret)
    if not SUPABASE_JWT_SECRET:
        return None
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=[_SYMMETRIC_ALGORITHM],
            audience=_JWT_AUDIENCE,
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.info("[supabase-auth] JWT expired — frontend needs to refresh session")
        return None
    except jwt.InvalidAudienceError:
        logger.warning("[supabase-auth] JWT has wrong audience (expected 'authenticated')")
        return None
    except jwt.InvalidSignatureError:
        logger.warning(
            "[supabase-auth] JWT signature mismatch — frontend token was signed with "
            "a different SUPABASE_JWT_SECRET. Clear localStorage + re-login."
        )
        return None
    except jwt.InvalidTokenError as e:
        logger.info(f"[supabase-auth] Invalid JWT: {type(e).__name__}: {e}")
        return None


def get_user_id(token: str) -> Optional[str]:
    """Extract the user UUID (sub claim) from a valid Supabase JWT.

    Args:
        token: The raw JWT string.

    Returns:
        User UUID string on success, None if token invalid.
    """
    payload = verify_supabase_token(token)
    return payload.get("sub") if payload else None


def get_user_email(token: str) -> Optional[str]:
    """Extract the user email from a valid Supabase JWT.

    Args:
        token: The raw JWT string.

    Returns:
        Email string on success, None if token invalid or email missing.
    """
    payload = verify_supabase_token(token)
    return payload.get("email") if payload else None


# ── FastAPI dependencies ───────────────────────────────────────────────

def _extract_bearer_token(request: Request) -> str:
    """Extract Bearer token from Authorization header. Returns empty string if absent."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


async def require_auth(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: require a valid Supabase JWT.

    Raises HTTPException 401 on missing/invalid token.

    Returns:
        The decoded JWT payload (dict with sub, email, role, etc.).
        Use payload["sub"] to get the user UUID for data ownership.

    Usage:
        @router.get("/me")
        async def me(user: dict = Depends(require_auth)):
            return {"user_id": user["sub"], "email": user.get("email")}
    """
    token = _extract_bearer_token(request)
    payload = verify_supabase_token(token)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def require_auth_or_query_token(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: accept JWT from Authorization header OR ?token= query param.

    Needed for endpoints loaded via <img src="..."> or Leaflet tiles, where the
    browser cannot send Authorization headers. Query-param tokens are less secure
    (logged in proxies/Referer) — use only for resources where auth headers can't
    be set.

    Raises HTTPException 401 on missing/invalid token.

    Returns:
        The decoded JWT payload.
    """
    # Try Authorization header first (preferred)
    token = _extract_bearer_token(request)
    if token:
        payload = verify_supabase_token(token)
        if payload:
            return payload

    # Fallback: query param (for <img>, Leaflet, etc.)
    qt = request.query_params.get("token", "")
    if qt:
        payload = verify_supabase_token(qt)
        if payload:
            return payload

    raise HTTPException(
        status_code=401,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Convenience: backward-compat shim for legacy code ─────────────────

def verify_token(token: str) -> bool:
    """Legacy compat shim: returns True if token is a valid Supabase JWT.

    DEPRECATED: Use verify_supabase_token() to get the full payload.
    Kept for backward compat with code that imports from the old web_auth module.
    """
    return verify_supabase_token(token) is not None


def get_token_username(token: str) -> Optional[str]:
    """Legacy compat shim: returns the user email (or sub UUID) for a valid token.

    DEPRECATED: Use get_user_id() or get_user_email() directly.
    Kept for backward compat with code that imports from the old web_auth module.
    """
    payload = verify_supabase_token(token)
    if not payload:
        return None
    return payload.get("email") or payload.get("sub")
