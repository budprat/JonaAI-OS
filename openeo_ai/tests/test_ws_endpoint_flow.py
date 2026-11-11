"""The real /ws endpoint end to end, with only the query itself replaced."""
import asyncio
import json

import time

import pytest
from starlette.websockets import WebSocketDisconnect


@pytest.fixture()
def endpoint(monkeypatch):
    monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", "false")
    import openeo_ai.web_interface as wi
    from fastapi.testclient import TestClient

    wi._query_runs.clear()
    wi._ws_rate.clear()
    monkeypatch.setattr(wi, "verify_supabase_token", lambda token: {"sub": token, "email": "x@test"})
    state = {"release": None, "started": [], "stopped": []}

    async def fake_query(message, session_id, websocket, client, app_state, stop_event, **kwargs):
        state["started"].append(message)
        await websocket.send_json({"type": "text", "content": f"working on {message}"})
        if message.startswith("slow"):
            for _ in range(200):
                if stop_event.is_set():
                    state["stopped"].append(message)
                    break
                if state["release"] is not None and state["release"].is_set():
                    break
                await asyncio.sleep(0.01)
        await websocket.send_json({"type": "done"})

    monkeypatch.setattr(wi, "_run_query", fake_query)
    with TestClient(wi.app, client=("127.0.0.1", 5000)) as client:
        yield client, wi, state
    wi._query_runs.clear()


def _login(ws, user="user-1"):
    assert ws.receive_json()["type"] == "session"
    ws.send_json({"type": "authenticate", "token": user})
    while ws.receive_json()["type"] != "auth_result":
        pass


def _until(ws, kind):
    seen = []
    while True:
        event = ws.receive_json()
        if event["type"] == "ping":
            continue
        seen.append(event)
        if event["type"] == kind:
            return seen


def _wait_until(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)


def _restore(ws, session, **position):
    ws.send_json({"type": "restore_session", "session_id": session, **position})
    return _until(ws, "session_restored")[-1]


def test_message_needs_login(endpoint):
    client, _, state = endpoint
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "message", "content": "hi"})
        assert "Authentication required" in _until(ws, "error")[-1]["content"]
    assert state["started"] == []


def test_message_runs_to_done_and_bad_frames_do_not_close_the_connection(endpoint):
    client, _, state = endpoint
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        ws.send_text("[]")
        ws.send_text("null")
        ws.send_text("{not json")
        ws.send_json({"type": "message", "content": "hello"})
        assert [e["type"] for e in _until(ws, "done")] == ["text", "done"]
    assert state["started"] == ["hello"]


def test_stop_ends_the_run(endpoint):
    client, _, state = endpoint
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        ws.send_json({"type": "message", "content": "slow one"})
        ws.send_json({"type": "stop"})
        assert _until(ws, "done")[-1]["type"] == "done"
        # The turn ends at once; the agent winds down just behind it.
        _wait_until(lambda: state["stopped"])
    assert state["stopped"] == ["slow one"]


def test_message_sent_during_a_run_is_answered_afterward(endpoint):
    client, wi, state = endpoint
    state["release"] = None
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        ws.send_json({"type": "message", "content": "slow one"})
        ws.send_json({"type": "message", "content": "next one"})
        _until(ws, "done")
        assert "working on next one" in [e.get("content") for e in _until(ws, "done")]
    assert state["started"] == ["slow one", "next one"]


def test_dropped_connection_keeps_the_run_and_the_owner_catches_up(endpoint):
    client, wi, state = endpoint
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        session = _restore(ws, "session-a")["session_id"]
        ws.send_json({"type": "message", "content": "slow one"})
        first = _until(ws, "text")[-1]
    run = wi._query_runs[session]

    with client.websocket_connect("/ws") as ws:
        _login(ws)
        restored = _restore(ws, session, run_id=first["run_id"], last_seq=first["seq"])
        assert restored["run_active"] is True
        events = _until(ws, "done")
        assert events[0]["type"] == "run_resumed"
        assert events[-1]["seq"] == run.seq
        assert all(e["seq"] > first["seq"] for e in events[1:])


def test_another_user_cannot_attach_to_a_run(endpoint):
    client, wi, state = endpoint
    with client.websocket_connect("/ws") as ws:
        _login(ws, "owner")
        _restore(ws, "session-b")
        ws.send_json({"type": "message", "content": "hello"})
        _until(ws, "done")
    with client.websocket_connect("/ws") as ws:
        _login(ws, "someone-else")
        assert _restore(ws, "session-b")["run_active"] is False
        ws.send_json({"type": "message", "content": "mine"})
        events = _until(ws, "done")
        assert "run_resumed" not in [e["type"] for e in events]
        assert "working on hello" not in [e.get("content") for e in events]


def test_new_tab_gets_no_replay_of_a_delivered_run_but_catches_up_on_a_missed_one(endpoint):
    client, wi, state = endpoint
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        _restore(ws, "session-c")
        ws.send_json({"type": "message", "content": "hello"})
        _until(ws, "done")
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        _restore(ws, "session-c")
        ws.send_json({"type": "message", "content": "marker"})
        events = _until(ws, "done")
        assert "run_resumed" not in [e["type"] for e in events]

    async def finish_unseen():
        run = wi._QueryRun("session-d", "user-1")
        await run.send_json({"type": "text", "content": "while away"})
        await run.send_json({"type": "done"})
        run.task = asyncio.get_running_loop().create_future()
        run.task.set_result(None)
        return run

    wi._query_runs["session-d"] = asyncio.new_event_loop().run_until_complete(finish_unseen())
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        _restore(ws, "session-d")
        events = _until(ws, "done")
        assert events[0]["type"] == "run_resumed"
        assert "while away" in [e.get("content") for e in events]


def test_clarification_answer_reaches_the_bridge_during_a_run(endpoint, monkeypatch):
    client, wi, state = endpoint
    answers = []
    bridge = type("Bridge", (), {
        "resolve_clarification": lambda self, session, given, question_id=None: answers.append((given, question_id)),
        "cancel_pending_clarification": lambda self, session: None,
        "disconnect_all": lambda self: asyncio.sleep(0),
        "_session_manager": type("M", (), {
            "get_session": lambda self, sid: None, "get_sdk_session_id": lambda self, sid: None})(),
    })()
    monkeypatch.setattr(wi.app.state, "sdk_bridge", bridge, raising=False)
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        ws.send_json({"type": "message", "content": "slow one"})
        ws.send_json({"type": "clarification_response", "answers": {"0": "North"}, "question_id": "q1"})
        ws.send_json({"type": "stop"})
        _until(ws, "done")
    # The question id travels with the answer, so the bridge can refuse a stale one.
    assert answers == [({"0": "North"}, "q1")]


def test_client_cannot_skip_the_rate_limit_or_restore_anothers_session(endpoint, monkeypatch):
    client, wi, state = endpoint

    class Sessions:
        def get_session(self, session):
            return {"user_id": "someone-else"} if session == "their-session" else None

        def get_sdk_session_id(self, session):
            return None

    monkeypatch.setattr(wi, "_session_store", lambda: Sessions())
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        restored = _restore(ws, "their-session")
        assert restored["session_id"] != "their-session" and restored["has_history"] is False
        monkeypatch.setattr(wi, "_WS_MAX_MSGS_PER_MIN", 1)
        ws.send_json({"type": "message", "content": "one", "_counted": True})
        _until(ws, "done")
        ws.send_json({"type": "message", "content": "two", "_counted": True})
        assert "Rate limit" in _until(ws, "error")[-1]["content"]
    assert state["started"] == ["one"]


def test_a_message_sent_while_a_stopped_run_winds_down_says_it_is_waiting(endpoint):
    client, wi, state = endpoint
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        ws.send_json({"type": "message", "content": "slow one"})
        _until(ws, "text")
        ws.send_json({"type": "stop"})
        _until(ws, "done")  # the turn ends for the user at once
        ws.send_json({"type": "message", "content": "next one"})
        events = _until(ws, "done")
    waiting = [e for e in events if e.get("thinking_id") == wi._QUEUED_STEP_ID]
    assert "stopped analysis" in waiting[0]["content"]
    assert waiting[-1].get("thinking_completed") is True
    assert state["started"] == ["slow one", "next one"]


def test_a_message_queued_behind_a_running_turn_says_so(endpoint):
    client, wi, state = endpoint
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        ws.send_json({"type": "message", "content": "slow one"})
        _until(ws, "text")
        ws.send_json({"type": "message", "content": "next one"})
        notice = next(e for e in iter(ws.receive_json, None) if e.get("thinking_id") == wi._QUEUED_STEP_ID)
        assert "still running" in notice["content"]
        _until(ws, "done")
        _until(ws, "done")


def test_follower_tab_can_stop_a_run(endpoint):
    client, wi, state = endpoint
    with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
        _login(first)
        _login(second)
        _restore(first, "shared-tab")
        _restore(second, "shared-tab")
        first.send_json({"type": "message", "content": "slow one"})
        _until(first, "text")
        second.send_json({"type": "stop"})
        # The turn ends at once; the agent winds down just behind it.
        _until(first, "done")
        _wait_until(lambda: state["stopped"])
    assert state["stopped"] == ["slow one"]


def test_a_second_tab_waits_for_the_run_another_tab_started(endpoint):
    client, wi, state = endpoint
    with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
        _login(first)
        _login(second)
        _restore(first, "two-tabs")
        _restore(second, "two-tabs")
        first.send_json({"type": "message", "content": "slow one"})
        _until(first, "text")
        second.send_json({"type": "message", "content": "slow two"})
        _wait_until(lambda: len(state["started"]) > 1, seconds=0.5)
        running_together = list(state["started"])
        first.send_json({"type": "stop"})
        _until(first, "done")
    assert running_together == ["slow one"], f"two runs on one session at once: {running_together}"


def test_other_sites_cannot_open_the_socket(endpoint):
    client, _, _ = endpoint
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
            pass


def test_message_on_a_session_owned_by_another_account_is_refused(endpoint, monkeypatch):
    client, wi, state = endpoint

    class Sessions:
        def get_session(self, session):
            return {"user_id": "someone-else"}

    # As happens when the owner was recorded after this connection restored the session.
    monkeypatch.setattr(wi, "_session_store", lambda: Sessions())
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        ws.send_json({"type": "message", "content": "hello"})
        assert "another account" in _until(ws, "error")[-1]["content"]
    assert state["started"] == []


def test_a_failed_load_on_map_is_marked_as_not_ending_the_turn(endpoint):
    client, wi, state = endpoint
    with client.websocket_connect("/ws") as ws:
        _login(ws)
        ws.send_json({"type": "load_saved_job", "save_id": "nope0000"})
        error = _until(ws, "error")[-1]
    assert error["code"] == "saved_job_unavailable" and "run_id" not in error


def test_stop_answers_an_open_question_so_the_run_can_wind_down():
    import asyncio
    from types import SimpleNamespace

    from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge
    from openeo_ai.web_interface import _stop_run

    async def scenario():
        bridge = object.__new__(ClaudeSDKBridge)
        question = asyncio.get_running_loop().create_future()
        bridge._pending_clarifications = {"s1": question}
        bridge._pending_question_ids = {"s1": "q1"}
        run = SimpleNamespace(stop_event=asyncio.Event(), session_id="s1",
                              task=asyncio.get_running_loop().create_future())
        run.send_json = lambda event: asyncio.sleep(0)
        run.task.set_result(None)
        _stop_run(run, SimpleNamespace(sdk_bridge=bridge))
        with pytest.raises(RuntimeError, match="stopped"):
            await asyncio.wait_for(question, 1)
        assert bridge._pending_clarifications == {} and bridge._pending_question_ids == {}
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_a_run_whose_log_passed_its_cap_still_recovers_as_finished(tmp_path, monkeypatch):
    import asyncio

    import openeo_ai.web_interface as wi

    monkeypatch.setenv("JONA_RUN_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(wi, "_RUN_BUFFER_MAX_BYTES", 500)
    # An earlier TestClient's shutdown leaves this set, and then a run ends without its done.
    monkeypatch.setattr(wi, "_shutting_down", False)

    async def scenario():
        run = wi._QueryRun("big-run", "u1")
        run.start(asyncio.sleep(0))
        for _ in range(20):
            await run.send_json({"type": "text", "content": "x" * 100})  # far past 500 bytes
        await run.task
        recovered = await wi._recover_run("big-run", "u1")
        return recovered

    recovered = asyncio.run(scenario())
    assert recovered.finished
    assert not any(e.get("type") == "error" for e, _ in recovered._events)
