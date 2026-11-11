"""Rate limit identity, input filter scope, SSRF targets, origins, and route behavior."""
import asyncio
import base64
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from openeo_ai.intelligence.schemas import AssetCreate, AssetUpdate
from openeo_ai.intelligence.worker import DeliveryDispatchError, _validate_webhook_target
from openeo_ai.sdk.inprocess_tools import _access_denied
from openeo_ai.sdk.permissions import check_suspicious_input
from openeo_app.rate_limit import _rate_limit_key


def _request(peer, **headers):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers={k.replace("_", "-"): v for k, v in headers.items()})


def test_visitors_behind_the_tunnel_have_separate_allowances():
    assert _rate_limit_key(_request("127.0.0.1", cf_connecting_ip="1.1.1.1")) == "1.1.1.1"
    assert _rate_limit_key(_request("127.0.0.1", cf_connecting_ip="2.2.2.2")) == "2.2.2.2"


def test_internal_calls_are_not_limited():
    assert _rate_limit_key(_request("127.0.0.1")) is None


def test_a_direct_client_cannot_choose_its_own_identity():
    assert _rate_limit_key(_request("203.0.113.9", cf_connecting_ip="1.1.1.1")) == "203.0.113.9"


@pytest.mark.parametrize(
    "field,text",
    [
        ("prompt", "Assess R&D plots; cost was $5 | see notes"),
        ("question", "What changed; and why?"),
        ("sections", "NDVI & NBR"),
    ],
)
def test_ordinary_text_passes_the_input_filter(field, text):
    assert check_suspicious_input({field: text}) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("file_path", "result.tif; rm -rf /"),
        ("output_path", "x$(whoami).tif"),
        ("job_id", "abc|def"),
        ("url", "http://a/`id`"),
    ],
)
def test_identifiers_with_shell_characters_are_refused(field, value):
    assert "Suspicious" in check_suspicious_input({field: value})


@pytest.mark.parametrize("value", ["../../etc/passwd", "<script>alert(1)</script>", "__import__('os')"])
def test_other_patterns_still_apply_to_text(value):
    assert "Suspicious" in check_suspicious_input({"prompt": value})


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:3210/api/action",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://[::ffff:169.254.169.254]/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://0.0.0.0/x",
        "http://localhost/x",
        "http://224.0.0.1/x",
        "file:///etc/passwd",
        "gopher://example.com/",
    ],
)
def test_delivery_refuses_internal_targets(target, monkeypatch):
    monkeypatch.delenv("JONA_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    with pytest.raises(DeliveryDispatchError):
        asyncio.run(_validate_webhook_target(target))


def test_delivery_allows_a_public_target(monkeypatch):
    monkeypatch.delenv("JONA_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    asyncio.run(_validate_webhook_target("https://8.8.8.8/hook"))


def test_tool_call_with_no_user_is_refused_when_required(monkeypatch):
    monkeypatch.setenv("JONA_REQUIRE_TOOL_USER", "true")
    assert "no signed-in user" in asyncio.run(_access_denied({"file_path": "/tmp/x.tif"}))


def test_tool_call_with_no_user_is_allowed_for_scripts(monkeypatch):
    monkeypatch.delenv("JONA_REQUIRE_TOOL_USER", raising=False)
    assert asyncio.run(_access_denied({"file_path": "/tmp/x.tif"})) is None


def test_user_id_with_a_colon_is_not_sent_as_a_login():
    from openeo_ai.sdk.user_context import set_current_user_id
    from openeo_ai.tools.job_tools import JobTools

    tools = JobTools.__new__(JobTools)
    tools._act_as_user = None
    tools.auth_header = "Basic " + base64.b64encode(b"dev:pw").decode()
    token_holder = set_current_user_id("victim:extra")
    with pytest.raises(ValueError):
        asyncio.run(tools._act_as_current_user(httpx.Request("GET", "http://x/")))
    assert token_holder is not None


@pytest.mark.parametrize("name", ["<script>alert(1)</script>", "a>b"])
def test_asset_names_cannot_carry_markup(name):
    with pytest.raises(ValidationError):
        AssetCreate(name=name)
    with pytest.raises(ValidationError):
        AssetUpdate(name=name)


def test_ordinary_asset_names_pass():
    assert AssetCreate(name="North field & orchard (2024)").name.startswith("North")
    assert AssetUpdate().name is None


class TestRoutes:
    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from openeo_ai.web_interface import app

        return TestClient(app, client=("127.0.0.1", 50000))

    def test_unknown_api_path_is_404_json(self, client):
        response = client.get("/api/nonexistent")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")

    def test_head_on_root_works(self, client):
        assert client.head("/").status_code == 200

    def test_metrics_are_local_only(self, client):
        assert client.get("/metrics").status_code == 200
        assert client.get("/metrics", headers={"CF-Connecting-IP": "1.1.1.1"}).status_code == 404
        assert client.get("/metrics", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 404

    def test_legacy_admin_login_is_gone(self, client):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code in (404, 405)

    def test_asset_delete_requires_login(self, client):
        assert client.delete("/api/assets/abc").status_code == 401


@pytest.mark.parametrize(
    "origin,host,allowed",
    [
        (None, "jonaai.example.org", True),
        ("https://jonaai.example.org", "jonaai.example.org", True),
        ("https://evil.example", "jonaai.example.org", False),
        ("https://jonaai.example.org.evil.example", "jonaai.example.org", False),
        ("null", "jonaai.example.org", False),
    ],
)
def test_websocket_origins(origin, host, allowed):
    from openeo_ai.web_interface import _ws_origin_allowed

    assert _ws_origin_allowed(origin, host) is allowed
