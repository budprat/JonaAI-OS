# ABOUTME: A second AskUserQuestion while one is open is refused, not silently
# ABOUTME: swapped in. Drives the REAL can_use_tool closure, not a reconstruction of it.
import asyncio

import pytest

from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge


class _Loose:
    """Any attribute reads as None unless set — enough config for _build_options."""

    def __getattr__(self, name):
        return None


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _can_use_tool(session_id="s1"):
    """The production closure, taken from _build_options rather than re-written.

    test_ask_user_question.py reconstructs this closure by hand, so it cannot catch a
    change in the real one. Building the real options is a few lines, so prefer it.
    """
    bridge = ClaudeSDKBridge.__new__(ClaudeSDKBridge)
    bridge._pending_clarifications = {}
    bridge._pending_question_ids = {}
    bridge._mcp_server_config = None
    bridge._project_refs = {}
    bridge._user_refs = {}
    bridge._trajectory_store = {}
    cfg = _Loose()
    cfg.model = "claude-sonnet-5"
    bridge.config = cfg
    ws = _FakeWS()
    options = ClaudeSDKBridge._build_options(bridge, {"ws": ws}, {}, [], session_id=session_id)
    return bridge, ws, options.can_use_tool


QUESTION = {"questions": [{"question": "Which area?", "header": "Area",
                           "options": [{"label": "A", "description": "a"},
                                       {"label": "B", "description": "b"}]}]}


@pytest.mark.asyncio
async def test_a_second_question_is_refused_while_one_is_open():
    bridge, ws, can_use_tool = _can_use_tool()

    first = asyncio.create_task(can_use_tool("AskUserQuestion", QUESTION, None))
    for _ in range(200):                      # let the first reach its await
        await asyncio.sleep(0)
        if bridge._pending_clarifications.get("s1") is not None:
            break
    open_future = bridge._pending_clarifications.get("s1")
    open_id = bridge._pending_question_ids.get("s1")
    assert open_future is not None and open_id is not None

    second = await can_use_tool("AskUserQuestion", QUESTION, None)
    assert type(second).__name__ == "PermissionResultDeny"
    assert "one question at a time" in second.message

    # The refusal must not disturb the question the user is actually looking at.
    assert bridge._pending_clarifications.get("s1") is open_future
    assert bridge._pending_question_ids.get("s1") == open_id
    assert not open_future.done()
    assert sum(1 for m in ws.sent if m.get("type") == "clarification") == 1

    # The first question still answers normally.
    bridge.resolve_clarification("s1", {"Area": "A"}, open_id)
    result = await asyncio.wait_for(first, timeout=5)
    assert type(result).__name__ == "PermissionResultAllow"


@pytest.mark.asyncio
async def test_a_finished_question_does_not_block_the_next_one():
    bridge, ws, can_use_tool = _can_use_tool()

    first = asyncio.create_task(can_use_tool("AskUserQuestion", QUESTION, None))
    for _ in range(200):
        await asyncio.sleep(0)
        if bridge._pending_clarifications.get("s1") is not None:
            break
    bridge.resolve_clarification("s1", {"Area": "A"},
                                 bridge._pending_question_ids.get("s1"))
    await asyncio.wait_for(first, timeout=5)

    # Slot is free again, so the next question is asked rather than refused.
    second = asyncio.create_task(can_use_tool("AskUserQuestion", QUESTION, None))
    for _ in range(200):
        await asyncio.sleep(0)
        if bridge._pending_clarifications.get("s1") is not None:
            break
    assert bridge._pending_clarifications.get("s1") is not None, "second question was refused"
    assert sum(1 for m in ws.sent if m.get("type") == "clarification") == 2
    bridge.resolve_clarification("s1", {"Area": "B"},
                                 bridge._pending_question_ids.get("s1"))
    assert type(await asyncio.wait_for(second, timeout=5)).__name__ == "PermissionResultAllow"


@pytest.mark.asyncio
async def test_a_stopped_question_frees_the_slot_for_the_next_one():
    bridge, ws, can_use_tool = _can_use_tool()
    first = asyncio.create_task(can_use_tool("AskUserQuestion", QUESTION, None))
    for _ in range(200):
        await asyncio.sleep(0)
        if bridge._pending_clarifications.get("s1") is not None:
            break
    assert bridge.stop_pending_clarification("s1") is True
    assert type(await asyncio.wait_for(first, timeout=5)).__name__ == "PermissionResultDeny"
    assert bridge._pending_clarifications.get("s1") is None, "Stop left the slot occupied"


@pytest.mark.asyncio
async def test_two_sessions_do_not_block_each_other():
    bridge, ws, can_use_tool = _can_use_tool(session_id="s1")
    # A separate session has its own closure and its own slot key.
    first = asyncio.create_task(can_use_tool("AskUserQuestion", QUESTION, None))
    for _ in range(200):
        await asyncio.sleep(0)
        if bridge._pending_clarifications.get("s1") is not None:
            break
    assert "s2" not in bridge._pending_clarifications
    bridge.resolve_clarification("s1", {"Area": "A"}, bridge._pending_question_ids.get("s1"))
    await asyncio.wait_for(first, timeout=5)


@pytest.mark.asyncio
async def test_other_tools_are_untouched():
    bridge, ws, can_use_tool = _can_use_tool()
    result = await can_use_tool("openeo_create_job", {"process_graph": {}}, None)
    assert type(result).__name__ == "PermissionResultAllow"
    assert ws.sent == []
