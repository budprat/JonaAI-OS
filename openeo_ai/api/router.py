# ABOUTME: FastAPI router for Jona AI REST API v1.
# Implements authentication, analysis, templates, results, and batch endpoints.

"""
Jona AI REST API v1 Router

Endpoints:
    POST   /api/v1/auth/token          - Get access token (JWT)
    POST   /api/v1/auth/refresh         - Refresh access token
    POST   /api/v1/auth/keys            - Create API key
    GET    /api/v1/auth/keys            - List API keys
    DELETE /api/v1/auth/keys/{key_id}   - Revoke API key

    POST   /api/v1/analyze              - Submit analysis (async)
    GET    /api/v1/analyze/{job_id}     - Get job status
    POST   /api/v1/analyze/sync         - Submit analysis (sync, blocking)
    DELETE /api/v1/analyze/{job_id}     - Cancel analysis
    GET    /api/v1/analyze/{job_id}/events - SSE stream for job progress

    GET    /api/v1/templates            - List templates
    GET    /api/v1/templates/{id}       - Get template
    POST   /api/v1/templates/{id}/execute - Execute template

    GET    /api/v1/results/{job_id}/data          - Download result data
    GET    /api/v1/results/{job_id}/report         - Download HTML report
    GET    /api/v1/results/{job_id}/process-graph  - Get process graph
    GET    /api/v1/results/{job_id}/metrics        - Get quality metrics

    POST   /api/v1/batch               - Submit batch analysis
    GET    /api/v1/batch/{batch_id}     - Get batch status
"""

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from urllib.parse import urlparse

from .auth import (
    require_scope,
    AuthenticatedClient,
    add_rate_limit_headers,
    check_rate_limit,
    create_api_key,
    decrement_concurrent,
    get_authenticated_client,
    increment_concurrent,
    list_api_keys,
    revoke_api_key,
    _create_jwt,
    _decode_jwt,
    _ACCESS_TOKEN_TTL,
    _REFRESH_TOKEN_TTL,
    _refresh_tokens,
    RATE_LIMIT_TIERS,
)
from .jobs import job_manager
from .schemas import (
    AnalyzeRequest,
    AnalyzeSyncRequest,
    APIKeyCreate,
    APIKeyInfo,
    APIKeyResponse,
    BatchRequest,
    BatchStatusResponse,
    JobResponse,
    JobStatus,
    JobStatusResponse,
    ProblemDetail,
    RefreshTokenRequest,
    TemplateExecuteRequest,
    TemplateInfo,
    TokenRequest,
    TokenResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Jona AI API v1"])

# A key created without an expiry gets this one (the schema caps any request at 365).
DEFAULT_KEY_EXPIRY_DAYS = 90


# ══════════════════════════════════════════════════════════════════════
#  AUTHENTICATION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


def _check_concurrent_limit(client: AuthenticatedClient, rate_info: Dict[str, Any], starting: int = 1) -> None:
    """Refuse with 429 if starting this many analyses would pass the tier's concurrent limit.

    Every route that starts agent analyses checks this: /analyze, /analyze/sync, template
    execution, and /batch, which starts one per location.
    """
    _, max_concurrent = RATE_LIMIT_TIERS.get(client.tier, (100, 2))
    active = rate_info["concurrent_active"]
    if active + starting > max_concurrent:
        asked = f" and this request would start {starting} more" if starting > 1 else ""
        raise HTTPException(
            status_code=429,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/concurrent-limit",
                title="Concurrent Analysis Limit Reached",
                status=429,
                detail=f"You have {active} analyses running{asked}. Maximum for {client.tier} tier: {max_concurrent}.",
            ).model_dump(),
        )

@router.post(
    "/auth/token",
    response_model=TokenResponse,
    summary="Get access token",
    description="Authenticate with username/password to receive a JWT access token and refresh token.",
    tags=["Authentication"],
)
async def create_token(body: TokenRequest):
    # Validate against admin credentials (extend for multi-user)
    admin_user = os.environ.get("ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")

    if not admin_pass:
        raise HTTPException(
            status_code=503,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/auth-not-configured",
                title="Authentication Not Configured",
                status=503,
                detail="Set ADMIN_PASSWORD environment variable to enable authentication.",
            ).model_dump(),
        )

    if not (
        hmac.compare_digest(body.username, admin_user)
        and hmac.compare_digest(body.password, admin_pass)
    ):
        raise HTTPException(
            status_code=401,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/invalid-credentials",
                title="Invalid Credentials",
                status=401,
                detail="The username or password is incorrect.",
            ).model_dump(),
        )

    now = time.time()
    access_payload = {
        "sub": body.username,
        "iat": int(now),
        "exp": int(now + _ACCESS_TOKEN_TTL),
        "tier": "enterprise",
        "scopes": [
            "analyze",
            "results",
            "templates",
            "batch",
            "assets:read",
            "assets:write",
            "watches:read",
            "watches:write",
            "observations:read",
            "evidence:read",
            "signals:read",
            "alerts:write",
            "admin",
        ],
    }
    access_token = _create_jwt(access_payload)

    # Create refresh token
    import hashlib
    import secrets
    refresh_raw = secrets.token_hex(32)
    refresh_hash = hashlib.sha256(refresh_raw.encode()).hexdigest()
    _refresh_tokens[refresh_hash] = {
        "user_id": body.username,
        "expires_at": datetime.utcnow() + timedelta(seconds=_REFRESH_TOKEN_TTL),
    }

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=_ACCESS_TOKEN_TTL,
        refresh_token=refresh_raw,
    )


@router.post(
    "/auth/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
    tags=["Authentication"],
)
async def refresh_token(body: RefreshTokenRequest):
    import hashlib
    refresh_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    token_data = _refresh_tokens.get(refresh_hash)

    if not token_data:
        raise HTTPException(
            status_code=401,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/invalid-refresh-token",
                title="Invalid Refresh Token",
                status=401,
                detail="The refresh token is invalid or has been revoked.",
            ).model_dump(),
        )

    if datetime.utcnow() > token_data["expires_at"]:
        del _refresh_tokens[refresh_hash]
        raise HTTPException(
            status_code=401,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/expired-refresh-token",
                title="Expired Refresh Token",
                status=401,
                detail="The refresh token has expired. Please re-authenticate.",
            ).model_dump(),
        )

    # Issue new access token
    now = time.time()
    access_payload = {
        "sub": token_data["user_id"],
        "iat": int(now),
        "exp": int(now + _ACCESS_TOKEN_TTL),
        "tier": "enterprise",
        "scopes": [
            "analyze",
            "results",
            "templates",
            "batch",
            "assets:read",
            "assets:write",
            "watches:read",
            "watches:write",
            "observations:read",
            "evidence:read",
            "signals:read",
            "alerts:write",
            "admin",
        ],
    }

    # Rotate refresh token
    del _refresh_tokens[refresh_hash]
    import secrets
    new_refresh = secrets.token_hex(32)
    new_hash = hashlib.sha256(new_refresh.encode()).hexdigest()
    _refresh_tokens[new_hash] = {
        "user_id": token_data["user_id"],
        "expires_at": datetime.utcnow() + timedelta(seconds=_REFRESH_TOKEN_TTL),
    }

    return TokenResponse(
        access_token=_create_jwt(access_payload),
        token_type="bearer",
        expires_in=_ACCESS_TOKEN_TTL,
        refresh_token=new_refresh,
    )


@router.post(
    "/auth/keys",
    response_model=APIKeyResponse,
    summary="Create API key",
    tags=["Authentication"],
)
async def create_key(
    body: APIKeyCreate,
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    # A key can't grant what its creator doesn't have.
    # With none named, a key gets its creator's scopes rather than the schema's default list.
    requested = body.scopes if "scopes" in body.model_fields_set else list(client.scopes)
    missing = [scope for scope in (requested or []) if not client.has_scope(scope)]
    if missing:
        raise HTTPException(
            status_code=403,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/insufficient-scope",
                title="Insufficient Permissions",
                status=403,
                detail=f"You can't grant scopes you don't have: {', '.join(missing)}",
            ).model_dump(),
        )
    # Every key expires, so a leaked key can't mint one that outlives any revocation.
    result = create_api_key(
        name=body.name,
        user_id=client.user_id,
        scopes=requested,
        tier=client.tier,
        expires_in_days=body.expires_in_days or DEFAULT_KEY_EXPIRY_DAYS,
    )
    return APIKeyResponse(
        id=result["id"],
        name=result["name"],
        key=result["key"],
        prefix=result["prefix"],
        scopes=result["scopes"],
        created_at=result["created_at"],
        expires_at=result.get("expires_at"),
    )


@router.get(
    "/auth/keys",
    response_model=List[APIKeyInfo],
    summary="List API keys",
    tags=["Authentication"],
)
async def list_keys(
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    keys = list_api_keys(client.user_id)
    return [
        APIKeyInfo(
            id=k["id"],
            name=k["name"],
            prefix=k["prefix"],
            scopes=k["scopes"],
            created_at=k["created_at"],
            expires_at=k.get("expires_at"),
            last_used_at=k.get("last_used_at"),
            is_active=k.get("is_active", True),
        )
        for k in keys
    ]


@router.delete(
    "/auth/keys/{key_id}",
    summary="Revoke API key",
    tags=["Authentication"],
)
async def delete_key(
    key_id: str,
    client: AuthenticatedClient = Depends(get_authenticated_client),
):
    # A key may revoke only itself; revoking a user's other keys takes a signed-in session.
    if client.auth_method == "api_key" and key_id != client.client_id:
        raise HTTPException(
            status_code=403,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/insufficient-scope",
                title="Insufficient Permissions",
                status=403,
                detail="An API key can revoke only itself. Sign in to revoke your other keys.",
            ).model_dump(),
        )
    if not revoke_api_key(key_id, client.user_id):
        raise HTTPException(
            status_code=404,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/key-not-found",
                title="API Key Not Found",
                status=404,
                detail=f"No active API key found with ID '{key_id}'.",
            ).model_dump(),
        )
    return {"deleted": True, "key_id": key_id}


# ══════════════════════════════════════════════════════════════════════
#  ANALYSIS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

async def _execute_analysis(job: Dict[str, Any], app_state) -> None:
    """Background task that runs the AI analysis pipeline for a job.

    Bridges into the existing Claude SDK bridge or Anthropic client path.
    """
    job_id = job["job_id"]
    start_time = time.time()

    try:
        job["status"] = "running"
        job["started_at"] = datetime.utcnow()
        job["current_step"] = "Initializing AI analysis pipeline"
        job["progress"] = 5.0

        # Build the query with optional bbox context
        query = job["query"]
        bbox = job.get("bbox")
        if bbox:
            query = (
                f"[Bounding box: west={bbox['west']}, south={bbox['south']}, "
                f"east={bbox['east']}, north={bbox['north']}] {query}"
            )
        temporal = job.get("temporal_extent")
        if temporal:
            query = f"[Time range: {temporal['start']} to {temporal['end']}] {query}"

        # Use the SDK bridge (same path as WebSocket)
        from ..sdk.client import OpenEOAIClient, OpenEOAIConfig
        from ..sdk.claude_sdk_bridge import ClaudeSDKBridge

        if not hasattr(app_state, "sdk_bridge"):
            config = OpenEOAIConfig()
            app_state.sdk_bridge = ClaudeSDKBridge(config=config)
        bridge = app_state.sdk_bridge

        # Create a collector that captures results instead of sending to WebSocket
        collector = _ResultCollector(job)
        stop_event = job.get("_stop_event", asyncio.Event())

        session_id = f"api_{job_id}"

        job["current_step"] = "Processing query with AI"
        job["progress"] = 15.0

        await bridge.query(
            prompt=query,
            websocket=collector,
            session_id=session_id,
            stop_event=stop_event,
            active_project_id=job.get("project_id"),
            # Tenant identity MUST be threaded through: in-process tools
            # (saved_jobs, monitor_*) scope by it. Without it they run unscoped.
            auth_user_id=job.get("user_id"),
        )

        # A refused or failed query that produced nothing has not completed.
        if collector.error and not collector.text_content:
            raise RuntimeError(collector.error)

        # Mark completed
        elapsed_ms = int((time.time() - start_time) * 1000)
        job["status"] = "completed"
        job["completed_at"] = datetime.utcnow()
        job["progress"] = 100.0
        job["current_step"] = None
        job["execution_time_ms"] = elapsed_ms
        job["result_summary"] = collector.text_content or "Analysis completed"

        # Build data download URLs from saved results
        if collector.save_ids:
            for save_id in collector.save_ids:
                job["data_urls"][f"geotiff_{save_id}"] = f"/saved-jobs/{save_id}/files/"

        logger.info(f"Job {job_id} completed in {elapsed_ms}ms")

    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["completed_at"] = datetime.utcnow()
        logger.info(f"Job {job_id} cancelled")

    except Exception as e:
        import traceback
        logger.error(f"Job {job_id} failed: {e}\n{traceback.format_exc()}")
        job["status"] = "failed"
        job["completed_at"] = datetime.utcnow()
        job["error"] = {
            "type": "https://api.jonaai.com/errors/analysis-failed",
            "title": "Analysis Failed",
            "status": 500,
            "detail": str(e),
        }

    finally:
        # Release concurrent slot
        decrement_concurrent(job.get("client_id", "unknown"))

        # Fire webhook if configured
        webhook_url = job.get("webhook_url")
        if webhook_url:
            asyncio.create_task(_fire_webhook(job))


class _ResultCollector:
    """Duck-typed WebSocket substitute that captures results from the SDK bridge.

    The SDK bridge calls `await websocket.send_json(msg)` -- this class
    captures those messages and populates the job dict instead.
    """

    def __init__(self, job: Dict[str, Any]):
        self._job = job
        self.text_content = ""
        self.save_ids: List[str] = []
        self.error: Optional[str] = None

    async def send_json(self, msg: Dict[str, Any]) -> None:
        msg_type = msg.get("type", "")

        if msg_type == "error":
            self.error = msg.get("content") or "The analysis failed."

        elif msg_type == "text_delta":
            self.text_content += msg.get("content", "")

        elif msg_type == "text":
            self.text_content += msg.get("content", "")

        elif msg_type == "text_stream_start":
            pass  # Start of streaming text

        elif msg_type == "text_stream_end":
            pass  # End of streaming text

        elif msg_type == "visualization":
            viz = msg.get("visualization")
            if viz:
                self._job.setdefault("visualizations", []).append(viz)

        elif msg_type == "quality_metrics":
            self._job["quality_metrics"] = msg.get("metrics")

        elif msg_type == "process_graph":
            self._job["process_graph"] = msg.get("graph")

        elif msg_type == "thinking":
            step = msg.get("content", "")
            if step and not msg.get("thinking_completed"):
                self._job["current_step"] = step

        elif msg_type == "tool_start":
            tool_name = msg.get("tool_name", "")
            self._job["current_step"] = f"Running {tool_name}"
            self._job["steps_completed"] = self._job.get("steps_completed", 0) + 1

        elif msg_type == "tool_result":
            tool_name = msg.get("tool_name", "")
            result = msg.get("result")
            # Track save IDs from export tools
            if isinstance(result, dict) and "save_id" in result:
                self.save_ids.append(result["save_id"])
            elif isinstance(result, str) and "save_id" in result:
                try:
                    parsed = json.loads(result)
                    if "save_id" in parsed:
                        self.save_ids.append(parsed["save_id"])
                except (json.JSONDecodeError, TypeError):
                    pass

        elif msg_type == "sustainability_metrics":
            pass  # Could store if needed

        elif msg_type == "suggestions":
            pass  # Not relevant for API responses

        elif msg_type == "done":
            pass  # Handled in _execute_analysis

        elif msg_type == "error":
            error_content = msg.get("content", "Unknown error")
            self._job["error"] = {
                "type": "https://api.jonaai.com/errors/analysis-error",
                "title": "Analysis Error",
                "status": 500,
                "detail": error_content,
            }


def _is_safe_webhook_url(url: str) -> bool:
    """Validate webhook URL is not targeting internal/private networks (SSRF prevention).

    Resolves hostname to IP addresses and checks resolved IPs against private
    ranges to prevent DNS rebinding attacks.
    """
    import ipaddress
    import socket
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http"):
            return False
        hostname = parsed.hostname or ""
        # Block known internal hostnames
        blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1",
                         "metadata.google.internal", "169.254.169.254"}
        if hostname in blocked_hosts:
            return False

        # Resolve hostname to IP and check resolved addresses
        # This prevents DNS rebinding attacks where hostname resolves to
        # a public IP at validation time but a private IP at delivery time
        try:
            addr_infos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
            for family, kind, proto, canonname, sockaddr in addr_infos:
                resolved_ip = sockaddr[0]
                addr = ipaddress.ip_address(resolved_ip)
                addr = getattr(addr, "ipv4_mapped", None) or addr
                if not addr.is_global or addr.is_multicast:
                    return False
        except (socket.gaierror, ValueError):
            # If DNS resolution fails, block the URL
            return False

        return True
    except Exception:
        return False


async def _fire_webhook(job: Dict[str, Any]) -> None:
    """POST results to the user's webhook URL."""
    webhook_url = job.get("webhook_url")
    if not webhook_url:
        return

    if not _is_safe_webhook_url(webhook_url):
        logger.warning(f"Webhook blocked (SSRF prevention): {urlparse(webhook_url).hostname}")
        return

    try:
        import httpx
        payload = {
            "event": f"job.{job['status']}",
            "job_id": job["job_id"],
            "status": job["status"],
            "result_summary": job.get("result_summary"),
            "data_urls": job.get("data_urls", {}),
            "quality_metrics": job.get("quality_metrics"),
            "error": job.get("error"),
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": job.get("metadata"),
        }

        from ..intelligence.worker import pinned_post

        resp = await pinned_post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "JonaAI-Webhook/1.0"},
        )
        logger.info(f"Webhook {urlparse(webhook_url).hostname} responded: {resp.status_code}")

    except Exception as e:
        logger.warning(f"Webhook delivery failed for {urlparse(webhook_url).hostname}: {e}")


@router.post(
    "/analyze",
    response_model=JobResponse,
    status_code=202,
    summary="Submit analysis (async)",
    description=(
        "Submit a natural language Earth observation query for asynchronous processing. "
        "Returns a job_id to poll for status and results."
    ),
    tags=["Analysis"],
)
async def submit_analysis(
    body: AnalyzeRequest,
    request: Request,
    response: Response,
    client: AuthenticatedClient = Depends(require_scope("analyze")),
):
    # Rate limit check
    allowed, rate_info = check_rate_limit(client.client_id, client.tier)
    add_rate_limit_headers(response, rate_info)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/rate-limit-exceeded",
                title="Rate Limit Exceeded",
                status=429,
                detail=f"You have exceeded the {client.tier} tier limit of {rate_info['limit']} requests/day. Resets at {rate_info['reset_at']}.",
            ).model_dump(),
            headers={
                "Retry-After": rate_info["reset_at"],
                "X-RateLimit-Limit": str(rate_info["limit"]),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": rate_info["reset_at"],
            },
        )

    _check_concurrent_limit(client, rate_info)

    # Create job
    job = job_manager.create_job(
        query=body.query,
        client_id=client.client_id,
        user_id=client.user_id,
        bbox=body.bbox.model_dump() if body.bbox else None,
        temporal_extent=body.temporal_extent.model_dump() if body.temporal_extent else None,
        collection=body.collection,
        output_format=body.output_format.value,
        webhook_url=body.webhook_url,
        project_id=body.project_id,
        metadata=body.metadata,
    )

    # Track concurrent
    increment_concurrent(client.client_id)

    # Launch background task
    stop_event = asyncio.Event()
    job["_stop_event"] = stop_event
    task = asyncio.create_task(_execute_analysis(job, request.app.state))
    job["_task"] = task

    job_id = job["job_id"]
    return JobResponse(
        job_id=job_id,
        status=JobStatus.queued,
        created_at=job["created_at"],
        estimated_duration_seconds=45,
        links={
            "self": f"/api/v1/analyze/{job_id}",
            "cancel": f"/api/v1/analyze/{job_id}",
            "results": f"/api/v1/results/{job_id}/data",
        },
    )


@router.get(
    "/analyze/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status",
    tags=["Analysis"],
)
async def get_job_status(
    job_id: str,
    response: Response,
    client: AuthenticatedClient = Depends(require_scope("analyze")),
):
    allowed, rate_info = check_rate_limit(client.client_id, client.tier)
    add_rate_limit_headers(response, rate_info)

    job = job_manager.get_job_for_client(job_id, client.client_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/job-not-found",
                title="Job Not Found",
                status=404,
                detail=f"No analysis job found with ID '{job_id}'.",
            ).model_dump(),
        )

    status_data = job_manager.to_status_response(job)
    return JobStatusResponse(**status_data)


@router.post(
    "/analyze/sync",
    summary="Submit analysis (synchronous)",
    description=(
        "Submit a query and block until results are ready or timeout is reached. "
        "Suitable for simple queries. For complex analyses, use the async endpoint."
    ),
    tags=["Analysis"],
)
async def submit_analysis_sync(
    body: AnalyzeSyncRequest,
    request: Request,
    response: Response,
    client: AuthenticatedClient = Depends(require_scope("analyze")),
):
    # Rate limit check
    allowed, rate_info = check_rate_limit(client.client_id, client.tier)
    add_rate_limit_headers(response, rate_info)
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _check_concurrent_limit(client, rate_info)

    # Create and launch job
    job = job_manager.create_job(
        query=body.query,
        client_id=client.client_id,
        user_id=client.user_id,
        bbox=body.bbox.model_dump() if body.bbox else None,
        temporal_extent=body.temporal_extent.model_dump() if body.temporal_extent else None,
        collection=body.collection,
        output_format=body.output_format.value,
        project_id=body.project_id,
        metadata=body.metadata,
    )

    increment_concurrent(client.client_id)

    stop_event = asyncio.Event()
    job["_stop_event"] = stop_event
    task = asyncio.create_task(_execute_analysis(job, request.app.state))
    job["_task"] = task

    # Wait for completion or timeout
    try:
        await asyncio.wait_for(task, timeout=body.timeout_seconds)
    except asyncio.TimeoutError:
        # Return partial results with 202
        logger.info(f"Sync job {job['job_id']} timed out after {body.timeout_seconds}s")
        response.status_code = 202
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "message": f"Analysis is still running. Poll /api/v1/analyze/{job['job_id']} for results.",
            "links": {"self": f"/api/v1/analyze/{job['job_id']}"},
        }

    if job["status"] == "failed":
        raise HTTPException(
            status_code=500,
            detail=job.get("error", {"title": "Analysis failed"}),
        )

    return job_manager.to_result_response(job)


@router.delete(
    "/analyze/{job_id}",
    summary="Cancel analysis",
    tags=["Analysis"],
)
async def cancel_analysis(
    job_id: str,
    client: AuthenticatedClient = Depends(require_scope("analyze")),
):
    if not job_manager.cancel_job(job_id, client.client_id):
        raise HTTPException(
            status_code=404,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/job-not-found",
                title="Job Not Found or Not Cancellable",
                status=404,
                detail=f"Job '{job_id}' was not found or has already completed.",
            ).model_dump(),
        )
    return {"cancelled": True, "job_id": job_id}


@router.get(
    "/analyze/{job_id}/events",
    summary="Stream job progress (SSE)",
    description="Server-Sent Events stream for real-time job progress updates.",
    tags=["Analysis"],
)
async def stream_job_events(
    job_id: str,
    client: AuthenticatedClient = Depends(require_scope("analyze")),
):
    job = job_manager.get_job_for_client(job_id, client.client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        last_step = None
        last_progress = None

        while True:
            current_job = job_manager.get_job(job_id)
            if not current_job:
                yield f"event: error\ndata: {json.dumps({'message': 'Job not found'})}\n\n"
                break

            # Send progress update if changed
            step = current_job.get("current_step")
            progress = current_job.get("progress")
            if step != last_step or progress != last_progress:
                data = {
                    "status": current_job["status"],
                    "progress": progress,
                    "current_step": step,
                    "steps_completed": current_job.get("steps_completed", 0),
                }
                yield f"event: progress\ndata: {json.dumps(data)}\n\n"
                last_step = step
                last_progress = progress

            # Send terminal event
            if current_job["status"] in ("completed", "failed", "cancelled"):
                result_data = job_manager.to_status_response(current_job)
                yield f"event: {current_job['status']}\ndata: {json.dumps(result_data)}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════════════
#  TEMPLATES ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

# Built-in workflow templates for common Earth observation tasks
_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "tmpl_ndvi_timeseries": {
        "id": "tmpl_ndvi_timeseries",
        "name": "NDVI Time Series",
        "description": "Calculate NDVI (Normalized Difference Vegetation Index) over time for a region.",
        "category": "vegetation",
        "parameters": [
            {"name": "bbox", "type": "bbox", "description": "Area of interest", "required": True},
            {"name": "start_date", "type": "date", "description": "Start date (YYYY-MM-DD)", "required": True},
            {"name": "end_date", "type": "date", "description": "End date (YYYY-MM-DD)", "required": True},
            {"name": "collection", "type": "enum", "description": "Satellite collection",
             "required": False, "default": "sentinel-2-l2a",
             "enum_values": ["sentinel-2-l2a", "landsat-c2-l2"]},
        ],
        "example_query": "Calculate NDVI time series for Kerala from June to September 2024",
        "estimated_duration_seconds": 30,
        "tags": ["ndvi", "vegetation", "time-series"],
    },
    "tmpl_ndvi_change": {
        "id": "tmpl_ndvi_change",
        "name": "NDVI Change Detection",
        "description": "Compare vegetation health between two time periods to detect significant changes.",
        "category": "vegetation",
        "parameters": [
            {"name": "bbox", "type": "bbox", "description": "Area of interest", "required": True},
            {"name": "period_before_start", "type": "date", "description": "Baseline period start", "required": True},
            {"name": "period_before_end", "type": "date", "description": "Baseline period end", "required": True},
            {"name": "period_after_start", "type": "date", "description": "Comparison period start", "required": True},
            {"name": "period_after_end", "type": "date", "description": "Comparison period end", "required": True},
            {"name": "threshold", "type": "number", "description": "Change detection threshold (0.0-1.0)",
             "required": False, "default": 0.1, "min_value": 0.0, "max_value": 1.0},
        ],
        "example_query": "Detect NDVI changes between Jan-Mar 2024 and Jul-Sep 2024",
        "estimated_duration_seconds": 60,
        "tags": ["ndvi", "change-detection", "vegetation", "insurance"],
    },
    "tmpl_flood_assessment": {
        "id": "tmpl_flood_assessment",
        "name": "Flood Extent Assessment",
        "description": "Analyze water extent using NDWI to assess flood impact in a region.",
        "category": "water",
        "parameters": [
            {"name": "bbox", "type": "bbox", "description": "Area of interest", "required": True},
            {"name": "event_date", "type": "date", "description": "Date of flood event", "required": True},
            {"name": "baseline_date", "type": "date", "description": "Pre-flood baseline date", "required": True},
        ],
        "example_query": "Assess flood extent by comparing water levels before and after the event",
        "estimated_duration_seconds": 45,
        "tags": ["flood", "water", "ndwi", "disaster", "insurance"],
    },
    "tmpl_land_cover": {
        "id": "tmpl_land_cover",
        "name": "Land Cover Classification",
        "description": "Classify land cover types (urban, vegetation, water, bare soil) for a region.",
        "category": "urban",
        "parameters": [
            {"name": "bbox", "type": "bbox", "description": "Area of interest", "required": True},
            {"name": "date", "type": "date", "description": "Analysis date", "required": True},
        ],
        "example_query": "Classify land cover for the specified area",
        "estimated_duration_seconds": 40,
        "tags": ["land-cover", "classification", "urban"],
    },
    "tmpl_crop_health": {
        "id": "tmpl_crop_health",
        "name": "Crop Health Monitoring",
        "description": "Monitor crop health using multiple vegetation indices (NDVI, EVI) over a growing season.",
        "category": "agriculture",
        "parameters": [
            {"name": "bbox", "type": "bbox", "description": "Agricultural area of interest", "required": True},
            {"name": "season_start", "type": "date", "description": "Growing season start", "required": True},
            {"name": "season_end", "type": "date", "description": "Growing season end", "required": True},
            {"name": "index", "type": "enum", "description": "Vegetation index to use",
             "required": False, "default": "ndvi",
             "enum_values": ["ndvi", "evi", "ndwi"]},
        ],
        "example_query": "Monitor crop health for the growing season using NDVI",
        "estimated_duration_seconds": 50,
        "tags": ["agriculture", "crop", "monitoring", "insurance"],
    },
}


@router.get(
    "/templates",
    response_model=List[TemplateInfo],
    summary="List available templates",
    tags=["Templates"],
)
async def list_templates(
    category: Optional[str] = Query(None, description="Filter by category"),
    client: AuthenticatedClient = Depends(require_scope("templates")),
):
    templates = list(_TEMPLATES.values())
    if category:
        templates = [t for t in templates if t["category"] == category]
    return templates


@router.get(
    "/templates/{template_id}",
    response_model=TemplateInfo,
    summary="Get template details",
    tags=["Templates"],
)
async def get_template(
    template_id: str,
    client: AuthenticatedClient = Depends(require_scope("templates")),
):
    template = _TEMPLATES.get(template_id)
    if not template:
        raise HTTPException(
            status_code=404,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/template-not-found",
                title="Template Not Found",
                status=404,
                detail=f"No template found with ID '{template_id}'. Use GET /api/v1/templates to list available templates.",
            ).model_dump(),
        )
    return template


@router.post(
    "/templates/{template_id}/execute",
    response_model=JobResponse,
    status_code=202,
    summary="Execute template",
    tags=["Templates"],
)
async def execute_template(
    template_id: str,
    body: TemplateExecuteRequest,
    request: Request,
    response: Response,
    client: AuthenticatedClient = Depends(require_scope("templates")),
):
    template = _TEMPLATES.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Validate required parameters
    for param in template["parameters"]:
        if param["required"] and param["name"] not in body.parameters:
            raise HTTPException(
                status_code=422,
                detail=ProblemDetail(
                    type="https://api.jonaai.com/errors/missing-parameter",
                    title="Missing Required Parameter",
                    status=422,
                    detail=f"Template '{template_id}' requires parameter '{param['name']}': {param['description']}",
                    errors=[{"field": f"parameters.{param['name']}", "message": "Required"}],
                ).model_dump(),
            )

    # Build query from template + parameters
    query = _build_template_query(template, body.parameters)

    # Extract bbox from parameters if provided
    bbox_param = body.parameters.get("bbox")
    bbox = None
    if isinstance(bbox_param, dict):
        bbox = bbox_param

    # Create and launch job (reuse analysis endpoint logic)
    allowed, rate_info = check_rate_limit(client.client_id, client.tier)
    add_rate_limit_headers(response, rate_info)
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _check_concurrent_limit(client, rate_info)

    job = job_manager.create_job(
        query=query,
        client_id=client.client_id,
        user_id=client.user_id,
        bbox=bbox,
        output_format=body.output_format.value,
        webhook_url=body.webhook_url,
        project_id=body.project_id,
        template_id=template_id,
    )

    increment_concurrent(client.client_id)

    stop_event = asyncio.Event()
    job["_stop_event"] = stop_event
    task = asyncio.create_task(_execute_analysis(job, request.app.state))
    job["_task"] = task

    job_id = job["job_id"]
    return JobResponse(
        job_id=job_id,
        status=JobStatus.queued,
        created_at=job["created_at"],
        estimated_duration_seconds=template.get("estimated_duration_seconds", 30),
        links={
            "self": f"/api/v1/analyze/{job_id}",
            "cancel": f"/api/v1/analyze/{job_id}",
            "results": f"/api/v1/results/{job_id}/data",
        },
    )


def _build_template_query(template: Dict[str, Any], params: Dict[str, Any]) -> str:
    """Build a natural language query from template + user parameters."""
    name = template["name"]
    desc = template["description"]

    # Start with the template's example query as a base pattern
    parts = [f"{name}: {desc}"]

    # Add parameter context
    for param_def in template["parameters"]:
        pname = param_def["name"]
        value = params.get(pname, param_def.get("default"))
        if value is not None:
            if pname == "bbox":
                continue  # Handled separately via bbox injection
            parts.append(f"{param_def['description']}: {value}")

    return ". ".join(parts)


# ══════════════════════════════════════════════════════════════════════
#  RESULTS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@router.get(
    "/results/{job_id}/data",
    summary="Download result data",
    description="Download the result data file (GeoTIFF, CSV, etc.) for a completed analysis.",
    tags=["Results"],
)
async def download_result_data(
    job_id: str,
    format: Optional[str] = Query(None, description="Override output format"),
    client: AuthenticatedClient = Depends(require_scope("results")),
):
    job = job_manager.get_job_for_client(job_id, client.client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/job-not-completed",
                title="Job Not Completed",
                status=409,
                detail=f"Job '{job_id}' is in '{job['status']}' state. Data is available only for completed jobs.",
            ).model_dump(),
        )

    # Find the result file
    data_urls = job.get("data_urls", {})
    if not data_urls:
        raise HTTPException(
            status_code=404,
            detail=ProblemDetail(
                type="https://api.jonaai.com/errors/no-result-data",
                title="No Result Data",
                status=404,
                detail="This job completed but did not produce downloadable data files.",
            ).model_dump(),
        )

    # Return the first available file
    for key, url in data_urls.items():
        # Map to actual file path via saved jobs
        from ..storage.job_archive import get_job as _get_saved_job
        save_id = key.replace("geotiff_", "")
        saved = _get_saved_job(save_id)
        if saved and Path(saved["result_path"]).exists():
            return FileResponse(
                saved["result_path"],
                filename=Path(saved["result_path"]).name,
                media_type="image/tiff",
            )

    raise HTTPException(status_code=404, detail="Result file not found on disk")


@router.get(
    "/results/{job_id}/report",
    summary="Download HTML report",
    tags=["Results"],
)
async def download_report(
    job_id: str,
    client: AuthenticatedClient = Depends(require_scope("results")),
):
    job = job_manager.get_job_for_client(job_id, client.client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail="Job not completed")

    # Generate a simple HTML report from the job results
    html = _generate_html_report(job)
    return Response(
        content=html,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="report_{job_id}.html"'},
    )


def _generate_html_report(job: Dict[str, Any]) -> str:
    """Generate a standalone HTML report from job results.

    The query, the model's summary and the graph are untrusted text, so each is escaped."""
    from html import escape as esc

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Jona AI Analysis Report - {esc(str(job['job_id']))}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
        h1 {{ color: #16a34a; border-bottom: 2px solid #16a34a; padding-bottom: 10px; }}
        .meta {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
        .metric {{ display: inline-block; padding: 8px 16px; margin: 4px;
                   background: #f0fdf4; border-radius: 8px; font-size: 14px; }}
        pre {{ background: #f8f9fa; padding: 16px; border-radius: 8px;
               overflow-x: auto; font-size: 13px; }}
        .summary {{ background: #f0fdf4; padding: 16px; border-radius: 8px;
                    border-left: 4px solid #16a34a; margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>Jona AI Analysis Report</h1>
    <div class="meta">
        <p><strong>Job ID:</strong> {esc(str(job['job_id']))}</p>
        <p><strong>Query:</strong> {esc(str(job['query']))}</p>
        <p><strong>Created:</strong> {job['created_at'].isoformat() if job.get('created_at') else 'N/A'}</p>
        <p><strong>Completed:</strong> {job['completed_at'].isoformat() if job.get('completed_at') else 'N/A'}</p>
        <p><strong>Execution Time:</strong> {esc(str(job.get('execution_time_ms', 'N/A')))} ms</p>
    </div>

    <h2>Summary</h2>
    <div class="summary">{esc(str(job.get('result_summary') or 'No summary available.'))}</div>

    <h2>Quality Metrics</h2>
    {''.join(f'<span class="metric">{esc(str(k))}: {esc(str(v))}</span>' for k, v in (job.get("quality_metrics") or {}).items())}

    <h2>Process Graph</h2>
    <pre>{esc(json.dumps(job.get('process_graph', {}), indent=2))}</pre>

    <hr>
    <p style="color: #999; font-size: 12px;">Generated by Jona AI - {datetime.utcnow().isoformat()}</p>
</body>
</html>"""


@router.get(
    "/results/{job_id}/process-graph",
    summary="Get OpenEO process graph",
    tags=["Results"],
)
async def get_process_graph(
    job_id: str,
    client: AuthenticatedClient = Depends(require_scope("results")),
):
    job = job_manager.get_job_for_client(job_id, client.client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job_id,
        "process_graph": job.get("process_graph"),
        "collection": job.get("collection"),
        "spatial_extent": job.get("bbox"),
        "temporal_extent": job.get("temporal_extent"),
    }


@router.get(
    "/results/{job_id}/metrics",
    summary="Get quality metrics",
    tags=["Results"],
)
async def get_quality_metrics(
    job_id: str,
    client: AuthenticatedClient = Depends(require_scope("results")),
):
    job = job_manager.get_job_for_client(job_id, client.client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail="Job not completed")

    return {
        "job_id": job_id,
        "quality_metrics": job.get("quality_metrics"),
    }


# ══════════════════════════════════════════════════════════════════════
#  BATCH ENDPOINTS
# ══════════════════════════════════════════════════════════════════════
# Batch state is persisted (BatchRun table) via storage.job_archive so it
# survives restarts — see create_batch/get_batch below.


@router.post(
    "/batch",
    status_code=202,
    summary="Submit batch analysis",
    description="Submit the same analysis query for multiple locations. Each location runs independently.",
    tags=["Batch"],
)
async def submit_batch(
    body: BatchRequest,
    request: Request,
    response: Response,
    client: AuthenticatedClient = Depends(require_scope("batch")),
):
    # Rate limit
    allowed, rate_info = check_rate_limit(client.client_id, client.tier)
    add_rate_limit_headers(response, rate_info)
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _check_concurrent_limit(client, rate_info, starting=len(body.locations))

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow()

    location_jobs = []
    for loc in body.locations:
        job = job_manager.create_job(
            query=body.query,
            client_id=client.client_id,
            user_id=client.user_id,
            bbox=loc.bbox.model_dump(),
            temporal_extent=body.temporal_extent.model_dump() if body.temporal_extent else None,
            output_format=body.output_format.value,
            webhook_url=None,  # Batch webhook fires at batch level
            project_id=body.project_id,
            metadata={"batch_id": batch_id, "location_id": loc.id, "location_name": loc.name},
            template_id=body.template_id,
        )

        increment_concurrent(client.client_id)

        stop_event = asyncio.Event()
        job["_stop_event"] = stop_event
        task = asyncio.create_task(_execute_analysis(job, request.app.state))
        job["_task"] = task

        location_jobs.append({
            "location_id": loc.id,
            "name": loc.name,
            "job_id": job["job_id"],
        })

    from ..storage.job_archive import create_batch

    batch = {
        "batch_id": batch_id,
        "client_id": client.client_id,
        "user_id": client.user_id,
        "location_jobs": location_jobs,
        "webhook_url": body.webhook_url,
        "created_at": now,
    }
    create_batch(batch)

    return {
        "batch_id": batch_id,
        "status": "running",
        "total_locations": len(body.locations),
        "created_at": now.isoformat(),
        "links": {
            "self": f"/api/v1/batch/{batch_id}",
        },
    }


@router.get(
    "/batch/{batch_id}",
    summary="Get batch status",
    tags=["Batch"],
)
async def get_batch_status(
    batch_id: str,
    client: AuthenticatedClient = Depends(require_scope("batch")),
):
    from ..storage.job_archive import get_batch

    # Client scoping lives in the query (get_batch), preserving the
    # 404-for-other-clients behavior without fetching another client's row.
    batch = get_batch(batch_id, client.client_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    locations = []
    completed = failed = running = queued = cancelled = unknown = 0

    for loc_job in batch["location_jobs"]:
        job = job_manager.get_job(loc_job["job_id"])
        status = job["status"] if job else "unknown"
        locations.append({
            "location_id": loc_job["location_id"],
            "name": loc_job.get("name"),
            "job_id": loc_job["job_id"],
            "status": status,
            "progress": job.get("progress") if job else None,
            "error": str(job.get("error")) if job and job.get("error") else None,
        })

        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1
        elif status == "running":
            running += 1
        elif status == "queued":
            queued += 1
        elif status == "cancelled":
            cancelled += 1
        else:
            unknown += 1

    # Location jobs live in memory (api/jobs.py), so a restart loses them while the batch
    # row survives. Those locations would never finish, so reporting "running" would be
    # wrong forever; the batch says it can't tell instead.
    total = len(batch["location_jobs"])
    if unknown:
        overall_status = "unknown"
    elif completed + failed + cancelled == total:
        overall_status = "failed" if failed else "cancelled" if cancelled else "completed"
    else:
        overall_status = "running"

    return {
        "batch_id": batch_id,
        "status": overall_status,
        "total_locations": total,
        "completed": completed,
        "failed": failed,
        "running": running,
        "queued": queued,
        "cancelled": cancelled,
        "unknown": unknown,
        "locations": locations,
        "created_at": batch["created_at"].isoformat(),
        "links": {"self": f"/api/v1/batch/{batch_id}"},
    }
