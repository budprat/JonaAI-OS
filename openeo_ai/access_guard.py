# ABOUTME: Checks the Cloudflare Access token on requests that came through the tunnel,
# ABOUTME: so a request can't reach the app on the strength of a header merely being present.
"""Cloudflare Access validation.

JONA_ACCESS_MODE is off (default), observe (log only), or enforce. nginx proves only that
the header exists; this proves Cloudflare signed it for this application. Requests with no
visitor header came from this host, such as tools calling the job API, and are not checked.
"""

import asyncio
import logging
import os
from typing import Optional

import jwt

logger = logging.getLogger(__name__)

_jwks_clients: dict = {}


def _claims(token: str, team_domain: str, audience: str) -> Optional[dict]:
    """The token's claims if Cloudflare signed it for this application, else None."""
    issuer = f"https://{team_domain}"
    client = _jwks_clients.get(issuer)
    if client is None:
        client = _jwks_clients[issuer] = jwt.PyJWKClient(f"{issuer}/cdn-cgi/access/certs", cache_keys=True)
    try:
        key = client.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=issuer)
    except Exception as exc:  # any failure means the token proves nothing
        logger.warning(f"[access] token rejected: {type(exc).__name__}")
        return None


class AccessGuard:
    """ASGI wrapper. Covers WebSockets too, which HTTP middleware doesn't see."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        mode = os.environ.get("JONA_ACCESS_MODE", "off").lower()
        if mode not in ("observe", "enforce") or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])}
        if not headers.get("cf-connecting-ip") and not headers.get("x-forwarded-for"):
            return await self.app(scope, receive, send)

        team = os.environ.get("JONA_ACCESS_TEAM_DOMAIN", "")
        audience = os.environ.get("JONA_ACCESS_AUD", "")
        token = headers.get("cf-access-jwt-assertion", "")
        valid = bool(team and audience and token and await asyncio.to_thread(_claims, token, team, audience))
        if mode == "observe":
            logger.info(f"[access] observe: token {'valid' if valid else 'INVALID'} for {scope.get('path', '')[:40]}")
        elif not valid:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"Forbidden\n"})
            return
        await self.app(scope, receive, send)
