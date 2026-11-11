# ABOUTME: WebSocket-level tests for the chat endpoint dispatch loop:
# ABOUTME: session handshake and authentication gating of message types.

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def ws_client():
    # Import deferred to test time on purpose: a module-level import runs at
    # pytest collection, before per-test env fixtures apply, and pulls the
    # whole app (routers + DB-backed stores) into every other test's process
    # state — it broke test_learned_patterns in full-suite runs.
    from openeo_ai.web_interface import app
    with TestClient(app) as client:
        yield client


def test_connect_receives_session_event(ws_client):
    with ws_client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "session"
        assert msg.get("session_id")


def test_unauthenticated_message_rejected(ws_client):
    with ws_client.websocket_connect("/ws") as ws:
        ws.receive_json()  # session handshake
        ws.send_json({"type": "message", "content": "run ndvi over kerala"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "Authentication required" in msg["content"]


def test_unauthenticated_load_saved_job_rejected(ws_client):
    with ws_client.websocket_connect("/ws") as ws:
        ws.receive_json()  # session handshake
        ws.send_json({"type": "load_saved_job", "save_id": "abcd1234"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "Authentication required" in msg["content"]
