# ABOUTME: Admin authentication for the OpenEO AI web interface.
# Token-based auth with constant-time comparison, rate limiting, and automatic cleanup.

import hmac
import logging
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Admin Authentication ──
_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")  # Empty = login disabled if unset
_MAX_ADMIN_TOKENS = 100
_admin_tokens: Dict[str, datetime] = {}

# ── Login Rate Limiting ──
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 300  # 5 minutes
_failed_attempts: Dict[str, List[float]] = defaultdict(list)


def _check_login_rate(ip: str) -> bool:
    """Check if IP is rate-limited for login attempts.

    Returns True if allowed, False if rate-limited.
    """
    now = time.time()
    cutoff = now - _LOGIN_LOCKOUT_SECONDS

    # Clean old entries
    _failed_attempts[ip] = [t for t in _failed_attempts[ip] if t > cutoff]

    if len(_failed_attempts[ip]) >= _MAX_LOGIN_ATTEMPTS:
        return False
    return True


def _record_failed_login(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    _failed_attempts[ip].append(time.time())


def verify_token(token: str) -> bool:
    """Verify an admin token is valid. Also sweeps expired tokens."""
    now = datetime.utcnow()
    expired = [t for t, created in _admin_tokens.items()
               if (now - created).total_seconds() >= 86400]
    for t in expired:
        del _admin_tokens[t]
    # Cap token count to prevent memory exhaustion
    if len(_admin_tokens) > _MAX_ADMIN_TOKENS:
        oldest = sorted(_admin_tokens, key=_admin_tokens.get)[:len(_admin_tokens) - _MAX_ADMIN_TOKENS]
        for t in oldest:
            del _admin_tokens[t]

    if token in _admin_tokens:
        created = _admin_tokens[token]
        if (now - created).total_seconds() < 86400:
            return True
        del _admin_tokens[token]
    return False


def get_token_username(token: str) -> Optional[str]:
    """Return the username associated with a valid token, or None if invalid.

    Currently all valid tokens map to the single admin user.
    """
    if verify_token(token):
        return _ADMIN_USERNAME
    return None


@router.post("/login")
async def admin_login(request: Request):
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")

    # Rate limiting by IP
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(client_ip):
        logger.warning(f"Login rate limit exceeded for IP: {client_ip}")
        return JSONResponse(
            {"ok": False, "error": "Too many login attempts. Try again later."},
            status_code=429,
        )

    if not _ADMIN_PASSWORD:
        return JSONResponse({"ok": False, "error": "Admin login disabled (ADMIN_PASSWORD not set)"}, status_code=403)
    if hmac.compare_digest(username, _ADMIN_USERNAME) and hmac.compare_digest(password, _ADMIN_PASSWORD):
        token = secrets.token_hex(32)
        _admin_tokens[token] = datetime.utcnow()
        return {"ok": True, "token": token, "username": username}

    _record_failed_login(client_ip)
    return JSONResponse({"ok": False, "error": "Invalid credentials"}, status_code=401)


@router.post("/logout")
async def admin_logout(request: Request):
    auth = request.headers.get("authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    _admin_tokens.pop(token, None)
    return {"ok": True}


@router.get("/me")
async def admin_me(request: Request):
    auth = request.headers.get("authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    if verify_token(token):
        return {"ok": True, "username": _ADMIN_USERNAME}
    return JSONResponse({"ok": False}, status_code=401)
