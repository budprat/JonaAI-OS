# ABOUTME: Durable Earth Intelligence worker for scheduled watches and queued observations.

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import logging
import os
import re
import smtplib
import socket
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from openeo_ai.hashing import hash_json as _hash_json
from openeo_ai.intelligence.severity_pipeline import evaluate_severity
from openeo_ai.storage.job_archive import save_job
from openeo_ai.tools.job_tools import JobTools

from .repository import EarthIntelligenceRepository

logger = logging.getLogger(__name__)


TERMINAL_JOB_STATUSES = {"finished", "completed", "error", "canceled", "cancelled"}
RETRYABLE_ERROR_PATTERNS = (
    "timeout",
    "timed out",
    "temporary",
    "temporarily",
    "connection",
    "network",
    "rate limit",
    "429",
    "502",
    "503",
    "504",
    "service unavailable",
    "gateway",
)
PERMANENT_ERROR_PATTERNS = (
    "no process graph",
    "invalid process graph",
    "unauthorized",
    "forbidden",
    "permission",
    "not found",
)


def _now() -> datetime:
    return datetime.utcnow()


# Cover the Sentinel-2 revisit gap plus typical cloud coverage so a run finds at
# least one usable scene even for short cadences.
_MIN_WATCH_LOOKBACK = timedelta(days=14)


def _rolling_temporal_window(cadence: Optional[str], temporal_policy: Optional[Dict[str, Any]]) -> tuple:
    """[start_iso, end_iso] window ending 'now', sized to the watch cadence.

    A recurring Watch must analyse the LATEST imagery each run, not the frozen
    dates the agent baked into the stored process graph. Lookback comes from
    temporal_policy if set, else the cadence (floored at the revisit gap), else
    30 days.
    """
    from .repository import _parse_cadence

    policy = temporal_policy or {}
    lookback_days = policy.get("lookback_days") or policy.get("window_days")
    if lookback_days:
        lookback = timedelta(days=float(lookback_days))
    else:
        cadence_delta = _parse_cadence(cadence)
        lookback = max(cadence_delta, _MIN_WATCH_LOOKBACK) if cadence_delta else timedelta(days=30)
    end = _now()
    start = end - lookback
    return start.date().isoformat(), end.date().isoformat()


def _reroll_load_collection_dates(process_graph: Dict[str, Any], start_iso: str, end_iso: str) -> tuple:
    """Return (graph_copy, count): a deep copy with every load_collection node's
    temporal_extent set to [start_iso, end_iso]. Leaves the input untouched.
    Handles both raw-node graphs and {"process_graph": {...}}-wrapped ones.
    """
    graph = copy.deepcopy(process_graph)
    nodes = graph.get("process_graph") if isinstance(graph.get("process_graph"), dict) else graph
    count = 0
    if isinstance(nodes, dict):
        for node in nodes.values():
            if isinstance(node, dict) and node.get("process_id") == "load_collection":
                args = node.setdefault("arguments", {})
                if "temporal_extent" in args:
                    args["temporal_extent"] = [start_iso, end_iso]
                    count += 1
    return graph, count


def _failure_category(error: str) -> str:
    text = (error or "").lower()
    if any(pattern in text for pattern in PERMANENT_ERROR_PATTERNS):
        return "permanent"
    if any(pattern in text for pattern in RETRYABLE_ERROR_PATTERNS):
        return "transient"
    return "unknown"


def _is_retryable_error(error: str) -> bool:
    return _failure_category(error) == "transient"


class DeliveryDispatchError(RuntimeError):
    """Delivery failure carrying an explicit retry classification."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


_PRIVATE_HOSTNAMES = {"localhost", "0.0.0.0", "::1"}


def _is_private_host(hostname: str) -> bool:
    if hostname.lower() in _PRIVATE_HOSTNAMES:
        return True
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Not an IP literal — callers must resolve it and re-check each address.
        return False
    # An IPv4-mapped IPv6 address reaches the IPv4 host it wraps. is_global also excludes
    # shared space such as 100.64.0.0/10, which is this host's tailnet.
    addr = getattr(addr, "ipv4_mapped", None) or addr
    return not addr.is_global or addr.is_multicast


async def _resolve_addresses(hostname: str) -> List[str]:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror as exc:
        raise DeliveryDispatchError(
            f"webhook host could not be resolved: {exc}", retryable=False
        )
    # Strip IPv6 zone ids ("%en0") so ipaddress can parse the address.
    return [str(info[4][0]).split("%", 1)[0] for info in infos]


async def _validate_webhook_target(target: str) -> Optional[str]:
    """SSRF guard — delivery targets are user-controlled.

    Returns the checked address of a named host, for pinned_post to connect to.
    """
    parsed = urlparse(target or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise DeliveryDispatchError(
            "webhook target must be an http(s) URL", retryable=False
        )
    if os.environ.get("JONA_ALLOW_PRIVATE_WEBHOOKS", "").lower() == "true":
        return None
    hostname = parsed.hostname
    if _is_private_host(hostname):
        raise DeliveryDispatchError(
            "webhook target points at a private or local address", retryable=False
        )
    try:
        ipaddress.ip_address(hostname)
        return None
    except ValueError:
        # A name (or legacy numeric form like http://2130706433/) can launder a
        # private destination — every address it resolves to must be public.
        addresses = await _resolve_addresses(hostname)
        for address in addresses:
            if _is_private_host(address):
                raise DeliveryDispatchError(
                    "webhook target resolves to a private or local address",
                    retryable=False,
                )
        return addresses[0] if addresses else None


async def pinned_post(target: str, **kwargs) -> httpx.Response:
    """POST to a checked target, connecting to the address that was checked.

    Otherwise a DNS answer could change between the check and the connection. TLS still
    verifies the original name. Redirects aren't followed.
    """
    address = await _validate_webhook_target(target)
    url = httpx.URL(target)
    headers = dict(kwargs.pop("headers", None) or {})
    extensions = {}
    if address:
        headers["Host"] = url.netloc.decode("ascii")
        extensions["sni_hostname"] = url.host
        url = url.copy_with(host=address)
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.post(url, headers=headers, extensions=extensions, **kwargs)


def _http_status_error(status_code: int, channel: str) -> DeliveryDispatchError:
    retryable = status_code == 429 or status_code >= 500
    return DeliveryDispatchError(
        f"{channel} endpoint returned HTTP {status_code}", retryable=retryable
    )


async def _send_webhook(target: str, payload: Dict[str, Any]) -> None:
    response = await pinned_post(target, json=payload)
    if not (200 <= response.status_code < 300):
        raise _http_status_error(response.status_code, "webhook")


async def _send_slack(target: str, payload: Dict[str, Any]) -> None:
    await _validate_webhook_target(target)
    summary = "[{severity}] {metric} signal for asset {asset}: value {value}".format(
        severity=payload.get("severity"),
        metric=payload.get("metric"),
        asset=payload.get("asset_id"),
        value=payload.get("value"),
    )
    explanation = str(payload.get("explanation") or "").strip()
    body: Dict[str, Any] = {
        "text": summary,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        ],
    }
    if explanation:
        body["blocks"].append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": explanation[:2000]}],
            }
        )
    response = await pinned_post(target, json=body)
    if not (200 <= response.status_code < 300):
        raise _http_status_error(response.status_code, "slack")


def _send_email_sync(target: str, payload: Dict[str, Any]) -> None:
    host = os.environ.get("JONA_SMTP_HOST")
    if not host:
        raise DeliveryDispatchError(
            "SMTP not configured (set JONA_SMTP_HOST)", retryable=False
        )
    port = int(os.environ.get("JONA_SMTP_PORT", "587"))
    user = os.environ.get("JONA_SMTP_USER")
    password = os.environ.get("JONA_SMTP_PASSWORD")
    sender = os.environ.get("JONA_SMTP_FROM") or user or "jonaai@localhost"

    message = EmailMessage()
    message["Subject"] = (
        f"[JonaAI {payload.get('severity')}] {payload.get('metric')} signal"
    )
    message["From"] = sender
    message["To"] = target
    message.set_content(json.dumps(payload, indent=2, default=str))

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        if os.environ.get("JONA_SMTP_STARTTLS", "true").lower() == "true":
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)


async def _send_email(target: str, payload: Dict[str, Any]) -> None:
    await asyncio.to_thread(_send_email_sync, target, payload)


_DELIVERY_SENDERS = {
    "webhook": _send_webhook,
    "slack": _send_slack,
    "email": _send_email,
}


def _tick_metrics(due: List[Dict[str, Any]], processed: List[Dict[str, Any]]) -> Dict[str, Any]:
    status_counts: Dict[str, int] = {}
    for item in processed:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "queued_due_count": len(due),
        "processed_count": len(processed),
        "status_counts": status_counts,
        "retry_scheduled_count": sum(1 for item in processed if item.get("retry_scheduled")),
        "failed_count": status_counts.get("failed", 0),
    }


def _extract_output_path(result_info: Dict[str, Any]) -> Optional[str]:
    for key in ("output_path", "file_path", "path"):
        value = result_info.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_statistics(result_info: Dict[str, Any]) -> Dict[str, Any]:
    stats = result_info.get("statistics")
    return stats if isinstance(stats, dict) else {}


def _infer_metric_name(watch: Dict[str, Any], result_info: Dict[str, Any]) -> str:
    policy = watch.get("quality_policy") or {}
    if isinstance(policy, dict) and isinstance(policy.get("metric"), str):
        return policy["metric"]

    interpreted = result_info.get("interpreted_index")
    if isinstance(interpreted, str) and interpreted:
        return interpreted

    text = " ".join(
        str(part or "")
        for part in (
            watch.get("name"),
            watch.get("description"),
            watch.get("template_id"),
        )
    )
    for token in (
        "NDVI",
        "EVI",
        "NDWI",
        "MNDWI",
        "NBR",
        "NDBI",
        "NDMI",
        "BSI",
        "LAI",
        "SAVI",
    ):
        if re.search(rf"\b{token}\b", text, flags=re.IGNORECASE):
            return token
    return "result_mean"


def _extract_scalar_value(stats: Dict[str, Any]) -> Optional[float]:
    for key in ("mean", "value", "median", "max", "min"):
        value = stats.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _confidence_from_stats(stats: Dict[str, Any]) -> Optional[float]:
    nan_percent = stats.get("nan_percent")
    if isinstance(nan_percent, (int, float)):
        return max(0.0, min(1.0, 1.0 - (float(nan_percent) / 100.0)))
    coverage = stats.get("coverage_pct")
    if isinstance(coverage, (int, float)):
        return max(0.0, min(1.0, float(coverage) / 100.0))
    return None


_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
_DROP_WORDS = {"drop", "decrease", "down", "fall", "decline"}
_RISE_WORDS = {"rise", "increase", "up", "gain"}


def _delivery_gate_enabled() -> bool:
    """Deliveries are gated on a policy breach unless explicitly disabled."""
    return os.environ.get("JONA_DELIVERY_THRESHOLD_GATE", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _delivery_allowed(policy: Dict[str, Any], signal: Dict[str, Any]) -> bool:
    """A delivery fires only when the signal breaches the watch policy.

    Precedence: an evaluable direction+threshold on the metric delta is decisive;
    otherwise fall back to a severity floor (default 'warning' — kills the
    info-alert-every-tick spam that scheduled watches would otherwise produce).
    JONA_DELIVERY_THRESHOLD_GATE=0 restores the legacy unconditional dispatch.
    """
    if not _delivery_gate_enabled():
        return True
    direction = str(policy.get("direction") or "").strip().lower()
    threshold = policy.get("threshold")
    delta = signal.get("delta")
    if direction and isinstance(threshold, (int, float)) and isinstance(delta, (int, float)):
        if direction in _DROP_WORDS:
            return delta <= -abs(threshold)
        if direction in _RISE_WORDS:
            return delta >= abs(threshold)
    min_severity = str(policy.get("min_severity") or "warning").strip().lower()
    return _SEVERITY_RANK.get(str(signal.get("severity", "info")).lower(), 0) >= _SEVERITY_RANK.get(min_severity, 1)


class EarthIntelligenceWorker:
    """Executes queued Earth observations and materializes scheduled watches."""

    def __init__(
        self,
        repository: Optional[EarthIntelligenceRepository] = None,
        openeo_url: Optional[str] = None,
        poll_interval_seconds: int = 15,
        job_timeout_seconds: int = 300,
        job_poll_interval_seconds: int = 10,
        schedule_limit: int = 25,
        stale_lock_seconds: int = 3600,
        max_attempts: int = 3,
        retry_base_delay_seconds: int = 60,
        retry_max_delay_seconds: int = 900,
        worker_id: Optional[str] = None,
    ) -> None:
        self.repository = repository or EarthIntelligenceRepository()
        self.openeo_url = (openeo_url or "http://localhost:8080/openeo/1.1.0").rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds
        self.job_timeout_seconds = job_timeout_seconds
        self.job_poll_interval_seconds = job_poll_interval_seconds
        self.schedule_limit = schedule_limit
        self.stale_lock_seconds = stale_lock_seconds
        self.max_attempts = max_attempts
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self.worker_id = worker_id or f"earth-worker-{uuid.uuid4().hex[:8]}"
        self._running = False
        self._started_at: Optional[datetime] = None
        self._last_tick_at: Optional[datetime] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._processed_count = 0
        self._failed_count = 0

    @classmethod
    def from_env(cls) -> "EarthIntelligenceWorker":
        return cls(
            openeo_url=os.environ.get("OPENEO_URL") or os.environ.get("OPENEO_API_URL"),
            poll_interval_seconds=int(os.environ.get("EARTH_INTELLIGENCE_POLL_INTERVAL", "15")),
            job_timeout_seconds=int(os.environ.get("EARTH_INTELLIGENCE_JOB_TIMEOUT", "300")),
            job_poll_interval_seconds=int(os.environ.get("EARTH_INTELLIGENCE_JOB_POLL_INTERVAL", "10")),
            schedule_limit=int(os.environ.get("EARTH_INTELLIGENCE_SCHEDULE_LIMIT", "25")),
            stale_lock_seconds=int(os.environ.get("EARTH_INTELLIGENCE_STALE_LOCK_SECONDS", "3600")),
            max_attempts=int(os.environ.get("EARTH_INTELLIGENCE_MAX_ATTEMPTS", "3")),
            retry_base_delay_seconds=int(os.environ.get("EARTH_INTELLIGENCE_RETRY_BASE_DELAY", "60")),
            retry_max_delay_seconds=int(os.environ.get("EARTH_INTELLIGENCE_RETRY_MAX_DELAY", "900")),
            worker_id=os.environ.get("EARTH_INTELLIGENCE_WORKER_ID"),
        )

    def status(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "running": self._running,
            "openeo_url": self.openeo_url,
            "poll_interval_seconds": self.poll_interval_seconds,
            "job_timeout_seconds": self.job_timeout_seconds,
            "job_poll_interval_seconds": self.job_poll_interval_seconds,
            "schedule_limit": self.schedule_limit,
            "stale_lock_seconds": self.stale_lock_seconds,
            "max_attempts": self.max_attempts,
            "retry_base_delay_seconds": self.retry_base_delay_seconds,
            "retry_max_delay_seconds": self.retry_max_delay_seconds,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
            "processed_count": self._processed_count,
            "failed_count": self._failed_count,
            "last_result": self._last_result,
        }

    async def run_forever(self) -> None:
        self._running = True
        self._started_at = _now()
        logger.info("Earth Intelligence worker %s started", self.worker_id)
        try:
            while self._running:
                try:
                    await self.tick(max_observations=1)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Earth Intelligence worker tick failed: %s", exc)
                    self._failed_count += 1
                    self._last_result = {"error": str(exc), "at": _now().isoformat()}
                await asyncio.sleep(self.poll_interval_seconds)
        finally:
            self._running = False
            logger.info("Earth Intelligence worker %s stopped", self.worker_id)

    async def stop(self) -> None:
        self._running = False

    async def tick(self, max_observations: int = 1) -> Dict[str, Any]:
        """Run one worker tick: enqueue due watches, then process queued observations."""
        self._last_tick_at = _now()
        due = await asyncio.to_thread(self.repository.enqueue_due_watches, limit=self.schedule_limit)
        processed: List[Dict[str, Any]] = []

        for _ in range(max(0, max_observations)):
            observation = await asyncio.to_thread(
                self.repository.claim_next_observation,
                worker_id=self.worker_id,
                stale_after_seconds=self.stale_lock_seconds,
            )
            if not observation:
                break
            processed.append(await self.execute_observation(observation))

        deliveries = await self.dispatch_deliveries()

        metrics = _tick_metrics(due, processed)
        result = {
            "worker_id": self.worker_id,
            "queued_due": due,
            "processed": processed,
            "deliveries": deliveries,
            "metrics": metrics,
            "at": self._last_tick_at.isoformat(),
        }
        self._last_result = result
        return result

    async def tick_for_user(self, user_id: str, max_observations: int = 1) -> Dict[str, Any]:
        """Run a scoped tick for a user-triggered control-plane request."""
        tick_at = _now()
        due = await asyncio.to_thread(
            self.repository.enqueue_due_watches, limit=self.schedule_limit, user_id=user_id
        )
        processed: List[Dict[str, Any]] = []

        for _ in range(max(0, max_observations)):
            observation = await asyncio.to_thread(
                self.repository.claim_next_observation,
                worker_id=self.worker_id,
                user_id=user_id,
                stale_after_seconds=self.stale_lock_seconds,
            )
            if not observation:
                break
            processed.append(await self.execute_observation(observation))

        deliveries = await self.dispatch_deliveries(user_id=user_id)

        return {
            "worker_id": self.worker_id,
            "queued_due": due,
            "processed": processed,
            "deliveries": deliveries,
            "metrics": _tick_metrics(due, processed),
            "at": tick_at.isoformat(),
        }

    async def dispatch_deliveries(
        self, limit: int = 10, user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Send claimed pending deliveries and record per-attempt outcomes."""
        claimed = await asyncio.to_thread(
            self.repository.claim_pending_deliveries, limit=limit, user_id=user_id
        )
        results: List[Dict[str, Any]] = []
        for delivery in claimed:
            channel = str(delivery.get("channel") or "").strip().lower()
            payload = {
                "signal_id": delivery.get("signal_id"),
                "severity": delivery.get("severity"),
                "metric": delivery.get("metric"),
                "value": delivery.get("value"),
                "explanation": delivery.get("explanation"),
                "asset_id": delivery.get("asset_id"),
                "delivered_at": _now().isoformat(),
            }
            try:
                sender = _DELIVERY_SENDERS.get(channel)
                if sender is None:
                    raise DeliveryDispatchError(
                        f"unknown delivery channel: {channel!r}", retryable=False
                    )
                await sender(delivery.get("target") or "", payload)
                updated = await asyncio.to_thread(
                    self.repository.finish_delivery,
                    delivery["id"], success=True, max_attempts=self.max_attempts
                )
            except Exception as exc:
                retryable = (
                    exc.retryable
                    if isinstance(exc, DeliveryDispatchError)
                    else _is_retryable_error(str(exc))
                )
                updated = await asyncio.to_thread(
                    self.repository.finish_delivery,
                    delivery["id"],
                    success=False,
                    error=str(exc),
                    retryable=retryable,
                    max_attempts=self.max_attempts,
                )
            status = (updated or {}).get("status", "unknown")
            # Log id/channel/status only — target may be an email address or a
            # webhook URL with an embedded secret.
            logger.info("Delivery %s via %s -> %s", delivery["id"], channel, status)
            results.append({"id": delivery["id"], "status": status})
        return results

    async def execute_observation_by_id(self, user_id: str, observation_id: str) -> Dict[str, Any]:
        observation = await asyncio.to_thread(
            self.repository.get_observation, user_id, observation_id
        )
        if not observation:
            return {"observation_id": observation_id, "status": "missing"}
        started = await asyncio.to_thread(
            self.repository.start_observation, user_id, observation_id, self.worker_id
        )
        if not started:
            # A worker claimed it, or it finished, after the request was made.
            return {"observation_id": observation_id, "status": observation.get("status"),
                    "skipped": "not queued or failed"}
        await asyncio.to_thread(
            self.repository.add_observation_event,
            user_id,
            observation_id,
            "claimed",
            "Observation claimed for immediate execution.",
            {"worker_id": self.worker_id, "source": "manual_execute"},
        )
        return await self.execute_observation(started)

    async def execute_observation(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        user_id = observation["user_id"]
        observation_id = observation["id"]
        context = await asyncio.to_thread(
            self.repository.get_observation_context, user_id, observation_id
        )
        if not context:
            return {"observation_id": observation_id, "status": "missing"}

        observation = context["observation"]
        watch = context.get("watch") or {}
        asset = context.get("asset") or {}
        process_graph = watch.get("process_graph") or {}
        # Recurring watches must analyse the LATEST imagery each run; the stored
        # graph carries the frozen dates the agent baked in. Re-window every
        # load_collection to a rolling period ending now (cadenced watches only).
        _rewindow: Optional[Dict[str, Any]] = None
        if process_graph and watch.get("cadence"):
            _start_iso, _end_iso = _rolling_temporal_window(
                watch.get("cadence"), watch.get("temporal_policy")
            )
            process_graph, _n_rerolled = _reroll_load_collection_dates(
                process_graph, _start_iso, _end_iso
            )
            if _n_rerolled:
                _rewindow = {"start": _start_iso, "end": _end_iso, "load_nodes": _n_rerolled}
        scf = watch.get("scf")
        process_graph_hash = _hash_json(process_graph)
        scf_hash = _hash_json(scf)

        if not observation.get("openeo_job_id") and not process_graph:
            return await asyncio.to_thread(
                self._mark_failed,
                user_id,
                observation_id,
                "Watch has no process graph and no existing OpenEO job to resume.",
                retryable=False,
            )

        update_payload: Dict[str, Any] = {
            "status": "running",
            "process_graph_hash": process_graph_hash,
            "scf_hash": scf_hash,
            "error": None,
        }
        if not observation.get("started_at"):
            update_payload["started_at"] = _now()
        await asyncio.to_thread(
            self.repository.update_observation, user_id, observation_id, update_payload
        )
        await asyncio.to_thread(
            self.repository.add_observation_event,
            user_id,
            observation_id,
            "started",
            "Observation execution started.",
            {"worker_id": self.worker_id, "process_graph_hash": process_graph_hash},
        )
        if _rewindow:
            await asyncio.to_thread(
                self.repository.add_observation_event,
                user_id,
                observation_id,
                "temporal_rewindowed",
                f"Rolled watch window to {_rewindow['start']}..{_rewindow['end']} "
                f"({_rewindow['load_nodes']} load node(s)).",
                _rewindow,
            )

        tools = JobTools(openeo_url=self.openeo_url, act_as_user=user_id)
        try:
            job_id = observation.get("openeo_job_id")
            if not job_id:
                created = await tools.create(
                    title=watch.get("name") or f"Observation {observation_id}",
                    description=watch.get("description") or "Persistent Earth Intelligence observation",
                    process_graph=process_graph,
                )
                job_id = created.get("id")
                if not job_id:
                    return await asyncio.to_thread(
                        self._mark_failed, user_id, observation_id, "OpenEO job creation returned no job id."
                    )
                await asyncio.to_thread(
                    self.repository.update_observation, user_id, observation_id, {"openeo_job_id": job_id}
                )
                await asyncio.to_thread(
                    self.repository.add_observation_event,
                    user_id,
                    observation_id,
                    "openeo_job_created",
                    "OpenEO batch job created.",
                    {"openeo_job_id": job_id},
                )

                started = await tools.start(job_id)
                if started.get("status") == "error":
                    return await asyncio.to_thread(
                        self._mark_failed,
                        user_id,
                        observation_id,
                        str(started.get("error") or "OpenEO job failed to start."),
                    )
                await asyncio.to_thread(
                    self.repository.add_observation_event,
                    user_id,
                    observation_id,
                    "openeo_job_started",
                    "OpenEO batch job started.",
                    {"openeo_job_id": job_id, "start_result": started},
                )
            else:
                await asyncio.to_thread(
                    self.repository.add_observation_event,
                    user_id,
                    observation_id,
                    "openeo_job_resume",
                    "Resuming an existing OpenEO batch job.",
                    {"openeo_job_id": job_id},
                )

            status = await self._poll_job(tools, job_id, user_id, observation_id)
            job_status = str(status.get("status", "unknown")).lower()
            if job_status in {"error", "canceled", "cancelled"}:
                return await asyncio.to_thread(
                    self._mark_failed,
                    user_id,
                    observation_id,
                    status.get("error_message") or status.get("message") or f"OpenEO job ended with status {job_status}.",
                    {"openeo_job_id": job_id, "status": status},
                    retryable=False,
                )
            if job_status not in {"finished", "completed"}:
                resume_at = _now() + timedelta(seconds=max(self.job_poll_interval_seconds, 1))
                await asyncio.to_thread(
                    self.repository.update_observation,
                    user_id,
                    observation_id,
                    {
                        "status": "queued",
                        "error": "OpenEO job still running after local worker timeout.",
                        "next_attempt_at": resume_at,
                    },
                )
                await asyncio.to_thread(
                    self.repository.add_observation_event,
                    user_id,
                    observation_id,
                    "deferred",
                    "OpenEO job is still running; worker will resume it in a later tick.",
                    {
                        "openeo_job_id": job_id,
                        "status": status,
                        "next_attempt_at": resume_at.isoformat(),
                    },
                )
                return {
                    "observation_id": observation_id,
                    "status": "queued",
                    "openeo_job_id": job_id,
                    "retry_scheduled": True,
                    "next_attempt_at": resume_at.isoformat(),
                }

            result_info = await tools.get_results(job_id)
            return await self._complete_observation(
                user_id=user_id,
                observation_id=observation_id,
                job_id=job_id,
                context=context,
                result_info=result_info,
                process_graph=process_graph,
                scf=scf,
                process_graph_hash=process_graph_hash,
                asset=asset,
                watch=watch,
            )
        except Exception as exc:
            logger.exception("Observation %s failed", observation_id)
            return await asyncio.to_thread(self._mark_failed, user_id, observation_id, str(exc))
        finally:
            await tools.close()

    async def _poll_job(
        self,
        tools: JobTools,
        job_id: str,
        user_id: str,
        observation_id: str,
    ) -> Dict[str, Any]:
        elapsed = 0
        while elapsed < self.job_timeout_seconds:
            status = await tools.get_status(job_id)
            job_status = str(status.get("status", "unknown")).lower()
            await asyncio.to_thread(
                self.repository.add_observation_event,
                user_id,
                observation_id,
                "openeo_job_status",
                f"OpenEO job status: {job_status}.",
                {"openeo_job_id": job_id, "status": status},
            )
            if job_status in TERMINAL_JOB_STATUSES:
                return status
            await asyncio.sleep(self.job_poll_interval_seconds)
            elapsed += self.job_poll_interval_seconds
        return {"id": job_id, "status": "running", "note": "worker poll timeout"}

    async def _complete_observation(
        self,
        user_id: str,
        observation_id: str,
        job_id: str,
        context: Dict[str, Any],
        result_info: Dict[str, Any],
        process_graph: Dict[str, Any],
        scf: Optional[Dict[str, Any]],
        process_graph_hash: Optional[str],
        asset: Dict[str, Any],
        watch: Dict[str, Any],
    ) -> Dict[str, Any]:
        output_path = _extract_output_path(result_info)
        stats = _extract_statistics(result_info)
        save_id: Optional[str] = None

        if output_path and Path(output_path).exists():
            source_items = [
                {"id": source_id}
                for source_id in context["observation"].get("source_catalog_item_ids", [])
                if isinstance(source_id, str)
            ]
            save_id = await asyncio.to_thread(
                save_job,
                source_path=output_path,
                title=watch.get("name") or f"Observation {observation_id}",
                bounds=result_info.get("bounds"),
                source_query=watch.get("description") or "",
                job_id=job_id,
                project_id=watch.get("project_id"),
                user_id=user_id,
                observation_id=observation_id,
                process_graph=process_graph,
                scf=scf,
                source_items=source_items,
                provenance={
                    "worker_id": self.worker_id,
                    "openeo_job_id": job_id,
                    "watch_id": watch.get("id"),
                    "asset_id": asset.get("id"),
                    "process_graph_hash": process_graph_hash,
                },
            )
            await asyncio.to_thread(
                self.repository.add_observation_event,
                user_id,
                observation_id,
                "evidence_archived",
                "Observation result archived as an evidence package.",
                {"save_id": save_id, "output_path": output_path},
            )
        else:
            await asyncio.to_thread(
                self.repository.add_observation_event,
                user_id,
                observation_id,
                "result_missing",
                "OpenEO job completed but no local output artifact was found.",
                {"openeo_job_id": job_id, "result_info": result_info},
            )

        quality_metrics = {
            "statistics": stats,
            "validation": result_info.get("validation"),
            "format": result_info.get("format"),
            "size_bytes": result_info.get("size_bytes"),
            "save_id": save_id,
        }
        await asyncio.to_thread(
            self.repository.update_observation,
            user_id,
            observation_id,
            {
                "status": "completed",
                "openeo_job_id": job_id,
                "quality_metrics": quality_metrics,
                "completed_at": _now(),
                "error": None,
            },
        )
        await asyncio.to_thread(
            self.repository.add_observation_event,
            user_id,
            observation_id,
            "completed",
            "Observation completed.",
            {"openeo_job_id": job_id, "save_id": save_id, "quality_metrics": quality_metrics},
        )

        signal = await asyncio.to_thread(
            self._create_signal, user_id, observation_id, asset, watch, result_info, stats, save_id
        )
        self._processed_count += 1
        return {
            "observation_id": observation_id,
            "status": "completed",
            "openeo_job_id": job_id,
            "save_id": save_id,
            "signal": signal,
        }

    def _create_signal(
        self,
        user_id: str,
        observation_id: str,
        asset: Dict[str, Any],
        watch: Dict[str, Any],
        result_info: Dict[str, Any],
        stats: Dict[str, Any],
        save_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        value = _extract_scalar_value(stats)
        if value is None:
            return None

        metric = _infer_metric_name(watch, result_info)
        baseline_value = None
        baseline_std = None
        delta = None
        baseline_info = None
        asset_id = asset.get("id")
        watch_id = watch.get("id")
        if asset_id:
            baseline_info = self.repository.update_baseline_sample(
                user_id=user_id,
                asset_id=asset_id,
                watch_id=watch_id,
                metric=metric,
                value=value,
                window_policy=watch.get("temporal_policy") or {},
            )
            baseline_value = baseline_info.get("previous_mean")
            baseline_std = baseline_info.get("previous_std")
            if isinstance(baseline_value, (int, float)):
                delta = value - float(baseline_value)
            self.repository.add_observation_event(
                user_id,
                observation_id,
                "baseline_updated",
                "Baseline updated with observation metric.",
                {
                    "baseline_id": baseline_info.get("baseline", {}).get("id"),
                    "metric": metric,
                    "value": value,
                    "previous_mean": baseline_value,
                    "delta": delta,
                },
            )
        decision = evaluate_severity(
            result_info, stats, value, baseline_info if asset_id else None
        )
        severity = decision.severity
        explanation = f"{metric} mean is {value:.4f} for {watch.get('name') or 'watch'}."
        if delta is not None:
            explanation = f"{explanation} Delta from baseline: {delta:.4f}."
        if save_id:
            explanation = f"{explanation} Evidence save id: {save_id}."

        signal = self.repository.create_signal(
            user_id,
            {
                "asset_id": asset_id,
                "watch_id": watch_id,
                "observation_id": observation_id,
                "signal_type": "metric",
                "metric": metric,
                "value": value,
                "baseline_value": baseline_value,
                "delta": delta,
                "severity": severity,
                "confidence": _confidence_from_stats(stats),
                "geometry": asset.get("geometry"),
                "explanation": explanation,
            },
        )
        self.repository.add_observation_event(
            user_id,
            observation_id,
            "signal_created",
            "Metric signal created from observation statistics.",
            {
                "signal_id": signal.get("id"),
                "metric": metric,
                "value": value,
                "severity": severity,
                "severity_provenance": decision.as_event_payload(),
            },
        )
        self._create_configured_deliveries(user_id, observation_id, watch, signal)
        return signal

    def _create_configured_deliveries(
        self,
        user_id: str,
        observation_id: str,
        watch: Dict[str, Any],
        signal: Dict[str, Any],
    ) -> None:
        policy = watch.get("quality_policy") or {}
        targets = policy.get("alert_targets") or policy.get("delivery_targets") or []
        if not isinstance(targets, list):
            return
        # Gate the already-live delivery path so scheduled watches alert
        # only on a real breach, not on every routine observation.
        if not _delivery_allowed(policy, signal):
            return
        for target in targets:
            if not isinstance(target, dict):
                continue
            channel = target.get("channel")
            destination = target.get("target") or target.get("url") or target.get("email")
            if not channel or not destination:
                continue
            delivery = self.repository.create_delivery(
                user_id,
                {
                    "signal_id": signal["id"],
                    "channel": str(channel),
                    "target": str(destination),
                },
            )
            if delivery:
                self.repository.add_observation_event(
                    user_id,
                    observation_id,
                    "delivery_queued",
                    "Signal delivery queued from watch policy.",
                    {"signal_id": signal["id"], "delivery_id": delivery.get("id"), "channel": channel},
                )

    def _mark_failed(
        self,
        user_id: str,
        observation_id: str,
        error: str,
        payload: Optional[Dict[str, Any]] = None,
        retryable: Optional[bool] = None,
    ) -> Dict[str, Any]:
        observation = self.repository.get_observation(user_id, observation_id) or {}
        attempt_count = int(observation.get("attempt_count") or 0)
        category = _failure_category(error)
        should_retry = (
            (retryable if retryable is not None else _is_retryable_error(error))
            and attempt_count < self.max_attempts
        )
        base_payload = {
            "worker_id": self.worker_id,
            "attempt_count": attempt_count,
            "max_attempts": self.max_attempts,
            "failure_category": category,
            "retryable": bool(should_retry),
        }
        if payload:
            base_payload.update(payload)

        if should_retry:
            delay_seconds = min(
                self.retry_max_delay_seconds,
                self.retry_base_delay_seconds * (2 ** max(0, attempt_count - 1)),
            )
            next_attempt_at = _now() + timedelta(seconds=delay_seconds)
            self.repository.update_observation(
                user_id,
                observation_id,
                {
                    "status": "queued",
                    "error": error,
                    "next_attempt_at": next_attempt_at,
                },
                by_user=False,
            )
            self.repository.add_observation_event(
                user_id,
                observation_id,
                "retry_scheduled",
                error,
                {
                    **base_payload,
                    "retry_delay_seconds": delay_seconds,
                    "next_attempt_at": next_attempt_at.isoformat(),
                },
            )
            return {
                "observation_id": observation_id,
                "status": "queued",
                "error": error,
                "retry_scheduled": True,
                "attempt_count": attempt_count,
                "next_attempt_at": next_attempt_at.isoformat(),
                "failure_category": category,
            }

        self.repository.update_observation(
            user_id,
            observation_id,
            {
                "status": "failed",
                "error": error,
                "completed_at": _now(),
            },
            by_user=False,
        )
        self.repository.add_observation_event(
            user_id,
            observation_id,
            "failed",
            error,
            base_payload,
        )
        self._failed_count += 1
        return {
            "observation_id": observation_id,
            "status": "failed",
            "error": error,
            "attempt_count": attempt_count,
            "failure_category": category,
        }
