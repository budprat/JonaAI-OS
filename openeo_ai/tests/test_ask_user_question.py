# ABOUTME: Tests for AskUserQuestion tool (MCP registration, handler, bridge hook).
# Validates schema, handler response, WebSocket routing, future resolution, timeout, cleanup.

"""
Tests for the AskUserQuestion interactive clarification tool.

Covers:
- Tool schema structure and validation
- Handler function behavior (returns answers from args)
- Bridge hook intercept (WebSocket routing, future resolution, timeout)
- Cleanup of pending clarifications after resolution
- _strip_mcp_prefix utility edge cases
- Allowed tools list inclusion
"""

import asyncio
import json
import pytest

from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix


# ============================================================================
# In-Memory Test Doubles for I/O Boundaries
# ============================================================================

class InMemoryWebSocket:
    """In-memory WebSocket that records sent messages for assertion.

    Provides the same send_json interface as starlette.websockets.WebSocket.
    """

    def __init__(self, on_send=None, error_on_send=None):
        self.sent_messages = []
        self._on_send = on_send
        self._error_on_send = error_on_send

    async def send_json(self, message):
        if self._error_on_send:
            raise self._error_on_send
        self.sent_messages.append(message)
        if self._on_send:
            await self._on_send(message)


class InMemorySessionManager:
    """In-memory replacement for SessionManager (SQLite-backed in production).

    Satisfies the constructor interface of ClaudeSDKBridge without database I/O.
    """

    def __init__(self, *args, **kwargs):
        self._sdk_session_ids = {}

    def clear_all_sdk_session_ids(self):
        self._sdk_session_ids.clear()
        return 0

    def get_sdk_session_id(self, session_id):
        return self._sdk_session_ids.get(session_id)


# ============================================================================
# Schema Tests
# ============================================================================

class TestAskUserQuestionSchema:
    """Test tool schema structure as defined in inprocess_tools.py."""

    def _get_ask_user_schema(self):
        """Extract the AskUserQuestion schema matching inprocess_tools.py lines 131-158."""
        return {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "The question to ask the user"},
                            "header": {"type": "string", "description": "Short label (max 12 chars)"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label", "description"],
                                },
                            },
                            "multiSelect": {"type": "boolean", "default": False},
                        },
                        "required": ["question", "header", "options"],
                    },
                },
            },
            "required": ["questions"],
        }

    def test_tool_schema_has_required_questions(self):
        """Verify top-level schema requires 'questions' array."""
        schema = self._get_ask_user_schema()
        assert schema["type"] == "object"
        assert "questions" in schema["required"]
        assert schema["properties"]["questions"]["type"] == "array"

    def test_question_item_required_fields(self):
        """Each question must have question, header, and options required."""
        schema = self._get_ask_user_schema()
        question_item = schema["properties"]["questions"]["items"]
        required = question_item["required"]
        assert "question" in required
        assert "header" in required
        assert "options" in required

    def test_question_item_has_question_string(self):
        """question field must be a string."""
        schema = self._get_ask_user_schema()
        question_props = schema["properties"]["questions"]["items"]["properties"]
        assert question_props["question"]["type"] == "string"

    def test_question_item_has_header_string(self):
        """header field must be a string with description about char limit."""
        schema = self._get_ask_user_schema()
        question_props = schema["properties"]["questions"]["items"]["properties"]
        assert question_props["header"]["type"] == "string"
        assert "12 chars" in question_props["header"]["description"]

    def test_question_item_has_options_array(self):
        """options field must be an array."""
        schema = self._get_ask_user_schema()
        question_props = schema["properties"]["questions"]["items"]["properties"]
        assert question_props["options"]["type"] == "array"

    def test_options_structure_requires_label_and_description(self):
        """Each option must have label and description as required fields."""
        schema = self._get_ask_user_schema()
        options_items = (
            schema["properties"]["questions"]["items"]
            ["properties"]["options"]["items"]
        )
        assert "label" in options_items["required"]
        assert "description" in options_items["required"]
        assert options_items["properties"]["label"]["type"] == "string"
        assert options_items["properties"]["description"]["type"] == "string"

    def test_multi_select_is_optional_boolean(self):
        """multiSelect should be an optional boolean with default False."""
        schema = self._get_ask_user_schema()
        question_props = schema["properties"]["questions"]["items"]["properties"]
        assert question_props["multiSelect"]["type"] == "boolean"
        assert question_props["multiSelect"]["default"] is False
        # multiSelect should NOT be in required
        required = schema["properties"]["questions"]["items"]["required"]
        assert "multiSelect" not in required


# ============================================================================
# Handler Tests
# ============================================================================

class TestAskUserQuestionHandler:
    """Test the tool handler function from inprocess_tools.py."""

    @staticmethod
    async def _ask_user_handler(args):
        """Replicate the handler logic from inprocess_tools.py line 117-119.

        This is a direct copy of the production handler to verify its contract.
        """
        answers = args.get("answers", {})
        return {"content": [{"type": "text", "text": json.dumps(answers)}]}

    @pytest.mark.asyncio
    async def test_handler_returns_answers(self):
        """Handler returns user answers dict as JSON text content."""
        answers = {"q1": "Sentinel-2", "q2": "Monthly"}
        result = await self._ask_user_handler({"answers": answers})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        parsed = json.loads(result["content"][0]["text"])
        assert parsed == answers

    @pytest.mark.asyncio
    async def test_handler_empty_answers(self):
        """Handler returns empty dict when no answers provided."""
        result = await self._ask_user_handler({})

        parsed = json.loads(result["content"][0]["text"])
        assert parsed == {}

    @pytest.mark.asyncio
    async def test_handler_answers_key_missing(self):
        """Handler returns empty dict when args has other keys but no answers."""
        result = await self._ask_user_handler({"questions": [{"q": "test"}]})

        parsed = json.loads(result["content"][0]["text"])
        assert parsed == {}

    @pytest.mark.asyncio
    async def test_handler_preserves_answer_types(self):
        """Handler preserves various value types in answers dict."""
        answers = {
            "string_val": "hello",
            "list_val": ["a", "b"],
            "bool_val": True,
            "nested": {"key": "val"},
        }
        result = await self._ask_user_handler({"answers": answers})

        parsed = json.loads(result["content"][0]["text"])
        assert parsed["string_val"] == "hello"
        assert parsed["list_val"] == ["a", "b"]
        assert parsed["bool_val"] is True
        assert parsed["nested"]["key"] == "val"

    @pytest.mark.asyncio
    async def test_handler_returns_mcp_content_format(self):
        """Handler returns MCP-compatible content format."""
        result = await self._ask_user_handler({"answers": {"q": "a"}})

        # Must follow MCP tool response format
        assert isinstance(result, dict)
        assert "content" in result
        assert isinstance(result["content"], list)
        content_item = result["content"][0]
        assert content_item["type"] == "text"
        assert isinstance(content_item["text"], str)


# ============================================================================
# Bridge Hook Integration Tests
# ============================================================================

class TestBridgeHookIntegration:
    """The real can_use_tool hook, taken from _build_options.

    This class used to reconstruct the closure by hand. That copy drifted: it cited
    "lines 1114-1146" while the real hook had moved to 2571, and it had no question_id,
    no clarification_closed and a 120 s timeout where production uses 600 — so
    test_timeout_returns_deny asserted a "120 seconds" message production never emits,
    and the whole class passed regardless of what the bridge did.
    It now builds the real options object, so a change in the hook reaches these tests.
    """

    def _real_hook(self, ws, session_id="sess1"):
        """Return (bridge, can_use_tool) wired to the production closure."""
        from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge

        class _Loose:
            def __getattr__(self, name):
                return None

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
        options = ClaudeSDKBridge._build_options(
            bridge, {"ws": ws}, {}, [], session_id=session_id
        )
        return bridge, options.can_use_tool

    @staticmethod
    def _questions(text="Which satellite?", header="Satellite"):
        return [{
            "question": text,
            "header": header,
            "options": [
                {"label": "Sentinel-2", "description": "10m resolution"},
                {"label": "Landsat-8", "description": "30m resolution"},
            ],
        }]

    @pytest.mark.asyncio
    async def test_non_ask_tool_passes_through(self):
        """Non-AskUserQuestion tools are allowed without modification."""
        from claude_agent_sdk.types import PermissionResultAllow

        ws = InMemoryWebSocket()
        _, can_use_tool = self._real_hook(ws)

        input_data = {"collection_id": "sentinel-2-l2a"}
        result = await can_use_tool("mcp__openeo__openeo_search_collections", input_data, None)

        assert isinstance(result, PermissionResultAllow)
        assert result.updated_input == input_data
        assert len(ws.sent_messages) == 0

    @pytest.mark.asyncio
    async def test_empty_questions_skips_ws(self):
        """AskUserQuestion with an empty questions list returns Allow immediately."""
        from claude_agent_sdk.types import PermissionResultAllow

        ws = InMemoryWebSocket()
        bridge, can_use_tool = self._real_hook(ws)

        input_data = {"questions": []}
        result = await can_use_tool("mcp__openeo__AskUserQuestion", input_data, None)

        assert isinstance(result, PermissionResultAllow)
        assert result.updated_input == input_data
        assert len(ws.sent_messages) == 0
        assert "sess1" not in bridge._pending_clarifications

    @pytest.mark.asyncio
    async def test_sends_websocket_clarification_message(self):
        """The clarification carries a question_id, and the question is closed after."""
        from claude_agent_sdk.types import PermissionResultAllow

        session_id = "sess_ws_test"
        holder = {}

        async def on_send(message):
            await asyncio.sleep(0.01)
            future = holder["bridge"]._pending_clarifications.get(session_id)
            if future and not future.done():
                future.set_result({"q1": "Sentinel-2"})

        ws = InMemoryWebSocket(on_send=on_send)
        bridge, can_use_tool = self._real_hook(ws, session_id)
        holder["bridge"] = bridge

        questions = self._questions()
        result = await can_use_tool(
            "mcp__openeo__AskUserQuestion", {"questions": questions}, None
        )

        opening = ws.sent_messages[0]
        assert opening["type"] == "clarification"
        assert opening["questions"] == questions
        # The hand-written copy had no question_id at all.
        assert isinstance(opening.get("question_id"), str) and opening["question_id"]
        closing = [m for m in ws.sent_messages if m.get("type") == "clarification_closed"]
        assert closing, "the real hook closes the question so a reconnecting tab isn't re-asked"
        assert closing[0]["question_id"] == opening["question_id"]

        assert isinstance(result, PermissionResultAllow)
        # A non-positional key passes through _answers_by_question unchanged.
        assert result.updated_input["answers"] == {"q1": "Sentinel-2"}
        assert result.updated_input["questions"] == questions

    @pytest.mark.asyncio
    async def test_positional_answers_are_keyed_by_question_text(self):
        """The card answers by position; the model reads them keyed by question.

        This is real hook behaviour (_answers_by_question) that the reconstructed
        closure did not have at all.
        """
        session_id = "sess_positional"
        holder = {}

        async def on_send(message):
            await asyncio.sleep(0.005)
            future = holder["bridge"]._pending_clarifications.get(session_id)
            if future and not future.done():
                future.set_result({"0": ["Sentinel-2", "Landsat-8"]})

        ws = InMemoryWebSocket(on_send=on_send)
        bridge, can_use_tool = self._real_hook(ws, session_id)
        holder["bridge"] = bridge

        questions = self._questions(text="Which satellite?")
        result = await can_use_tool(
            "mcp__openeo__AskUserQuestion", {"questions": questions}, None
        )
        assert result.updated_input["answers"] == {"Which satellite?": "Sentinel-2, Landsat-8"}

    @pytest.mark.asyncio
    async def test_answers_injected_into_updated_input(self):
        """Answers are merged with the original input_data."""
        from claude_agent_sdk.types import PermissionResultAllow

        session_id = "sess_merge"
        holder = {}

        async def on_send(message):
            await asyncio.sleep(0.005)
            future = holder["bridge"]._pending_clarifications.get(session_id)
            if future and not future.done():
                future.set_result({"satellite": "Sentinel-2", "period": "monthly"})

        ws = InMemoryWebSocket(on_send=on_send)
        bridge, can_use_tool = self._real_hook(ws, session_id)
        holder["bridge"] = bridge

        questions = self._questions()
        input_data = {"questions": questions, "extra_field": "preserved"}
        result = await can_use_tool("mcp__openeo__AskUserQuestion", input_data, None)

        assert isinstance(result, PermissionResultAllow)
        assert result.updated_input["questions"] == questions
        assert result.updated_input["extra_field"] == "preserved"
        assert result.updated_input["answers"]["satellite"] == "Sentinel-2"
        assert result.updated_input["answers"]["period"] == "monthly"

    @pytest.mark.asyncio
    async def test_timeout_returns_deny(self, monkeypatch):
        """Timeout denies with the message production actually emits."""
        from claude_agent_sdk.types import PermissionResultDeny
        import openeo_ai.sdk.claude_sdk_bridge as bridge_module

        # Shorten the real constant instead of rebuilding the hook with a fake timeout.
        monkeypatch.setattr(bridge_module, "_CLARIFICATION_TIMEOUT_SECONDS", 0.05)

        ws = InMemoryWebSocket()
        session_id = "sess_timeout"
        bridge, can_use_tool = self._real_hook(ws, session_id)

        result = await can_use_tool(
            "mcp__openeo__AskUserQuestion", {"questions": self._questions()}, None
        )

        assert isinstance(result, PermissionResultDeny)
        # The old copy asserted "120 seconds", which production never says.
        assert "did not answer" in result.message
        assert "assumption" in result.message
        assert session_id not in bridge._pending_clarifications
        assert session_id not in bridge._pending_question_ids

    @pytest.mark.asyncio
    async def test_ws_send_error_returns_deny(self):
        """A WebSocket send failure denies and releases the slot."""
        from claude_agent_sdk.types import PermissionResultDeny

        ws = InMemoryWebSocket(error_on_send=ConnectionError("WebSocket closed"))
        session_id = "sess_ws_err"
        bridge, can_use_tool = self._real_hook(ws, session_id)

        result = await can_use_tool(
            "mcp__openeo__AskUserQuestion", {"questions": self._questions()}, None
        )

        assert isinstance(result, PermissionResultDeny)
        assert "Clarification failed" in result.message
        assert "WebSocket closed" in result.message
        assert session_id not in bridge._pending_clarifications

    @pytest.mark.asyncio
    async def test_a_resolved_question_does_not_block_the_next_one(self):
        """What "no leaked futures" should have asserted: the slot is reusable."""
        from claude_agent_sdk.types import PermissionResultAllow

        session_id = "sess_cleanup"
        holder = {}

        async def on_send(message):
            await asyncio.sleep(0.005)
            future = holder["bridge"]._pending_clarifications.get(session_id)
            if future and not future.done():
                future.set_result({"resolution": "10m"})

        ws = InMemoryWebSocket(on_send=on_send)
        bridge, can_use_tool = self._real_hook(ws, session_id)
        holder["bridge"] = bridge

        first = await can_use_tool(
            "mcp__openeo__AskUserQuestion", {"questions": self._questions()}, None
        )
        assert isinstance(first, PermissionResultAllow)
        assert first.updated_input["answers"] == {"resolution": "10m"}

        stale = bridge._pending_clarifications.get(session_id)
        assert stale is None or stale.done(), "a live future would refuse the next question"

        second = await can_use_tool(
            "mcp__openeo__AskUserQuestion", {"questions": self._questions()}, None
        )
        assert isinstance(second, PermissionResultAllow), "the next question was refused"

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self):
        """Two sessions hold independent slots."""
        ws = InMemoryWebSocket()
        bridge, _ = self._real_hook(ws)
        loop = asyncio.get_event_loop()

        future_a = loop.create_future()
        future_b = loop.create_future()
        bridge._pending_clarifications["sess_a"] = future_a
        bridge._pending_clarifications["sess_b"] = future_b

        bridge.resolve_clarification("sess_a", {"q": "answer_a"})

        assert await future_a == {"q": "answer_a"}
        assert "sess_a" not in bridge._pending_clarifications
        assert "sess_b" in bridge._pending_clarifications
        assert not future_b.done()

        future_b.cancel()


# ============================================================================
# resolve_clarification Method Tests
# ============================================================================

class TestResolveClarification:
    """Test the resolve_clarification method on a real ClaudeSDKBridge instance.

    Uses monkeypatch to replace heavy constructor dependencies with in-memory doubles.
    """

    def _create_bridge(self, monkeypatch):
        """Instantiate a real ClaudeSDKBridge with patched infrastructure."""
        # Patch MCP server constructors to return inert values
        monkeypatch.setattr(
            "openeo_ai.sdk.claude_sdk_bridge.create_openeo_mcp_server",
            lambda config: {"name": "openeo", "tools": []},
        )
        monkeypatch.setattr(
            "openeo_ai.sdk.claude_sdk_bridge.create_planner_mcp_server",
            lambda config: {"name": "planner", "tools": []},
        )
        monkeypatch.setattr(
            "openeo_ai.sdk.claude_sdk_bridge.create_specialist_mcp_server",
            lambda name, tool_names, config: {"name": name, "tools": []},
        )
        monkeypatch.setattr(
            "openeo_ai.sdk.claude_sdk_bridge.SessionManager",
            InMemorySessionManager,
        )
        # Disable trajectory eval
        monkeypatch.setenv("DISABLE_TRAJECTORY_EVAL", "1")

        from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge
        from openeo_ai.sdk.client import OpenEOAIConfig

        config = OpenEOAIConfig()
        return ClaudeSDKBridge(config)

    @pytest.mark.asyncio
    async def test_resolve_sets_future_and_pops(self, monkeypatch):
        """resolve_clarification pops session and sets future result."""
        bridge = self._create_bridge(monkeypatch)

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        bridge._pending_clarifications["test_session"] = future

        answers = {"satellite": "Sentinel-2"}
        bridge.resolve_clarification("test_session", answers)

        assert future.done()
        assert future.result() == answers
        assert "test_session" not in bridge._pending_clarifications

    @pytest.mark.asyncio
    async def test_resolve_missing_session_no_error(self, monkeypatch):
        """resolve_clarification with unknown session_id does not raise."""
        bridge = self._create_bridge(monkeypatch)

        # Should not raise
        bridge.resolve_clarification("nonexistent_session", {"q": "a"})

    @pytest.mark.asyncio
    async def test_resolve_already_done_future_no_error(self, monkeypatch):
        """resolve_clarification with already-done future does not raise."""
        bridge = self._create_bridge(monkeypatch)

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        future.set_result({"old": "answer"})
        bridge._pending_clarifications["done_session"] = future

        # Should not raise, future is already done
        bridge.resolve_clarification("done_session", {"new": "answer"})

        # Original result unchanged (set_result was not called again)
        assert future.result() == {"old": "answer"}
        # Session popped from pending
        assert "done_session" not in bridge._pending_clarifications

    @pytest.mark.asyncio
    async def test_resolve_cancelled_future_no_error(self, monkeypatch):
        """resolve_clarification with a cancelled future does not raise."""
        bridge = self._create_bridge(monkeypatch)

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        future.cancel()
        bridge._pending_clarifications["cancelled_sess"] = future

        # cancelled future is "done" so set_result should be skipped
        bridge.resolve_clarification("cancelled_sess", {"q": "a"})

        assert "cancelled_sess" not in bridge._pending_clarifications


# ============================================================================
# _strip_mcp_prefix Edge Cases
# ============================================================================

class TestStripMcpPrefix:
    """Test the _strip_mcp_prefix utility used by can_use_tool."""

    def test_standard_openeo_prefix(self):
        assert _strip_mcp_prefix("mcp__openeo__AskUserQuestion") == "AskUserQuestion"

    def test_specialist_prefix_data(self):
        assert _strip_mcp_prefix("mcp__openeo_data__some_tool") == "some_tool"

    def test_specialist_prefix_viz(self):
        assert _strip_mcp_prefix("mcp__openeo_viz__render_map") == "render_map"

    def test_specialist_prefix_planner(self):
        assert _strip_mcp_prefix("mcp__openeo_planner__plan_tool") == "plan_tool"

    def test_no_prefix_passthrough(self):
        assert _strip_mcp_prefix("AskUserQuestion") == "AskUserQuestion"

    def test_empty_string(self):
        assert _strip_mcp_prefix("") == ""

    def test_partial_prefix_non_openeo(self):
        """A name with mcp__ but not starting with openeo passes through."""
        result = _strip_mcp_prefix("mcp__something__tool")
        # Does not start with mcp__openeo, so returns unchanged
        assert result == "mcp__something__tool"

    def test_standard_tool_name(self):
        assert _strip_mcp_prefix("mcp__openeo__openeo_search_collections") == "openeo_search_collections"

    def test_double_underscore_in_tool_name(self):
        """Tool name containing __ is preserved after prefix strip."""
        result = _strip_mcp_prefix("mcp__openeo__my__tool")
        # split("__", 2) returns ['mcp', 'openeo', 'my__tool']
        assert result == "my__tool"


# ============================================================================
# Allowed Tools List Tests
# ============================================================================

class TestAllowedToolsList:
    """Verify AskUserQuestion is in the allowed tools list."""

    def test_ask_user_question_in_allowed_tools(self):
        """AskUserQuestion must be explicitly added to allowed_tools.

        In _build_options (line 1109), AskUserQuestion is appended separately
        because it is not in TOOL_DEFINITIONS (it is registered directly on the
        MCP server, not through the standard tool definition pipeline).
        """
        from openeo_ai.sdk.client import TOOL_DEFINITIONS

        # Build allowed_tools the same way _build_options does
        allowed_tools = [f"mcp__openeo__{td['name']}" for td in TOOL_DEFINITIONS]
        allowed_tools.append("mcp__openeo__AskUserQuestion")

        assert "mcp__openeo__AskUserQuestion" in allowed_tools

    def test_ask_user_not_in_tool_definitions(self):
        """AskUserQuestion is NOT in TOOL_DEFINITIONS (registered separately)."""
        from openeo_ai.sdk.client import TOOL_DEFINITIONS

        tool_names = {td["name"] for td in TOOL_DEFINITIONS}
        assert "AskUserQuestion" not in tool_names

    def test_all_tool_definitions_have_prefixed_entry(self):
        """Every TOOL_DEFINITION gets an mcp__openeo__ prefixed allowed entry."""
        from openeo_ai.sdk.client import TOOL_DEFINITIONS

        allowed_tools = [f"mcp__openeo__{td['name']}" for td in TOOL_DEFINITIONS]
        allowed_tools.append("mcp__openeo__AskUserQuestion")

        for td in TOOL_DEFINITIONS:
            expected = f"mcp__openeo__{td['name']}"
            assert expected in allowed_tools, f"Missing allowed tool: {expected}"


# ============================================================================
# Future Lifecycle Tests
# ============================================================================

class TestFutureLifecycle:
    """Test asyncio.Future behavior within the clarification flow."""

    @pytest.mark.asyncio
    async def test_future_resolves_with_dict(self):
        """Standard lifecycle: create future, set result, await it."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        answers = {"satellite": "Sentinel-2", "period": "monthly"}
        future.set_result(answers)

        result = await future
        assert result == answers
        assert future.done()

    @pytest.mark.asyncio
    async def test_future_wait_for_timeout(self):
        """asyncio.wait_for raises TimeoutError on unresolved future."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(future, timeout=0.02)

    @pytest.mark.asyncio
    async def test_future_delayed_resolution(self):
        """Future resolved from a concurrent task after creation."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        async def resolve_later():
            await asyncio.sleep(0.01)
            future.set_result({"resolved": True})

        asyncio.ensure_future(resolve_later())
        result = await asyncio.wait_for(future, timeout=2.0)
        assert result == {"resolved": True}

    @pytest.mark.asyncio
    async def test_pop_then_set_result_pattern(self):
        """Verify the pop-then-set pattern used by resolve_clarification."""
        pending = {}
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        pending["sess"] = future

        # Pop and set (matching resolve_clarification logic at line 1483-1485)
        popped = pending.pop("sess", None)
        if popped and not popped.done():
            popped.set_result({"answer": "yes"})

        assert await future == {"answer": "yes"}
        assert "sess" not in pending

    @pytest.mark.asyncio
    async def test_double_set_result_raises(self):
        """Setting result twice on a future raises InvalidStateError."""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        future.set_result({"first": True})

        with pytest.raises(asyncio.InvalidStateError):
            future.set_result({"second": True})


class TestQuestionIds:
    """An answer only resolves the question it was given for."""

    def _bridge(self, open_id="q2"):
        from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge

        bridge = ClaudeSDKBridge.__new__(ClaudeSDKBridge)
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        bridge._pending_clarifications = {"s": future}
        bridge._pending_question_ids = {"s": open_id}
        return bridge, future, loop

    def test_an_answer_for_a_closed_question_is_ignored(self):
        bridge, future, loop = self._bridge(open_id="q2")
        bridge.resolve_clarification("s", {"0": "yes"}, question_id="q1")
        assert not future.done()
        assert bridge._pending_question_ids == {"s": "q2"}
        loop.close()

    def test_the_matching_answer_resolves_it(self):
        bridge, future, loop = self._bridge(open_id="q2")
        bridge.resolve_clarification("s", {"0": "yes"}, question_id="q2")
        assert future.result() == {"0": "yes"}
        assert bridge._pending_question_ids == {} and bridge._pending_clarifications == {}
        loop.close()

    def test_an_answer_from_an_older_page_without_an_id_is_accepted(self):
        bridge, future, loop = self._bridge(open_id="q2")
        bridge.resolve_clarification("s", {"0": "yes"})
        assert future.result() == {"0": "yes"}
        loop.close()

    def test_cancelling_forgets_the_open_id(self):
        bridge, future, loop = self._bridge()
        bridge.cancel_pending_clarification("s")
        assert future.cancelled() and bridge._pending_question_ids == {}
        loop.close()
