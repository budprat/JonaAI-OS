# ABOUTME: Verifies STAC search retries transient transport errors but leaves
# ABOUTME: HTTP status errors terminal.
import asyncio

import httpx
import pytest

from openeo_ai.intelligence.catalog import CatalogDiscoveryService


async def _no_sleep(*_a, **_k):
    return None


@pytest.mark.asyncio
async def test_stac_search_retries_transport_error(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    cat = CatalogDiscoveryService(stac_api_url="https://stac.test", provider="test")
    calls = {"n": 0}

    async def flaky_post(self, body):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("boom")
        return {"features": []}

    # Bind the undecorated body via the decorated wrapper on the class.
    monkeypatch.setattr(CatalogDiscoveryService, "_post_stac_search_impl", flaky_post, raising=False)
    result = await cat._post_stac_search({"collections": ["s2"]})
    assert result == {"features": []}
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_stac_search_http_status_error_is_terminal(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    cat = CatalogDiscoveryService(stac_api_url="https://stac.test", provider="test")
    calls = {"n": 0}

    async def failing_post(self, body):
        calls["n"] += 1
        request = httpx.Request("POST", "https://stac.test/search")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr(CatalogDiscoveryService, "_post_stac_search_impl", failing_post, raising=False)
    with pytest.raises(httpx.HTTPStatusError):
        await cat._post_stac_search({"collections": ["s2"]})
    assert calls["n"] == 1
