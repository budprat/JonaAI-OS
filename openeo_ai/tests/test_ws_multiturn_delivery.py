# ABOUTME: Messages arriving while a query task finishes its tail must not be
# ABOUTME: dropped — the poll loop ate next-turn messages and clarification answers.
import concurrent.futures
import json
import time

import pytest


class _BridgeRecorder:
    def __init__(self):
        self.clarifications = []

    def resolve_clarification(self, session_id, answers, question_id=None):
        self.clarifications.append((session_id, answers))


def _recv_until(ws, wanted_type, timeout=8.0):
    """Receive frames (skipping pings/others) until wanted_type or timeout."""
    deadline = time.monotonic() + timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            fut = pool.submit(ws.receive_json)
            try:
                ev = fut.result(timeout=max(0.1, remaining))
            except concurrent.futures.TimeoutError:
                return None
            if ev.get("type") == wanted_type:
                return ev
    return None


@pytest.fixture()
def ws_app(monkeypatch):
    monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", "false")
    import openeo_ai.web_interface as wi  # lazy: module-level import breaks collection

    monkeypatch.setattr(
        wi, "verify_supabase_token",
        lambda token: {"sub": "user-1", "email": "t@dev.local"} if token else None,
    )
    bridge = _BridgeRecorder()
    wi.app.state.sdk_bridge = bridge
    return wi, bridge


def _connect_and_auth(client_cls, wi):
    client = client_cls(wi.app)
    ws = client.websocket_connect("/ws").__enter__()
    ws.receive_json()  # session frame
    ws.send_text(json.dumps({"type": "authenticate", "token": "t"}))
    assert _recv_until(ws, "auth_result") is not None
    return ws


def test_message_sent_during_query_tail_is_processed(ws_app, monkeypatch):
    """Regression for the live multi-turn hang: turn 2 arrived in the window
    between the client-visible done and query_task completion; the poll loop
    consumed it without handling, and the session never answered again."""
    wi, _ = ws_app
    from starlette.testclient import TestClient

    calls = []

    async def _scripted_query(message, session_id, websocket, *args, **kwargs):
        calls.append(message)
        await websocket.send_json({"type": "done"})
        import asyncio
        await asyncio.sleep(1.2)  # tail work after done (trajectory save etc.)

    monkeypatch.setattr(wi, "_run_query", _scripted_query)

    ws = _connect_and_auth(TestClient, wi)
    try:
        ws.send_text(json.dumps({"type": "message", "content": "turn one"}))
        assert _recv_until(ws, "done") is not None
        # Land turn 2 inside the 1.2s tail window
        ws.send_text(json.dumps({"type": "message", "content": "turn two"}))
        assert _recv_until(ws, "done", timeout=10.0) is not None, (
            "turn 2 was never processed — dropped by the query poll loop"
        )
        assert calls == ["turn one", "turn two"]
    finally:
        ws.close()


def test_clarification_response_during_query_reaches_bridge(ws_app, monkeypatch):
    """AskUserQuestion answers only ever arrive mid-query — the poll loop must
    resolve them, not drop them into the 120s timeout."""
    wi, bridge = ws_app
    from starlette.testclient import TestClient

    async def _scripted_query(message, session_id, websocket, *args, **kwargs):
        import asyncio
        await asyncio.sleep(1.5)  # stay in-flight while the answer arrives
        await websocket.send_json({"type": "done"})

    monkeypatch.setattr(wi, "_run_query", _scripted_query)

    ws = _connect_and_auth(TestClient, wi)
    try:
        ws.send_text(json.dumps({"type": "message", "content": "needs clarification"}))
        time.sleep(0.3)  # query now in-flight; poll loop is the reader
        ws.send_text(json.dumps({"type": "clarification_response", "answers": {"q": "a"}}))
        assert _recv_until(ws, "done", timeout=10.0) is not None
        assert bridge.clarifications, (
            "clarification_response was dropped by the query poll loop"
        )
        assert bridge.clarifications[0][1] == {"q": "a"}
    finally:
        ws.close()
