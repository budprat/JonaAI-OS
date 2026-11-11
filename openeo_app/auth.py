"""Custom authentication for OpenEO with development bypass."""

import hmac
import os
import uuid
import logging
import base64
from typing import Optional

from fastapi import Header, HTTPException

from openeo_fastapi.client.auth import Authenticator, User, IssuerHandler
from openeo_fastapi.client.psql.engine import get_first_or_default, create, Filter
from openeo_fastapi.client.settings import AppSettings

logger = logging.getLogger(__name__)

# Development mode configuration
DEV_MODE = os.environ.get("OPENEO_DEV_MODE", "false").lower() == "true"
DEV_USER_ID = os.environ.get("OPENEO_DEV_USER_ID", "dev-user-001")
DEV_PASSWORD = os.environ.get("OPENEO_DEV_PASSWORD", "")

if DEV_MODE:
    logger.warning("Running in DEVELOPMENT MODE - Basic Auth enabled!")
    # Warn if binding to non-localhost in dev mode
    bind_host = os.environ.get("UVICORN_HOST", os.environ.get("HOST", ""))
    if bind_host and bind_host not in ("localhost", "127.0.0.1", "::1", ""):
        logger.critical(
            f"DEV_MODE is active but server binds to {bind_host}. "
            "This is unsafe — dev mode should only run on localhost."
        )


class DevAuthenticator(Authenticator):
    """
    Custom authenticator that supports:
    1. Development mode with Basic Auth bypass
    2. Production OIDC authentication
    """

    @staticmethod
    def validate(
        authorization: str = Header(),
        cf_connecting_ip: Optional[str] = Header(default=None),
        x_forwarded_for: Optional[str] = Header(default=None),
    ) -> User:
        """
        Validate authorization and return user.

        In dev mode: Accepts Basic Auth with any credentials
        In prod mode: Requires valid OIDC Bearer token
        """
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail="Authorization header required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check for Basic Auth in dev mode
        if DEV_MODE and authorization.lower().startswith("basic "):
            # This login names any user, so it's for this host's own tools only.
            if cf_connecting_ip or x_forwarded_for:
                raise HTTPException(status_code=401, detail="Basic Auth is not accepted through the proxy")
            return DevAuthenticator._handle_basic_auth(authorization)

        # Check for Bearer token (OIDC)
        if authorization.lower().startswith("bearer "):
            return DevAuthenticator._handle_bearer_auth(authorization)

        raise HTTPException(
            status_code=401,
            detail="Invalid authorization scheme. Use Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @staticmethod
    def _handle_basic_auth(authorization: str) -> User:
        """Handle Basic Auth for development mode."""
        if not DEV_MODE:
            raise HTTPException(
                status_code=401,
                detail="Basic Auth only allowed in development mode",
            )

        try:
            encoded = authorization.split(" ", 1)[1]
            decoded = base64.b64decode(encoded).decode("utf-8")
            username, password = decoded.split(":", 1)

            # Validate password even in dev mode
            if DEV_PASSWORD:
                if not hmac.compare_digest(password, DEV_PASSWORD):
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid Basic Auth credentials",
                    )
            elif not password:
                # Reject empty passwords when no DEV_PASSWORD is configured
                raise HTTPException(
                    status_code=401,
                    detail="Empty password not allowed. Set OPENEO_DEV_PASSWORD env var.",
                )

            logger.info(f"[DEV MODE] Basic Auth login: {username}")

            dev_user_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"dev-{username}")
            oidc_sub = f"dev:{username}"

            user = User(user_id=dev_user_id, oidc_sub=oidc_sub)
            logger.info(f"[DEV MODE] Authenticated user: {user.user_id}")
            return user

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Basic Auth failed: {e}")
            raise HTTPException(
                status_code=401,
                detail="Invalid Basic Auth credentials",
            )

    @staticmethod
    def _handle_bearer_auth(authorization: str) -> User:
        """Handle Bearer token (OIDC) authentication."""
        settings = AppSettings()

        try:
            policies = None
            if settings.OIDC_POLICIES:
                policies = settings.OIDC_POLICIES

            issuer = IssuerHandler(issuer_uri=settings.OIDC_URL, policies=policies)
            user_info = issuer.validate_token(authorization)

            # Check if user exists
            found_user = get_first_or_default(
                User, Filter(column_name="oidc_sub", value=user_info["sub"])
            )

            if found_user:
                return found_user

            # Create new user
            user = User(user_id=uuid.uuid4(), oidc_sub=user_info["sub"])
            create(create_object=user)

            return user

        except Exception as e:
            # Log full error internally but don't expose details to client
            logger.error(f"OIDC validation failed: {e}")
            raise HTTPException(
                status_code=401,
                detail="Invalid Bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )


def get_auth_dependency():
    """Get the appropriate authentication dependency."""
    if DEV_MODE:
        logger.warning("Running in DEVELOPMENT MODE - Basic Auth enabled!")
    return DevAuthenticator.validate
