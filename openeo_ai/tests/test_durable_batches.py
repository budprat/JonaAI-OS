# ABOUTME: Tests durable public-API batch state — create/get round-trip,
# ABOUTME: client-scoped access (IDOR), and survival across a simulated restart.

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from openeo_ai.storage import job_archive
from openeo_ai.storage.models import BatchRun


@pytest.fixture
def batch_db():
    # Save/restore _tables_ready with the engine — _get_session gates table
    # creation on it independently, so leaking it would break later tests.
    prev = (job_archive._engine, job_archive._SessionLocal, job_archive._tables_ready)
    # StaticPool + check_same_thread=False: share ONE in-memory DB across
    # threads, so the TestClient request thread sees the same rows this fixture
    # inserted (default :memory: gives each connection its own empty DB).
    job_archive._engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)
    BatchRun.__table__.create(bind=job_archive._engine, checkfirst=True)
    job_archive._tables_ready = True  # table created above
    yield
    job_archive._engine, job_archive._SessionLocal, job_archive._tables_ready = prev


def _sample_batch(batch_id="batch_abc123", client_id="client-a"):
    return {
        "batch_id": batch_id,
        "client_id": client_id,
        "user_id": "user-a",
        "webhook_url": "https://example.test/hook",
        "location_jobs": [
            {"location_id": "L1", "name": "Kharg", "job_id": "job-1"},
            {"location_id": "L2", "name": "Malta", "job_id": "job-2"},
        ],
        "created_at": datetime(2026, 7, 7, 12, 0, 0),
    }


def test_create_get_roundtrip(batch_db):
    job_archive.create_batch(_sample_batch())

    got = job_archive.get_batch("batch_abc123", "client-a")
    assert got is not None
    assert got["batch_id"] == "batch_abc123"
    assert got["client_id"] == "client-a"
    assert got["user_id"] == "user-a"
    assert got["webhook_url"] == "https://example.test/hook"
    assert got["location_jobs"] == [
        {"location_id": "L1", "name": "Kharg", "job_id": "job-1"},
        {"location_id": "L2", "name": "Malta", "job_id": "job-2"},
    ]


def test_created_at_is_datetime_not_string(batch_db):
    # The /batch/{id} response calls created_at.isoformat(); get_batch must
    # return a real datetime, not a JSON-stringified one.
    job_archive.create_batch(_sample_batch())
    got = job_archive.get_batch("batch_abc123", "client-a")
    assert isinstance(got["created_at"], datetime)
    assert got["created_at"].isoformat() == "2026-07-07T12:00:00"


def test_get_batch_denies_other_client(batch_db):
    job_archive.create_batch(_sample_batch(client_id="client-a"))
    # Scoping is enforced in the query — another client sees nothing.
    assert job_archive.get_batch("batch_abc123", "client-b") is None


def test_get_missing_batch_returns_none(batch_db):
    assert job_archive.get_batch("batch_nope", "client-a") is None


def test_survives_simulated_restart(batch_db):
    job_archive.create_batch(_sample_batch())

    # Simulate a process restart: the module-level Session factory is rebound
    # to a NEW session against the SAME engine (the durable store). In-memory
    # dict state would be gone; a DB row is not.
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)

    got = job_archive.get_batch("batch_abc123", "client-a")
    assert got is not None
    assert [lj["job_id"] for lj in got["location_jobs"]] == ["job-1", "job-2"]


# ── End-to-end through the /api/v1/batch endpoint ─────────────────────

def _client_token(client_id):
    import jwt as pyjwt

    from openeo_ai.api import auth

    return pyjwt.encode({"sub": client_id, "scopes": ["*"]}, auth._JWT_SECRET, algorithm="HS256")


def _batch_api_app():
    # Mount ONLY the /api/v1 router — not the full web_interface app. Booting
    # the whole app runs startup hooks that mutate global _pg_compat/job_archive
    # backend state, which then poisons alphabetically-later tests. The router
    # already carries prefix="/api/v1", so paths are unchanged.
    from fastapi import FastAPI

    from openeo_ai.api.router import router as api_v1_router

    app = FastAPI()
    app.include_router(api_v1_router)
    return app


def test_get_batch_endpoint_returns_200_for_owner(batch_db):
    from fastapi.testclient import TestClient

    job_archive.create_batch(_sample_batch(client_id="client-x"))
    token = _client_token("client-x")
    with TestClient(_batch_api_app()) as client:
        resp = client.get(
            "/api/v1/batch/batch_abc123", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["batch_id"] == "batch_abc123"
    assert body["total_locations"] == 2
    # Jobs are volatile, so per-location status resolves to "unknown" — the
    # batch itself still returns 200 with both locations (the 404 fix).
    assert {loc["status"] for loc in body["locations"]} == {"unknown"}
    # A batch whose jobs were lost can't finish, so it must not say "running" forever.
    assert (body["status"], body["unknown"]) == ("unknown", 2)


def test_get_batch_endpoint_denies_other_client(batch_db):
    from fastapi.testclient import TestClient

    job_archive.create_batch(_sample_batch(client_id="client-x"))
    token = _client_token("client-y")
    with TestClient(_batch_api_app()) as client:
        resp = client.get(
            "/api/v1/batch/batch_abc123", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 404


@pytest.mark.parametrize("statuses,overall", [
    ({"job-1": "completed", "job-2": "completed"}, "completed"),
    ({"job-1": "completed", "job-2": "failed"}, "failed"),
    ({"job-1": "completed", "job-2": "cancelled"}, "cancelled"),
    ({"job-1": "completed", "job-2": "running"}, "running"),
    ({"job-1": "completed"}, "unknown"),  # job-2 lost in a restart
])
def test_batch_status_settles_from_its_locations(batch_db, monkeypatch, statuses, overall):
    from fastapi.testclient import TestClient

    from openeo_ai.api import jobs

    monkeypatch.setattr(jobs, "_jobs", {job_id: {"status": status} for job_id, status in statuses.items()})
    job_archive.create_batch(_sample_batch(client_id="client-x"))
    with TestClient(_batch_api_app()) as client:
        body = client.get(
            "/api/v1/batch/batch_abc123", headers={"Authorization": f"Bearer {_client_token('client-x')}"}
        ).json()
    assert body["status"] == overall
    from openeo_ai.api.schemas import BatchStatusResponse

    BatchStatusResponse.model_validate(body)  # the documented schema matches what's returned

