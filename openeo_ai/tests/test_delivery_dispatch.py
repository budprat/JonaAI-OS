# ABOUTME: Tests for Earth Intelligence delivery dispatch: claiming semantics,
# ABOUTME: channel senders against a real local HTTP listener, retry rules.

import asyncio
import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from openeo_ai.intelligence import repository as repo_mod
from openeo_ai.intelligence.repository import EarthIntelligenceRepository
from openeo_ai.intelligence.worker import EarthIntelligenceWorker
from openeo_ai.storage import job_archive


def _memory_repo() -> EarthIntelligenceRepository:
    # The worker offloads its DB calls to pool threads (asyncio.to_thread), so
    # the in-memory DB must be shared across threads — StaticPool keeps one
    # connection and check_same_thread lets other threads use it. Mirrors the
    # thread-safe Postgres pool used in production.
    job_archive._engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)
    job_archive._tables_ready = False
    repo_mod._tables_ready = False
    return EarthIntelligenceRepository()


class _RecordingHandler(BaseHTTPRequestHandler):
    """Records every POST it receives; replies with the next queued status code."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(body.decode("utf-8"))
        except Exception:
            parsed = None
        self.server.received.append({"path": self.path, "json": parsed})
        code = self.server.status_codes.pop(0) if self.server.status_codes else 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, fmt, *args):
        return  # keep test output pristine


@pytest.fixture()
def http_listener():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    server.received = []
    server.status_codes = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _listener_url(server, path="/hook"):
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _delivery_setup(repo, channel, target):
    asset = repo.create_asset("user-1", {"name": "Site", "bbox": [10.0, 20.0, 10.1, 20.1]})
    signal = repo.create_signal(
        "user-1",
        {
            "asset_id": asset["id"],
            "metric": "NDVI",
            "value": 0.42,
            "severity": "warning",
            "explanation": "NDVI dropped below baseline.",
        },
    )
    delivery = repo.create_delivery(
        "user-1", {"signal_id": signal["id"], "channel": channel, "target": target}
    )
    return signal, delivery


def test_webhook_delivery_success(http_listener, monkeypatch):
    monkeypatch.setenv("JONA_ALLOW_PRIVATE_WEBHOOKS", "true")
    repo = _memory_repo()
    signal, delivery = _delivery_setup(repo, "webhook", _listener_url(http_listener))
    worker = EarthIntelligenceWorker(repository=repo)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "sent"}]
    row = repo.list_deliveries("user-1", status="sent")[0]
    assert row["attempt_count"] == 1
    assert row["sent_at"] is not None
    assert len(http_listener.received) == 1
    payload = http_listener.received[0]["json"]
    assert payload["signal_id"] == signal["id"]
    assert payload["severity"] == "warning"
    assert payload["metric"] == "NDVI"
    assert payload["asset_id"] == signal["asset_id"]


def test_webhook_500_is_retryable(http_listener, monkeypatch):
    monkeypatch.setenv("JONA_ALLOW_PRIVATE_WEBHOOKS", "true")
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "webhook", _listener_url(http_listener))
    http_listener.status_codes.append(500)
    worker = EarthIntelligenceWorker(repository=repo)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "pending"}]
    row = repo.list_deliveries("user-1", status="pending")[0]
    assert row["attempt_count"] == 1
    assert "500" in row["last_error"]


def test_webhook_exhausted_attempts_fail(http_listener, monkeypatch):
    monkeypatch.setenv("JONA_ALLOW_PRIVATE_WEBHOOKS", "true")
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "webhook", _listener_url(http_listener))
    worker = EarthIntelligenceWorker(repository=repo)
    repo.update_delivery(
        "user-1", delivery["id"], {"attempt_count": worker.max_attempts - 1}
    )
    http_listener.status_codes.append(503)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "failed"}]
    row = repo.list_deliveries("user-1", status="failed")[0]
    assert row["attempt_count"] == worker.max_attempts


def test_webhook_4xx_is_not_retryable(http_listener, monkeypatch):
    monkeypatch.setenv("JONA_ALLOW_PRIVATE_WEBHOOKS", "true")
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "webhook", _listener_url(http_listener))
    http_listener.status_codes.append(404)
    worker = EarthIntelligenceWorker(repository=repo)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "failed"}]
    row = repo.list_deliveries("user-1", status="failed")[0]
    assert row["attempt_count"] == 1
    assert "404" in row["last_error"]


def test_unknown_channel_fails_immediately(monkeypatch):
    monkeypatch.setenv("JONA_ALLOW_PRIVATE_WEBHOOKS", "true")
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "carrier-pigeon", "coop-7")
    worker = EarthIntelligenceWorker(repository=repo)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "failed"}]
    row = repo.list_deliveries("user-1", status="failed")[0]
    assert row["attempt_count"] == 1
    assert "channel" in row["last_error"]


def test_email_without_smtp_config_fails_cleanly(monkeypatch):
    monkeypatch.delenv("JONA_SMTP_HOST", raising=False)
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "email", "ops@example.test")
    worker = EarthIntelligenceWorker(repository=repo)

    # Must not raise even though the channel cannot be used.
    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "failed"}]
    row = repo.list_deliveries("user-1", status="failed")[0]
    assert "SMTP not configured" in row["last_error"]


def test_slack_delivery_posts_text(http_listener, monkeypatch):
    monkeypatch.setenv("JONA_ALLOW_PRIVATE_WEBHOOKS", "true")
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "slack", _listener_url(http_listener, "/slack"))
    worker = EarthIntelligenceWorker(repository=repo)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "sent"}]
    assert len(http_listener.received) == 1
    payload = http_listener.received[0]["json"]
    assert "text" in payload
    assert "NDVI" in payload["text"]


def test_ssrf_guard_blocks_hostname_resolving_to_private_ip(http_listener, monkeypatch):
    import socket

    from openeo_ai.intelligence import worker as worker_module

    def resolves_to_private(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(worker_module.socket, "getaddrinfo", resolves_to_private)
    monkeypatch.delenv("JONA_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "webhook", "http://internal.example.com/hook")
    worker = EarthIntelligenceWorker(repository=repo)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "failed"}]
    assert http_listener.received == []


def test_ssrf_guard_blocks_integer_ip_notation(http_listener, monkeypatch):
    import socket

    from openeo_ai.intelligence import worker as worker_module

    def resolves_to_loopback(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(worker_module.socket, "getaddrinfo", resolves_to_loopback)
    monkeypatch.delenv("JONA_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    repo = _memory_repo()
    # 2130706433 == 0x7F000001 == 127.0.0.1 in legacy integer notation; it is
    # not a parseable IP literal, so only address resolution can catch it.
    _, delivery = _delivery_setup(repo, "webhook", "http://2130706433/hook")
    worker = EarthIntelligenceWorker(repository=repo)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "failed"}]
    assert http_listener.received == []


def test_ssrf_guard_blocks_private_target_by_default(http_listener, monkeypatch):
    monkeypatch.delenv("JONA_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    repo = _memory_repo()
    _, delivery = _delivery_setup(
        repo, "webhook", "http://169.254.169.254/latest/meta-data"
    )
    worker = EarthIntelligenceWorker(repository=repo)

    results = asyncio.run(worker.dispatch_deliveries())

    assert results == [{"id": delivery["id"], "status": "failed"}]
    row = repo.list_deliveries("user-1", status="failed")[0]
    assert row["attempt_count"] == 1
    assert http_listener.received == []


def test_claim_is_exclusive():
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "webhook", "https://example.test/hook")

    first = repo.claim_pending_deliveries()
    second = repo.claim_pending_deliveries()

    assert [d["id"] for d in first] == [delivery["id"]]
    assert first[0]["severity"] == "warning"
    assert first[0]["metric"] == "NDVI"
    assert second == []


def test_stale_sending_rows_are_reclaimed():
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "webhook", "https://example.test/hook")
    repo.update_delivery(
        "user-1",
        delivery["id"],
        {
            "status": "sending",
            "created_at": datetime.utcnow() - timedelta(minutes=20),
        },
    )

    claimed = repo.claim_pending_deliveries()

    assert [d["id"] for d in claimed] == [delivery["id"]]


def test_tick_dispatches_pending_deliveries(http_listener, monkeypatch):
    monkeypatch.setenv("JONA_ALLOW_PRIVATE_WEBHOOKS", "true")
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "webhook", _listener_url(http_listener))
    worker = EarthIntelligenceWorker(repository=repo)

    result = asyncio.run(worker.tick(max_observations=0))

    assert result["deliveries"] == [{"id": delivery["id"], "status": "sent"}]
    assert len(http_listener.received) == 1


def test_tick_for_user_dispatches_only_that_users_deliveries(http_listener, monkeypatch):
    monkeypatch.setenv("JONA_ALLOW_PRIVATE_WEBHOOKS", "true")
    repo = _memory_repo()
    _, delivery = _delivery_setup(repo, "webhook", _listener_url(http_listener))
    worker = EarthIntelligenceWorker(repository=repo)

    other = asyncio.run(worker.tick_for_user("user-2", max_observations=0))
    assert other["deliveries"] == []
    assert http_listener.received == []

    mine = asyncio.run(worker.tick_for_user("user-1", max_observations=0))
    assert mine["deliveries"] == [{"id": delivery["id"], "status": "sent"}]
    assert len(http_listener.received) == 1
