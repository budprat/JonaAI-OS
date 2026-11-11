# ABOUTME: The deadman check is operators-only (the admin scope), every API key expires, and an
# ABOUTME: API key can revoke only itself.
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from openeo_ai.api import router as api_router
from openeo_ai.api.auth import SIGNED_IN_SCOPES, AuthenticatedClient, get_authenticated_client
from openeo_ai.api.schemas import APIKeyCreate
from openeo_ai.intelligence import deadman
from openeo_ai.intelligence import routes as intelligence_routes


# ── The deadman check: it alarms to the deployment's targets and dispatches every user's
# pending deliveries, so only an operator may call it. ──


def _deadman_client(monkeypatch, scopes):
    calls = []

    async def fake_check(*args, **kwargs):
        calls.append(kwargs)
        return {"alive": True}

    monkeypatch.setattr(deadman, "run_deadman_check", fake_check)
    monkeypatch.setattr(intelligence_routes, "EarthIntelligenceRepository", lambda: object())
    monkeypatch.setattr(intelligence_routes.EarthIntelligenceWorker, "from_env",
                        classmethod(lambda cls: SimpleNamespace(dispatch_deliveries=lambda: None)))
    app = FastAPI()
    app.include_router(intelligence_routes.router)
    app.dependency_overrides[get_authenticated_client] = lambda: AuthenticatedClient(
        client_id="u1", user_id="u1", scopes=list(scopes), auth_method="supabase_jwt")
    return TestClient(app), calls


def test_a_signed_in_user_without_the_admin_scope_cannot_run_the_deadman_check(monkeypatch):
    http, calls = _deadman_client(monkeypatch, SIGNED_IN_SCOPES)
    response = http.post("/api/intelligence/deadman/check")
    assert response.status_code == 403
    assert calls == []


def test_an_operator_with_the_admin_scope_can_run_the_deadman_check(monkeypatch):
    http, calls = _deadman_client(monkeypatch, ["admin"])
    response = http.post("/api/intelligence/deadman/check")
    assert response.status_code == 200
    assert response.json() == {"alive": True}
    assert len(calls) == 1


def test_signed_in_users_do_not_get_the_admin_scope_by_default():
    assert "admin" not in SIGNED_IN_SCOPES and "*" not in SIGNED_IN_SCOPES


# ── API keys: every key expires, and a key can revoke only itself ──


def _create(monkeypatch, body, client):
    created = {}

    def fake_create(**kwargs):
        created.update(kwargs)
        return {"id": "key_new", "name": kwargs["name"], "key": "jona_sk_live_x", "prefix": "jona_sk_live_x",
                "scopes": kwargs["scopes"], "created_at": "2026-11-11T00:00:00", "expires_at": None}

    monkeypatch.setattr(api_router, "create_api_key", fake_create)
    asyncio.run(api_router.create_key(body, client))
    return created


def test_a_key_created_without_an_expiry_gets_the_default(monkeypatch):
    client = AuthenticatedClient(client_id="key_a", user_id="u1", scopes=["analyze"], auth_method="api_key")
    created = _create(monkeypatch, APIKeyCreate(name="pipeline", scopes=["analyze"]), client)
    assert created["expires_in_days"] == api_router.DEFAULT_KEY_EXPIRY_DAYS == 90


def test_a_requested_expiry_is_kept(monkeypatch):
    client = AuthenticatedClient(client_id="u1", user_id="u1", scopes=["analyze"], auth_method="supabase_jwt")
    created = _create(monkeypatch, APIKeyCreate(name="pipeline", scopes=["analyze"], expires_in_days=30), client)
    assert created["expires_in_days"] == 30


def test_no_key_can_be_asked_to_live_longer_than_a_year():
    with pytest.raises(ValueError):
        APIKeyCreate(name="forever", expires_in_days=366)


def _revoke(monkeypatch, key_id, client):
    revoked = []
    monkeypatch.setattr(api_router, "revoke_api_key", lambda kid, uid: revoked.append((kid, uid)) or True)
    return asyncio.run(api_router.delete_key(key_id, client)), revoked


def test_an_api_key_cannot_revoke_its_owners_other_keys(monkeypatch):
    client = AuthenticatedClient(client_id="key_a", user_id="u1", scopes=["analyze"], auth_method="api_key")
    with pytest.raises(HTTPException) as refused:
        _revoke(monkeypatch, "key_b", client)
    assert refused.value.status_code == 403


def test_an_api_key_can_revoke_itself(monkeypatch):
    client = AuthenticatedClient(client_id="key_a", user_id="u1", scopes=["analyze"], auth_method="api_key")
    result, revoked = _revoke(monkeypatch, "key_a", client)
    assert result == {"deleted": True, "key_id": "key_a"}
    assert revoked == [("key_a", "u1")]


def test_a_signed_in_user_can_revoke_any_of_their_keys(monkeypatch):
    client = AuthenticatedClient(client_id="u1", user_id="u1", scopes=list(SIGNED_IN_SCOPES),
                                 auth_method="supabase_jwt")
    result, revoked = _revoke(monkeypatch, "key_b", client)
    assert revoked == [("key_b", "u1")]
