# ABOUTME: A dropped WebSocket must not cancel a running query: the run keeps going,
# ABOUTME: buffers numbered events, and a reconnecting owner replays what it missed.
import asyncio
import json
import threading
import time

import pytest


class _FakeSocket:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_json(self, data, **kwargs):
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(data)


@pytest.fixture()
def wi(monkeypatch):
    monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", "false")
    import openeo_ai.web_interface as module  # lazy: module-level import breaks collection

    module._query_runs.clear()
    yield module
    module._query_runs.clear()


def _seqs(socket):
    return [event["seq"] for event in socket.sent]


def test_events_are_numbered_and_buffered_with_no_socket(wi):
    async def scenario():
        run = wi._QueryRun("s", "user-1")
        await run.send_json({"type": "text", "content": "a"})
        await run.send_json({"type": "text", "content": "b"})
        return run

    run = asyncio.run(scenario())
    assert run.seq == 2
    assert [event["seq"] for event, _ in run._events] == [1, 2]
    assert all(event["run_id"] == run.run_id for event, _ in run._events)


def test_attach_replays_only_missed_events_then_goes_live(wi):
    async def scenario():
        run = wi._QueryRun("s", "user-1")
        for content in "abc":
            await run.send_json({"type": "text", "content": content})
        socket = _FakeSocket()
        await run.attach(socket, last_seq=1)
        await run.send_json({"type": "text", "content": "d"})
        return socket

    assert _seqs(asyncio.run(scenario())) == [2, 3, 4]


def test_dead_socket_does_not_raise_into_the_agent(wi):
    async def scenario():
        run = wi._QueryRun("s", "user-1")
        run.attach_new(_FakeSocket(fail=True))
        await run.send_json({"type": "text", "content": "a"})
        await run.send_json({"type": "text", "content": "b"})
        late = _FakeSocket()
        await run.attach(late, last_seq=0)
        return run, late

    run, late = asyncio.run(scenario())
    assert _seqs(late) == [1, 2]
    assert late in run._sockets


def test_stale_connection_cannot_detach_its_replacement(wi):
    async def scenario():
        run = wi._QueryRun("s", "user-1")
        old, new = _FakeSocket(), _FakeSocket()
        run.attach_new(old)
        await run.attach(new, last_seq=0)
        run.detach(old)
        return run, new

    run, new = asyncio.run(scenario())
    assert new in run._sockets


def test_buffer_is_bounded_and_keeps_newest(wi, monkeypatch):
    monkeypatch.setattr(wi, "_RUN_BUFFER_MAX_EVENTS", 3)

    async def scenario():
        run = wi._QueryRun("s", "user-1")
        for i in range(5):
            await run.send_json({"type": "text", "content": str(i)})
        return run

    run = asyncio.run(scenario())
    assert [event["seq"] for event, _ in run._events] == [3, 4, 5]


def test_abandoned_run_is_stopped_unless_a_client_returns(wi, monkeypatch):
    monkeypatch.setattr(wi, "_RUN_ABANDON_SECONDS", 0.05)

    async def scenario(reattach):
        run = wi._QueryRun("s", "user-1")
        socket = _FakeSocket()
        run.attach_new(socket)
        run.start(asyncio.sleep(5))
        run.detach(socket)
        if reattach:
            await run.attach(_FakeSocket(), last_seq=0)
        await asyncio.sleep(0.15)
        stopped = run.stop_event.is_set()
        run.task.cancel()
        return stopped

    assert asyncio.run(scenario(reattach=False)) is True
    assert asyncio.run(scenario(reattach=True)) is False


def test_finished_run_is_forgotten_after_retention(wi, monkeypatch):
    monkeypatch.setattr(wi, "_RUN_RETENTION_SECONDS", 0.05)

    async def scenario():
        run = wi._QueryRun("s", "user-1")
        wi._query_runs["s"] = run
        run.start(asyncio.sleep(0))
        await asyncio.sleep(0.01)
        kept = "s" in wi._query_runs
        await asyncio.sleep(0.1)
        return kept, "s" in wi._query_runs

    assert asyncio.run(scenario()) == (True, False)


# ---- Through the real endpoint ----


class _Bridge:
    class _Sessions:
        def get_session(self, session_id):
            return None

        def get_sdk_session_id(self, session_id):
            return None

    _session_manager = _Sessions()

    def cancel_pending_clarification(self, session_id):
        pass

    async def disconnect_all(self):
        pass


def _recv_until(ws, wanted):
    while True:
        event = ws.receive_json()
        if event.get("type") == wanted:
            return event


def _open(client, wi, token="owner", session="sess-1"):
    ws = client.websocket_connect("/ws").__enter__()
    ws.receive_json()  # session frame
    ws.send_text(json.dumps({"type": "authenticate", "token": token}))
    _recv_until(ws, "auth_result")
    ws.send_text(json.dumps({"type": "restore_session", "session_id": session}))
    _recv_until(ws, "session_restored")
    return ws


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def endpoint(wi, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(
        wi, "verify_supabase_token",
        lambda token: {"sub": token, "email": "t@dev.local"} if token else None,
    )
    release = threading.Event()
    state = {"cancelled": False}

    async def scripted_query(message, session_id, websocket, *args, **kwargs):
        try:
            await websocket.send_json({"type": "text", "content": "before"})
            while not release.is_set():
                await asyncio.sleep(0.01)
            await websocket.send_json({"type": "text", "content": "after"})
            await websocket.send_json({"type": "done"})
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    monkeypatch.setattr(wi, "_run_query", scripted_query)
    # One shared event loop, as in production: the run must outlive a connection.
    with TestClient(wi.app) as client:
        monkeypatch.setattr(wi.app.state, "sdk_bridge", _Bridge(), raising=False)
        yield client, release, state
        release.set()


def test_disconnect_keeps_query_running_and_reconnect_catches_up(wi, endpoint):
    client, release, state = endpoint

    first = _open(client, wi)
    first.send_text(json.dumps({"type": "message", "content": "analyze"}))
    before = _recv_until(first, "text")
    assert before["content"] == "before"
    first.__exit__(None, None, None)

    assert _wait_for(lambda: not wi._query_runs["sess-1"]._sockets)
    release.set()
    run = wi._query_runs["sess-1"]
    assert _wait_for(run.task.done)
    assert state["cancelled"] is False

    second = client.websocket_connect("/ws").__enter__()
    second.receive_json()
    second.send_text(json.dumps({"type": "authenticate", "token": "owner"}))
    _recv_until(second, "auth_result")
    second.send_text(json.dumps({
        "type": "restore_session", "session_id": "sess-1",
        "run_id": before["run_id"], "last_seq": before["seq"],
    }))
    assert _recv_until(second, "session_restored")["run_active"] is False
    resumed = _recv_until(second, "run_resumed")
    assert resumed["running"] is False
    replayed = second.receive_json()
    assert (replayed["content"], replayed["seq"]) == ("after", before["seq"] + 1)
    assert second.receive_json()["type"] == "done"
    second.__exit__(None, None, None)


def test_other_user_cannot_attach_to_a_run(wi, endpoint):
    client, release, _ = endpoint

    owner = _open(client, wi)
    owner.send_text(json.dumps({"type": "message", "content": "analyze"}))
    _recv_until(owner, "text")
    run = wi._query_runs["sess-1"]

    other = client.websocket_connect("/ws").__enter__()
    other.receive_json()
    other.send_text(json.dumps({"type": "authenticate", "token": "someone-else"}))
    _recv_until(other, "auth_result")
    other.send_text(json.dumps({"type": "restore_session", "session_id": "sess-1"}))
    assert _recv_until(other, "session_restored")["run_active"] is False
    other.send_text(json.dumps({"type": "pong"}))

    time.sleep(0.2)
    assert run._sockets
    assert all(socket_event["seq"] == 1 for socket_event, _ in run._events)
    release.set()
    other.__exit__(None, None, None)
    owner.__exit__(None, None, None)


def test_inbox_keeps_every_frame_through_timeouts_and_skips_bad_ones(wi):
    class Reader:
        def __init__(self):
            self.frames = asyncio.Queue()

        async def receive_json(self):
            frame = await self.frames.get()
            if isinstance(frame, Exception):
                raise frame
            return frame

    async def scenario():
        reader = Reader()
        inbox = wi._WsInbox(reader)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(inbox.get(), timeout=0.01)
        for frame in ([], None, json.JSONDecodeError("bad", "", 0), {"type": "message", "content": "turn 2"}):
            reader.frames.put_nowait(frame)
        received = await asyncio.wait_for(inbox.get(), timeout=1)
        reader.frames.put_nowait(RuntimeError("closed"))
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(inbox.get(), timeout=1)
        # The end of the connection stays visible to whoever reads next.
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(inbox.get(), timeout=1)
        inbox.close()
        return received

    assert asyncio.run(scenario())["content"] == "turn 2"


def test_delivery_tracking_tells_a_seen_run_from_an_unseen_one(wi):
    async def scenario():
        seen = wi._QueryRun("s", "u")
        seen.attach_new(_FakeSocket())
        await seen.send_json({"type": "done"})

        unseen = wi._QueryRun("s", "u")
        await unseen.send_json({"type": "done"})

        dropped = wi._QueryRun("s", "u")
        dropped.attach_new(_FakeSocket(fail=True))
        await dropped.send_json({"type": "done"})

        replayed = wi._QueryRun("s", "u")
        await replayed.send_json({"type": "done"})
        await replayed.attach(_FakeSocket(), 0)
        return seen, unseen, dropped, replayed

    seen, unseen, dropped, replayed = asyncio.run(scenario())
    assert seen.fully_delivered
    assert not unseen.fully_delivered
    assert not dropped.fully_delivered
    assert replayed.fully_delivered


def test_events_after_done_are_dropped_except_the_trace(wi, capsys):
    """Stop ends the turn at once; output from the agent winding down must not reach the
    client, where it would land in the next turn. The trace id still arrives after done."""
    async def scenario():
        run = wi._QueryRun("s", "user-1")
        socket = _FakeSocket()
        run.attach_new(socket)
        await run.send_json({"type": "text_delta", "content": "partial"})
        await run.send_json({"type": "done"})
        await run.send_json({"type": "text_delta", "content": "late"})
        await run.send_json({"type": "text_stream_end"})
        await run.send_json({"type": "visualization", "data": {}})
        await run.send_json({"type": "trace", "trace_id": "t1"})
        await run.send_json({"type": "done"})
        return run, socket

    run, socket = asyncio.run(scenario())
    assert [event["type"] for event in socket.sent] == ["text_delta", "done", "trace"]
    assert [event["type"] for event, _ in run._events] == ["text_delta", "done", "trace"]
    assert _seqs(socket) == [1, 2, 3]
    assert capsys.readouterr().out.count("sent after done") == 3

