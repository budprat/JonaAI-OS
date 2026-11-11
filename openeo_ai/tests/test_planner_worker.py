# ABOUTME: Tests for the planner/worker architecture in claude_sdk_bridge.py.
# Covers plan JSON extraction, model routing, graph validation, and vision tool.

"""
Tests for P1+P2 features:
- _extract_plan_json edge cases
- _select_model routing (planner/worker vs direct)
- _validate_graph_schema (graph validation)
- analyze_result_image tool definition registration
"""

import json
import pytest

from openeo_ai.sdk.claude_sdk_bridge import (
    ClaudeSDKBridge,
    _qa_last,
    _select_model,
    MODEL_DEFAULT,
    MODEL_EFFORT,
    MODEL_HAIKU,
    _apply_reasoning,
    MODEL_SONNET,
)
from openeo_ai.tools.openeo_tools import OpenEOTools


# ============================================================================
# _extract_plan_json tests
# ============================================================================

# Defined only where Earth-Agent is installed (tools/earth_agent_tools.py), which this host and CI
# are not; test_earth_agent_sdk_integration covers them when it is.
_EARTH_AGENT_TOOLS = {"earth_agent_analyze", "get_analysis_strategies"}


class TestExtractPlanJson:
    """Test plan JSON extraction from planner response text."""

    def test_code_fenced_json(self):
        text = 'Here is my plan:\n```json\n{"steps": [{"tool": "test", "args": {}, "purpose": "test"}], "model_recommendation": "haiku", "estimated_turns": 1}\n```'
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        assert len(result["steps"]) == 1
        assert result["steps"][0]["tool"] == "test"

    def test_code_fence_without_json_lang(self):
        text = '```\n{"steps": [{"tool": "a", "args": {}, "purpose": "b"}]}\n```'
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        assert len(result["steps"]) == 1

    def test_raw_json_inline(self):
        text = 'Some text {"steps": [{"tool": "x", "args": {}, "purpose": "y"}], "summary": "test"} more text'
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        assert result["steps"][0]["tool"] == "x"

    def test_json_with_steps_key_not_first(self):
        text = 'Plan: {"summary": "NDVI analysis", "steps": [{"tool": "load", "args": {}, "purpose": "load data"}], "estimated_turns": 3}'
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        assert result["summary"] == "NDVI analysis"

    def test_empty_string(self):
        assert ClaudeSDKBridge._extract_plan_json("") is None

    def test_no_json(self):
        assert ClaudeSDKBridge._extract_plan_json("Just some text with no JSON") is None

    def test_json_without_steps(self):
        text = '{"key": "value", "no_steps_here": true}'
        assert ClaudeSDKBridge._extract_plan_json(text) is None

    def test_invalid_json(self):
        text = '```json\n{"steps": [broken json}\n```'
        assert ClaudeSDKBridge._extract_plan_json(text) is None

    def test_nested_braces_in_args(self):
        text = '{"steps": [{"tool": "generate", "args": {"extent": {"west": 13.0, "south": 52.0, "east": 13.5, "north": 52.5}}, "purpose": "build graph"}], "estimated_turns": 5}'
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        assert result["steps"][0]["args"]["extent"]["west"] == 13.0

    def test_multiline_code_fence(self):
        text = """I'll create a plan for this analysis.

```json
{
  "steps": [
    {"tool": "openeo_resolve_location", "args": {"location": "Berlin"}, "purpose": "Get bounding box"},
    {"tool": "openeo_parse_temporal", "args": {"dates": "summer 2023"}, "purpose": "Parse dates"},
    {"tool": "openeo_generate_graph", "args": {"description": "NDVI"}, "purpose": "Build graph"}
  ],
  "model_recommendation": "haiku",
  "estimated_turns": 8,
  "summary": "NDVI analysis for Berlin summer 2023"
}
```

This plan will efficiently calculate NDVI."""
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        assert len(result["steps"]) == 3
        assert result["summary"] == "NDVI analysis for Berlin summer 2023"


# ============================================================================
# _select_model tests
# ============================================================================

class TestSelectModel:
    """Test dynamic model selection for query routing."""

    def test_simple_list_query(self):
        assert _select_model("list collections", "default") == MODEL_DEFAULT

    def test_simple_status_query(self):
        assert _select_model("status", "default") == MODEL_DEFAULT

    def test_simple_hello(self):
        assert _select_model("hello", "default") == MODEL_DEFAULT

    def test_moderate_ndvi_query(self):
        # Single-index analysis → moderate → Haiku (cost-efficient)
        assert _select_model("Calculate NDVI for Berlin", "default") == MODEL_DEFAULT

    # 2026-04-11: _select_model now always returns Haiku regardless of
    # tier. Haiku-4.5 handles complex multi-agent queries well enough in
    # practice, cost is ~10x lower, and latency is ~3x faster. The tier
    # classifier still runs so Langfuse can tell us WHAT was classified
    # as complex — if a category proves insufficient we revisit.
    def test_complex_change_detection_routes_to_haiku(self):
        # Previously Sonnet — now unified on Haiku for cost/latency.
        assert _select_model("Run change detection between 2023 and 2024", "default") == MODEL_DEFAULT

    def test_complex_temporal_analysis_routes_to_haiku(self):
        assert _select_model("Show temporal trends for vegetation", "default") == MODEL_DEFAULT

    def test_ambiguous_defaults_to_haiku(self):
        # Ambiguous → moderate → Haiku (cost-efficient default)
        assert _select_model("What can you do for this area?", "default") == MODEL_DEFAULT

    def test_short_simple_patterns(self):
        assert _select_model("help", "default") == MODEL_DEFAULT
        assert _select_model("hi", "default") == MODEL_DEFAULT

    def test_complex_classify_routes_to_haiku(self):
        assert _select_model("Classify land cover types", "default") == MODEL_DEFAULT

    def test_complex_fire_routes_to_haiku(self):
        assert _select_model("Detect fire scars in the region", "default") == MODEL_DEFAULT


class TestComplexModelEscalation:
    """JONA_COMPLEX_MODEL plumbing (C06, ratified 2026-07-10): opt-in env
    escalation for complex-tier queries only. The shipped default leaves the
    env unset, so the flat-Haiku tests above must keep passing unchanged."""

    COMPLEX_PROMPT = "Run change detection between 2023 and 2024"

    def test_prompt_is_complex_tier(self):
        from openeo_ai.sdk.claude_sdk_bridge import _classify_query_complexity

        assert _classify_query_complexity(self.COMPLEX_PROMPT) == "complex"

    def test_env_unset_complex_stays_haiku(self, monkeypatch):
        monkeypatch.delenv("JONA_COMPLEX_MODEL", raising=False)
        assert _select_model(self.COMPLEX_PROMPT, "default") == MODEL_DEFAULT

    def test_env_set_complex_escalates(self, monkeypatch):
        monkeypatch.setenv("JONA_COMPLEX_MODEL", "claude-sonnet-5")
        assert _select_model(self.COMPLEX_PROMPT, "default") == "claude-sonnet-5"

    def test_env_set_simple_and_moderate_stay_haiku(self, monkeypatch):
        monkeypatch.setenv("JONA_COMPLEX_MODEL", "claude-sonnet-5")
        assert _select_model("hello", "default") == MODEL_DEFAULT
        assert _select_model("Calculate NDVI for Berlin", "default") == MODEL_DEFAULT

    def test_env_whitespace_only_is_ignored(self, monkeypatch):
        monkeypatch.setenv("JONA_COMPLEX_MODEL", "   ")
        assert _select_model(self.COMPLEX_PROMPT, "default") == MODEL_DEFAULT


class TestWorkerModelFromRecommendation:
    """The planner's model_recommendation is LLM output — it must pass an
    explicit opt-in (JONA_HONOR_PLAN_MODEL) plus a whitelist before it can
    reach an API call as a model ID."""

    @staticmethod
    def _fn():
        from openeo_ai.sdk.claude_sdk_bridge import _worker_model_from_recommendation

        return _worker_model_from_recommendation

    def test_ignored_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("JONA_HONOR_PLAN_MODEL", raising=False)
        assert self._fn()("sonnet") == MODEL_DEFAULT

    def test_honored_sonnet_when_enabled(self, monkeypatch):
        monkeypatch.setenv("JONA_HONOR_PLAN_MODEL", "true")
        assert self._fn()("sonnet") == MODEL_SONNET

    def test_honored_haiku_when_enabled(self, monkeypatch):
        monkeypatch.setenv("JONA_HONOR_PLAN_MODEL", "1")
        assert self._fn()("haiku") == MODEL_HAIKU

    @pytest.mark.parametrize("rec", ["opus", "gpt-5", "claude-sonnet-5; rm -rf /", "", "  "])
    def test_non_whitelisted_recommendations_fall_back(self, rec, monkeypatch):
        monkeypatch.setenv("JONA_HONOR_PLAN_MODEL", "true")
        assert self._fn()(rec) == MODEL_DEFAULT

    def test_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setenv("JONA_HONOR_PLAN_MODEL", "true")
        assert self._fn()(" Sonnet ") == MODEL_SONNET

    def test_sonnet_constant_is_current_model_id(self):
        # A stale ID would 404 the moment escalation is enabled.
        assert MODEL_SONNET == "claude-sonnet-5"


# ============================================================================
# _validate_graph_schema tests
# ============================================================================



class TestApplyReasoning:
    """Opus 5.5 rejects thinking budgets: adaptive thinking plus effort only."""

    def _opts(self, model):
        from claude_agent_sdk import ClaudeAgentOptions

        return ClaudeAgentOptions(model=model)

    def test_defaults_to_opus_at_max_effort(self):
        assert MODEL_DEFAULT == "claude-opus-5-5"
        assert MODEL_EFFORT == "max"

    def test_default_model_gets_adaptive_thinking_and_effort(self):
        opts = self._opts(MODEL_DEFAULT)
        _apply_reasoning(opts)
        assert opts.thinking == {"type": "adaptive"}
        assert opts.effort == MODEL_EFFORT
        assert opts.max_thinking_tokens is None

    def test_haiku_gets_neither(self):
        opts = self._opts(MODEL_HAIKU)
        _apply_reasoning(opts)
        assert opts.thinking is None
        assert opts.effort is None

class TestValidateGraphSchema:
    """Test process graph structural validation."""

    def test_valid_simple_graph(self):
        graph = {
            "load": {"process_id": "load_collection", "arguments": {"id": "s2"}},
            "save": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "load"}, "format": "GTiff"},
                "result": True,
            },
        }
        errors = OpenEOTools._validate_graph_schema(graph)
        assert errors == []

    def test_empty_graph(self):
        errors = OpenEOTools._validate_graph_schema({})
        assert len(errors) == 1
        assert "non-empty" in errors[0]

    def test_not_a_dict(self):
        errors = OpenEOTools._validate_graph_schema([1, 2, 3])
        assert len(errors) == 1

    def test_missing_process_id(self):
        graph = {"node1": {"arguments": {"x": 1}, "result": True}}
        errors = OpenEOTools._validate_graph_schema(graph)
        assert any("process_id" in e for e in errors)

    def test_missing_arguments(self):
        graph = {"node1": {"process_id": "add", "result": True}}
        errors = OpenEOTools._validate_graph_schema(graph)
        assert any("arguments" in e for e in errors)

    def test_missing_result_node(self):
        graph = {"node1": {"process_id": "add", "arguments": {"x": 1}}}
        errors = OpenEOTools._validate_graph_schema(graph)
        assert any("result" in e.lower() for e in errors)

    def test_invalid_from_node_reference(self):
        graph = {
            "load": {"process_id": "load", "arguments": {"id": "s2"}},
            "save": {
                "process_id": "save",
                "arguments": {"data": {"from_node": "nonexistent"}},
                "result": True,
            },
        }
        errors = OpenEOTools._validate_graph_schema(graph)
        assert any("nonexistent" in e for e in errors)

    def test_valid_from_node_reference(self):
        graph = {
            "load": {"process_id": "load", "arguments": {"id": "s2"}},
            "save": {
                "process_id": "save",
                "arguments": {"data": {"from_node": "load"}},
                "result": True,
            },
        }
        errors = OpenEOTools._validate_graph_schema(graph, check_process_names=False)
        assert errors == []

    def test_node_not_a_dict(self):
        graph = {"load": "not_a_dict", "save": {"process_id": "save", "arguments": {}, "result": True}}
        errors = OpenEOTools._validate_graph_schema(graph, check_process_names=False)
        assert any("must be a dict" in e for e in errors)

    def test_sub_graph_validation(self):
        """Test that sub-process graphs in reducers are validated."""
        graph = {
            "load": {"process_id": "load", "arguments": {"id": "s2"}},
            "reduce": {
                "process_id": "reduce_dimension",
                "arguments": {
                    "data": {"from_node": "load"},
                    "reducer": {
                        "process_graph": {
                            "mean": {"arguments": {"data": {"from_parameter": "data"}}, "result": True}
                            # Missing process_id in sub-node
                        }
                    },
                },
                "result": True,
            },
        }
        errors = OpenEOTools._validate_graph_schema(graph, check_process_names=False)
        assert any("process_id" in e and "mean" in e for e in errors)

    def test_ndvi_graph_from_conftest(self, ndvi_process_graph):
        """Verify the NDVI graph fixture passes validation."""
        errors = OpenEOTools._validate_graph_schema(ndvi_process_graph)
        assert errors == []

    def test_invalid_graph_from_conftest(self, invalid_process_graph):
        """Verify the invalid graph fixture fails validation."""
        errors = OpenEOTools._validate_graph_schema(invalid_process_graph)
        assert len(errors) > 0
        # Should catch: unknown from_node ref + missing result node
        assert any("unknown_node" in e for e in errors)
        assert any("result" in e.lower() for e in errors)

    def test_multiple_errors(self):
        graph = {
            "bad1": {"process_id": "x", "arguments": {"data": {"from_node": "missing1"}}},
            "bad2": {"process_id": "y", "arguments": {"data": {"from_node": "missing2"}}},
        }
        errors = OpenEOTools._validate_graph_schema(graph, check_process_names=False)
        # Should have: 2 unknown refs + 1 missing result = 3 errors
        assert len(errors) == 3


# ============================================================================
# Vision tool registration tests
# ============================================================================

class TestVisionToolRegistration:
    """Test that analyze_result_image is properly registered."""

    def test_handler_registered(self):
        from openeo_ai.tools.result_analysis_tools import create_result_analysis_tools
        tools = create_result_analysis_tools()
        assert "analyze_result_image" in tools

    def test_tool_definition_exists(self):
        from openeo_ai.tools.result_analysis_tools import RESULT_ANALYSIS_TOOL_DEFINITIONS
        names = [td["name"] for td in RESULT_ANALYSIS_TOOL_DEFINITIONS]
        assert "analyze_result_image" in names

    def test_tool_in_global_definitions(self):
        from openeo_ai.sdk.client import TOOL_DEFINITIONS
        names = [td["name"] for td in TOOL_DEFINITIONS]
        assert "analyze_result_image" in names

    def test_tool_schema_has_required_fields(self):
        from openeo_ai.tools.result_analysis_tools import RESULT_ANALYSIS_TOOL_DEFINITIONS
        tool_def = next(td for td in RESULT_ANALYSIS_TOOL_DEFINITIONS if td["name"] == "analyze_result_image")
        schema = tool_def["input_schema"]
        assert "file_path" in schema["properties"]
        assert "band" in schema["properties"]
        assert "question" in schema["properties"]
        assert "colormap" in schema["properties"]
        assert schema["required"] == ["file_path"]

    @pytest.mark.asyncio
    async def test_handler_returns_error_for_missing_file(self):
        from openeo_ai.tools.result_analysis_tools import create_result_analysis_tools
        tools = create_result_analysis_tools()
        result = await tools["analyze_result_image"]({"file_path": "/nonexistent/file.tif"})
        assert "content" in result
        text_items = [c for c in result["content"] if c.get("type") == "text"]
        # Path validator blocks paths outside allowed directories
        assert any("error" in t.get("text", "").lower() for t in text_items)

    @pytest.mark.asyncio
    async def test_handler_rejects_non_geotiff(self, tmp_path):
        from openeo_ai.tools.result_analysis_tools import create_result_analysis_tools
        # Create a file with blocked extension to trigger path validator
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("not a geotiff")
        tools = create_result_analysis_tools()
        result = await tools["analyze_result_image"]({"file_path": str(txt_file)})
        assert "content" in result
        text_items = [c for c in result["content"] if c.get("type") == "text"]
        # Path validator blocks disallowed file types or paths outside allowed dirs
        assert any("error" in t.get("text", "").lower() for t in text_items)


# ============================================================================
# Planner tool subset tests
# ============================================================================

class TestPlannerToolSubset:
    """Test that planner tools are correctly configured."""

    def test_planner_tools_exist_in_definitions(self):
        from openeo_ai.sdk.client import PLANNER_TOOL_NAMES, TOOL_DEFINITIONS
        td_names = {td["name"] for td in TOOL_DEFINITIONS}
        missing = PLANNER_TOOL_NAMES - td_names - _EARTH_AGENT_TOOLS
        assert missing == set(), f"Missing planner tools: {missing}"

    def test_planner_tools_are_read_only(self):
        """Planner should only have discovery/validation tools, not execution tools."""
        from openeo_ai.sdk.client import PLANNER_TOOL_NAMES
        # These execution tools should NOT be in planner
        execution_tools = {
            "openeo_generate_graph", "openeo_create_job", "openeo_start_job",
            "openeo_get_results", "viz_render_map", "result_inspect",
            "analyze_result_image",
        }
        overlap = PLANNER_TOOL_NAMES & execution_tools
        assert overlap == set(), f"Planner has execution tools: {overlap}"

    def test_planner_mcp_server_creates(self):
        from openeo_ai.sdk.inprocess_tools import create_planner_mcp_server
        server = create_planner_mcp_server()
        assert server is not None

    def test_planner_system_prompt_has_json_format(self):
        from openeo_ai.sdk.client import PLANNER_SYSTEM_PROMPT
        assert '"steps"' in PLANNER_SYSTEM_PROMPT
        assert '"tool"' in PLANNER_SYSTEM_PROMPT
        assert "JSON" in PLANNER_SYSTEM_PROMPT


# ============================================================================
# Citation / source extraction tests
# ============================================================================

class TestExtractSources:
    """Test citation source extraction from tool results."""

    def test_direct_sources_key(self):
        from openeo_ai.sdk.claude_sdk_bridge import _extract_sources
        result = {
            "id": "sentinel-2-l2a",
            "sources": [
                "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a",
                "License: proprietary",
            ],
        }
        sources = _extract_sources("openeo_get_collection_info", result)
        assert len(sources) >= 2
        assert any("sentinel-2-l2a" in s for s in sources)
        assert any("License" in s for s in sources)

    def test_collection_id_generates_stac_url(self):
        from openeo_ai.sdk.claude_sdk_bridge import _extract_sources
        result = {"id": "landsat-c2-l2", "title": "Landsat"}
        sources = _extract_sources("openeo_get_collection_info", result)
        assert any("earth-search" in s and "landsat-c2-l2" in s for s in sources)

    def test_no_sources_for_unrelated_tools(self):
        from openeo_ai.sdk.claude_sdk_bridge import _extract_sources
        result = {"status": "running", "job_id": "j-123"}
        sources = _extract_sources("openeo_get_job_status", result)
        assert sources == []

    def test_string_result_returns_empty(self):
        from openeo_ai.sdk.claude_sdk_bridge import _extract_sources
        sources = _extract_sources("some_tool", "plain text result")
        assert sources == []

    def test_deduplication(self):
        from openeo_ai.sdk.claude_sdk_bridge import _extract_sources
        result = {
            "id": "sentinel-2-l2a",
            "sources": [
                "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a",
            ],
        }
        sources = _extract_sources("openeo_get_collection_info", result)
        # Should have the explicit source + auto-generated STAC URL, deduplicated
        stac_urls = [s for s in sources if "sentinel-2-l2a" in s]
        assert len(stac_urls) == 1  # Deduplicated

    def test_mcp_content_format_sources(self):
        from openeo_ai.sdk.claude_sdk_bridge import _extract_sources
        result = {
            "content": [
                {
                    "type": "text",
                    "text": '{"id": "test-col", "sources": ["https://example.com/data"]}',
                }
            ]
        }
        sources = _extract_sources("openeo_get_collection_info", result)
        assert any("example.com" in s for s in sources)

    def test_recommend_collection_gets_url(self):
        from openeo_ai.sdk.claude_sdk_bridge import _extract_sources
        result = {"id": "sentinel-2-l2a", "recommendation": "best for NDVI"}
        sources = _extract_sources("recommend_collection", result)
        assert any("sentinel-2-l2a" in s for s in sources)


# ============================================================================
# Phase 3: Specialist tool subset tests
# ============================================================================

class TestSpecialistToolSubsets:
    """Test that specialist tool subsets contain valid, existing tools."""

    def test_data_tools_exist(self):
        from openeo_ai.sdk.client import DATA_AGENT_TOOL_NAMES, TOOL_DEFINITIONS
        td_names = {td["name"] for td in TOOL_DEFINITIONS}
        missing = DATA_AGENT_TOOL_NAMES - td_names
        assert missing == set(), f"Missing data agent tools: {missing}"

    def test_viz_tools_exist(self):
        from openeo_ai.sdk.client import VIZ_AGENT_TOOL_NAMES, TOOL_DEFINITIONS
        td_names = {td["name"] for td in TOOL_DEFINITIONS}
        missing = VIZ_AGENT_TOOL_NAMES - td_names
        assert missing == set(), f"Missing viz agent tools: {missing}"

    def test_ml_tools_exist(self):
        from openeo_ai.sdk.client import ML_AGENT_TOOL_NAMES, TOOL_DEFINITIONS
        td_names = {td["name"] for td in TOOL_DEFINITIONS}
        missing = ML_AGENT_TOOL_NAMES - td_names - _EARTH_AGENT_TOOLS
        assert missing == set(), f"Missing ML agent tools: {missing}"

    def test_qa_tools_exist(self):
        from openeo_ai.sdk.client import QA_AGENT_TOOL_NAMES, TOOL_DEFINITIONS
        td_names = {td["name"] for td in TOOL_DEFINITIONS}
        missing = QA_AGENT_TOOL_NAMES - td_names
        assert missing == set(), f"Missing QA agent tools: {missing}"

    def test_no_execution_tools_in_orchestrator(self):
        """Orchestrator (same as planner) should only have discovery tools."""
        from openeo_ai.sdk.client import ORCHESTRATOR_TOOL_NAMES
        execution_tools = {
            "openeo_generate_graph", "openeo_create_job", "openeo_start_job",
            "openeo_get_results", "result_inspect", "analyze_result_image",
        }
        overlap = ORCHESTRATOR_TOOL_NAMES & execution_tools
        assert overlap == set(), f"Orchestrator has execution tools: {overlap}"


# ============================================================================
# Phase 3: Routing plan extraction tests
# ============================================================================

class TestRoutingPlanExtraction:
    """Test routing and specialist assignment extraction from plans."""

    def test_single_specialist_routing(self):
        text = '```json\n{"steps": [{"tool": "openeo_generate_graph", "args": {}, "purpose": "build", "specialist": "data"}], "routing": ["data"], "model_recommendation": "haiku", "estimated_turns": 3, "summary": "test"}\n```'
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        assert result["routing"] == ["data"]

    def test_multi_specialist_routing(self):
        text = '{"steps": [{"tool": "a", "args": {}, "purpose": "x", "specialist": "data"}, {"tool": "b", "args": {}, "purpose": "y", "specialist": "viz"}], "routing": ["data", "viz"], "summary": "test"}'
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        assert result["routing"] == ["data", "viz"]

    def test_default_routing_when_missing(self):
        text = '{"steps": [{"tool": "a", "args": {}, "purpose": "x"}], "summary": "test"}'
        result = ClaudeSDKBridge._extract_plan_json(text)
        assert result is not None
        # No routing key — caller should default to ["data"]
        assert result.get("routing") is None

    def test_specialist_step_filtering(self):
        """Steps should be correctly filterable by specialist field."""
        plan = {
            "steps": [
                {"tool": "openeo_generate_graph", "args": {}, "purpose": "build", "specialist": "data"},
                {"tool": "viz_show_map", "args": {}, "purpose": "render", "specialist": "viz"},
                {"tool": "openeo_create_job", "args": {}, "purpose": "submit", "specialist": "data"},
            ],
            "routing": ["data", "viz"],
        }
        data_steps = [s for s in plan["steps"] if s.get("specialist") == "data"]
        viz_steps = [s for s in plan["steps"] if s.get("specialist") == "viz"]
        assert len(data_steps) == 2
        assert len(viz_steps) == 1
        assert data_steps[0]["tool"] == "openeo_generate_graph"
        assert viz_steps[0]["tool"] == "viz_show_map"


# ============================================================================
# Phase 3: _strip_mcp_prefix with specialist prefixes
# ============================================================================

class TestStripMcpPrefix:
    """Test MCP prefix stripping for base and specialist server names."""

    def test_strip_base_prefix(self):
        from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix
        assert _strip_mcp_prefix("mcp__openeo__viz_show_map") == "viz_show_map"

    def test_strip_specialist_prefix_data(self):
        from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix
        assert _strip_mcp_prefix("mcp__openeo_data__openeo_generate_graph") == "openeo_generate_graph"

    def test_strip_specialist_prefix_viz(self):
        from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix
        assert _strip_mcp_prefix("mcp__openeo_viz__viz_show_map") == "viz_show_map"

    def test_strip_specialist_prefix_ml(self):
        from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix
        assert _strip_mcp_prefix("mcp__openeo_ml__geoai_segment") == "geoai_segment"

    def test_strip_specialist_prefix_qa(self):
        from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix
        assert _strip_mcp_prefix("mcp__openeo_qa__analyze_result_image") == "analyze_result_image"

    def test_strip_planner_prefix(self):
        from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix
        assert _strip_mcp_prefix("mcp__openeo_planner__openeo_list_collections") == "openeo_list_collections"

    def test_no_prefix_passthrough(self):
        from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix
        assert _strip_mcp_prefix("some_tool") == "some_tool"


# ============================================================================
# Phase 3.1: Domain context injection tests
# ============================================================================

class TestDomainContextInjection:
    """Test domain classification and context injection into specialist prompts."""

    def test_classify_vegetation(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("Calculate NDVI for Berlin") == "vegetation"

    def test_classify_fire(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("Map burn scars from the wildfire") == "fire"

    def test_classify_water(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("Detect water bodies in the lake region") == "water"

    def test_classify_returns_none_for_ambiguous(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("hello") is None

    def test_get_domain_context_returns_string(self):
        from openeo_ai.sdk.domain_knowledge import get_domain_context
        ctx = get_domain_context("fire")
        assert isinstance(ctx, str)
        assert "dNBR" in ctx
        assert "inferno" in ctx

    def test_get_domain_context_unknown_returns_none(self):
        from openeo_ai.sdk.domain_knowledge import get_domain_context
        assert get_domain_context("nonexistent") is None

    def test_specialist_prompt_includes_domain(self):
        """Verify _build_specialist_prompt injects domain context."""
        prompt = ClaudeSDKBridge._build_specialist_prompt(
            specialist_name="data",
            steps=[{"tool": "openeo_generate_graph", "purpose": "Build NBR graph"}],
            original_prompt="Map burn scars from the wildfire near Athens",
            accumulated_context={},
            domain="fire",
        )
        assert "Domain expertise (fire)" in prompt
        assert "dNBR" in prompt
        assert "inferno" in prompt

    def test_specialist_prompt_without_domain(self):
        """Verify _build_specialist_prompt works without domain."""
        prompt = ClaudeSDKBridge._build_specialist_prompt(
            specialist_name="data",
            steps=[{"tool": "openeo_generate_graph", "purpose": "Build graph"}],
            original_prompt="hello",
            accumulated_context={},
            domain=None,
        )
        assert "Domain expertise" not in prompt

    def test_planner_system_prompt_has_domain_field(self):
        from openeo_ai.sdk.client import PLANNER_SYSTEM_PROMPT
        assert '"domain"' in PLANNER_SYSTEM_PROMPT

    # --- New domain classification tests ---

    def test_classify_wetland(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("Map wetland areas in the marsh") == "wetland"

    def test_classify_algal_bloom(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("Detect algal bloom in the reservoir") == "algal_bloom"

    def test_classify_mining(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("Monitor quarry excavation progress") == "mining"

    def test_classify_wetland_swamp(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("Analyze swamp ecosystem health") == "wetland"

    def test_classify_algal_bloom_eutrophic(self):
        from openeo_ai.sdk.domain_knowledge import classify_domain
        assert classify_domain("Detect eutrophic conditions and algae in reservoir") == "algal_bloom"

    # --- Domain context output tests for new domains ---

    def test_get_wetland_context(self):
        from openeo_ai.sdk.domain_knowledge import get_domain_context
        ctx = get_domain_context("wetland")
        assert ctx is not None
        assert "NDWI" in ctx
        assert "WIW" in ctx
        assert "coolwarm" in ctx

    def test_get_algal_bloom_context(self):
        from openeo_ai.sdk.domain_knowledge import get_domain_context
        ctx = get_domain_context("algal_bloom")
        assert ctx is not None
        assert "NDCI" in ctx
        assert "viridis" in ctx
        assert "oligotrophic" in ctx

    def test_get_mining_context(self):
        from openeo_ai.sdk.domain_knowledge import get_domain_context
        ctx = get_domain_context("mining")
        assert ctx is not None
        assert "BSI" in ctx
        assert "plasma" in ctx
        assert "excavated_area" in ctx

    # --- Enhanced existing domain tests ---

    def test_fire_dNBR_has_regrowth_thresholds(self):
        from openeo_ai.sdk.domain_knowledge import DOMAIN_CONTEXTS
        fire_thresholds = DOMAIN_CONTEXTS["fire"]["thresholds"]["dNBR"]
        assert "enhanced_regrowth" in fire_thresholds
        assert "unburned" in fire_thresholds
        assert "high_severity" in fire_thresholds

    def test_snow_ndsi_has_four_classes(self):
        from openeo_ai.sdk.domain_knowledge import DOMAIN_CONTEXTS
        snow_thresholds = DOMAIN_CONTEXTS["snow"]["thresholds"]["NDSI"]
        assert len(snow_thresholds) == 4
        assert "high_confidence_snow" in snow_thresholds

    def test_vegetation_has_msavi(self):
        from openeo_ai.sdk.domain_knowledge import DOMAIN_CONTEXTS
        assert "MSAVI" in DOMAIN_CONTEXTS["vegetation"]["indices"]

    def test_drought_has_vci_tci(self):
        from openeo_ai.sdk.domain_knowledge import DOMAIN_CONTEXTS
        indices = DOMAIN_CONTEXTS["drought"]["indices"]
        assert "VCI" in indices
        assert "TCI" in indices

    def test_all_16_domains_have_context(self):
        """Verify all 16 domains produce valid context strings."""
        from openeo_ai.sdk.domain_knowledge import DOMAIN_CONTEXTS, get_domain_context
        assert len(DOMAIN_CONTEXTS) == 16
        for domain in DOMAIN_CONTEXTS:
            ctx = get_domain_context(domain)
            assert ctx is not None, f"Domain {domain} returned None context"
            assert f"Domain: {domain}" in ctx


class TestDiscoveryToolsSync:
    """Tests that discovery_tools.py stays in sync with domain_knowledge.py."""

    def test_use_case_mapping_has_16_domains(self):
        from openeo_ai.tools.discovery_tools import USE_CASE_MAPPING
        assert len(USE_CASE_MAPPING) == 16

    def test_use_case_keywords_has_16_domains(self):
        from openeo_ai.tools.discovery_tools import _USE_CASE_KEYWORDS
        assert len(_USE_CASE_KEYWORDS) == 16

    def test_use_case_mapping_has_new_domains(self):
        from openeo_ai.tools.discovery_tools import USE_CASE_MAPPING
        for domain in ("wetland", "algal_bloom", "mining"):
            assert domain in USE_CASE_MAPPING, f"Missing domain: {domain}"

    def test_use_case_keywords_has_new_domains(self):
        from openeo_ai.tools.discovery_tools import _USE_CASE_KEYWORDS
        for domain in ("wetland", "algal_bloom", "mining"):
            assert domain in _USE_CASE_KEYWORDS, f"Missing domain: {domain}"

    def test_wetland_not_in_water_keywords(self):
        """Wetland was moved to its own domain — should not be in water keywords."""
        from openeo_ai.tools.discovery_tools import _USE_CASE_KEYWORDS
        assert "wetland" not in _USE_CASE_KEYWORDS["water"]

    def test_use_case_domains_match_domain_knowledge(self):
        from openeo_ai.tools.discovery_tools import USE_CASE_MAPPING
        from openeo_ai.sdk.domain_knowledge import DOMAIN_CONTEXTS
        for domain in DOMAIN_CONTEXTS:
            assert domain in USE_CASE_MAPPING, f"Domain {domain} in domain_knowledge but not in USE_CASE_MAPPING"

    def test_match_use_cases_finds_wetland(self):
        from openeo_ai.tools.discovery_tools import _match_use_cases
        matched = _match_use_cases("Map wetland marsh areas")
        assert "wetland" in matched

    def test_match_use_cases_finds_mining(self):
        from openeo_ai.tools.discovery_tools import _match_use_cases
        matched = _match_use_cases("Monitor quarry excavation")
        assert "mining" in matched


class TestIndexSpecsSync:
    """Tests that INDEX_SPECS includes new normalized-difference indices."""

    def test_new_indices_in_specs(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        for idx in ("mndwi", "ndci", "wiw", "ui", "ndti"):
            assert idx in tools.INDEX_SPECS, f"Missing index: {idx}"

    def test_mndwi_bands(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        spec = tools.INDEX_SPECS["mndwi"]
        assert spec["band_x"] == "green"
        assert spec["band_y"] == "swir16"

    def test_ndci_bands(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        spec = tools.INDEX_SPECS["ndci"]
        assert spec["band_x"] == "rededge1"
        assert spec["band_y"] == "red"

    def test_wiw_bands(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        spec = tools.INDEX_SPECS["wiw"]
        assert spec["band_x"] == "blue"
        assert spec["band_y"] == "nir"

    def test_ui_bands(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        spec = tools.INDEX_SPECS["ui"]
        assert spec["band_x"] == "swir22"
        assert spec["band_y"] == "nir"

    def test_ndti_bands(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        spec = tools.INDEX_SPECS["ndti"]
        assert spec["band_x"] == "swir16"
        assert spec["band_y"] == "swir22"

    def test_detect_analysis_type_mndwi(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        assert tools._detect_analysis_type("calculate mndwi for the lake") == "mndwi"

    def test_detect_analysis_type_ndci(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        assert tools._detect_analysis_type("ndci chlorophyll mapping") == "ndci"

    def test_detect_analysis_type_wiw(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        assert tools._detect_analysis_type("compute wiw for the swamp") == "wiw"

    def test_mndwi_before_ndwi_substring(self):
        """MNDWI must be detected before NDWI to avoid substring collision."""
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools.__new__(OpenEOTools)
        assert tools._detect_analysis_type("calculate mndwi") == "mndwi"


class TestColormapMapSync:
    """Tests that INDEX_COLORMAP_MAP includes new index and domain colormaps."""

    def test_new_index_colormaps(self):
        from openeo_ai.sdk.viz_extractor import INDEX_COLORMAP_MAP
        expected = {
            "ndci": "viridis",
            "wiw": "coolwarm",
            "awei": "coolwarm",
            "bai": "inferno",
            "ui": "coolwarm",
            "vci": "ndvi",
            "tci": "coolwarm",
            "ndti": "plasma",
        }
        for idx, cmap in expected.items():
            assert idx in INDEX_COLORMAP_MAP, f"Missing colormap for: {idx}"
            assert INDEX_COLORMAP_MAP[idx] == cmap, f"Wrong colormap for {idx}: expected {cmap}, got {INDEX_COLORMAP_MAP[idx]}"

    def test_domain_keyword_colormaps(self):
        from openeo_ai.sdk.viz_extractor import INDEX_COLORMAP_MAP
        assert INDEX_COLORMAP_MAP["wetland"] == "coolwarm"
        assert INDEX_COLORMAP_MAP["mining"] == "plasma"
        assert INDEX_COLORMAP_MAP["algal bloom"] == "viridis"

    def test_detect_colormap_ndci(self):
        from openeo_ai.sdk.viz_extractor import _detect_colormap_from_title
        assert _detect_colormap_from_title("NDCI Analysis for Reservoir") == "viridis"

    def test_detect_colormap_mining(self):
        from openeo_ai.sdk.viz_extractor import _detect_colormap_from_title
        assert _detect_colormap_from_title("Mining Area BSI Change") == "plasma"

    def test_detect_colormap_wetland(self):
        from openeo_ai.sdk.viz_extractor import _detect_colormap_from_title
        assert _detect_colormap_from_title("Wetland NDWI Mapping") == "coolwarm"


# ============================================================================
# Phase 3.2: Multi-index graph builder tests
# ============================================================================

class TestMultiIndexGraph:
    """Test _build_multi_index_graph produces correct process graphs."""

    def _make_tools(self):
        return OpenEOTools.__new__(OpenEOTools)

    def _default_args(self, indices):
        return {
            "collection": "sentinel-2-l2a",
            "spatial_extent": {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5},
            "temporal_extent": ["2024-06-01", "2024-08-31"],
            "output_format": "GTiff",
            "indices": indices,
        }

    def test_multi_index_two_indices(self):
        """Graph with NDVI + NDWI produces merge_cubes node."""
        tools = self._make_tools()
        graph = tools._build_multi_index_graph(**self._default_args(["ndvi", "ndwi"]))
        assert "merge_01" in graph
        assert graph["merge_01"]["process_id"] == "merge_cubes"

    def test_multi_index_three_indices(self):
        """Graph with 3 indices chains merge_cubes correctly."""
        tools = self._make_tools()
        graph = tools._build_multi_index_graph(**self._default_args(["ndvi", "ndwi", "ndbi"]))
        assert "merge_01" in graph
        # Second merge chains from merge_01
        merge_keys = [k for k in graph if k.startswith("merge_")]
        assert len(merge_keys) == 2

    def test_multi_index_with_cloud_mask(self):
        """Cloud masking nodes present when cloud_mask=True."""
        tools = self._make_tools()
        args = self._default_args(["ndvi", "ndwi"])
        args["cloud_mask"] = True
        graph = tools._build_multi_index_graph(**args)
        assert "get_scl" in graph
        assert "cloud_mask" in graph
        assert "apply_mask" in graph

    def test_multi_index_single_load(self):
        """Only one load_collection node (all bands unioned)."""
        tools = self._make_tools()
        graph = tools._build_multi_index_graph(**self._default_args(["ndvi", "ndwi"]))
        load_nodes = [k for k, v in graph.items() if v.get("process_id") == "load_collection"]
        assert len(load_nodes) == 1

    def test_multi_index_has_save(self):
        """Graph ends with save_result node."""
        tools = self._make_tools()
        graph = tools._build_multi_index_graph(**self._default_args(["ndvi", "ndwi"]))
        assert "save" in graph
        assert graph["save"]["process_id"] == "save_result"
        assert graph["save"].get("result") is True

    def test_multi_index_labels_each_result(self):
        """Each index result gets a bands dimension labeled with the index name."""
        tools = self._make_tools()
        graph = tools._build_multi_index_graph(**self._default_args(["ndvi", "ndwi"]))
        assert "rename_ndvi" in graph
        assert "rename_ndwi" in graph
        assert graph["rename_ndvi"]["process_id"] == "add_dimension"
        assert graph["rename_ndvi"]["arguments"]["label"] == "ndvi"
        assert graph["rename_ndwi"]["arguments"]["label"] == "ndwi"


# ============================================================================
# Phase 3.2: Zonal stats graph builder tests
# ============================================================================

class TestZonalStatsGraph:
    """Test _build_zonal_stats_graph produces correct process graphs."""

    def _make_tools(self):
        return OpenEOTools.__new__(OpenEOTools)

    def _default_geojson(self):
        return {
            "type": "Polygon",
            "coordinates": [[[11.0, 46.0], [11.5, 46.0], [11.5, 46.5], [11.0, 46.5], [11.0, 46.0]]],
        }

    def test_zonal_basic_structure(self):
        """Graph has load_geojson + aggregate_spatial nodes."""
        tools = self._make_tools()
        graph = tools._build_zonal_stats_graph(
            "sentinel-2-l2a",
            {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5},
            ["2024-06-01", "2024-08-31"],
            "JSON",
            self._default_geojson(),
        )
        assert "geometries" in graph
        assert graph["geometries"]["process_id"] == "load_geojson"
        assert "aggregate_spatial" in graph
        assert graph["aggregate_spatial"]["process_id"] == "aggregate_spatial"

    def test_zonal_json_output(self):
        """Output format is JSON, not GTiff."""
        tools = self._make_tools()
        graph = tools._build_zonal_stats_graph(
            "sentinel-2-l2a",
            {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5},
            ["2024-06-01", "2024-08-31"],
            "GTiff",  # Even if caller passes GTiff, builder forces JSON
            self._default_geojson(),
        )
        assert graph["save"]["arguments"]["format"] == "JSON"

    def test_zonal_with_index(self):
        """Index computation nodes present when index_name provided."""
        tools = self._make_tools()
        graph = tools._build_zonal_stats_graph(
            "sentinel-2-l2a",
            {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5},
            ["2024-06-01", "2024-08-31"],
            "JSON",
            self._default_geojson(),
            index_name="ndvi",
        )
        # NDVI uses built-in ndvi process
        assert "ndvi" in graph
        assert graph["ndvi"]["process_id"] == "ndvi"

    def test_zonal_reducer(self):
        """Reducer callback uses the specified function."""
        tools = self._make_tools()
        graph = tools._build_zonal_stats_graph(
            "sentinel-2-l2a",
            {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5},
            ["2024-06-01", "2024-08-31"],
            "JSON",
            self._default_geojson(),
            reducer="median",
        )
        reducer_pg = graph["aggregate_spatial"]["arguments"]["reducer"]["process_graph"]
        assert "median" in reducer_pg
        assert reducer_pg["median"]["process_id"] == "median"

    def test_zonal_without_index(self):
        """Works on raw bands when no index specified."""
        tools = self._make_tools()
        graph = tools._build_zonal_stats_graph(
            "sentinel-2-l2a",
            {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5},
            ["2024-06-01", "2024-08-31"],
            "JSON",
            self._default_geojson(),
        )
        # No index computation nodes
        index_nodes = [k for k in graph if k in tools.INDEX_SPECS]
        assert len(index_nodes) == 0
        # Still has load, reduce_time, aggregate_spatial, save
        assert "load" in graph
        assert "reduce_time" in graph
        assert "aggregate_spatial" in graph


# ============================================================================
# Phase 3.2: Spatial filter graph builder tests
# ============================================================================

class TestSpatialFilterGraph:
    """Test _build_spatial_filter_graph produces correct process graphs."""

    def _make_tools(self):
        return OpenEOTools.__new__(OpenEOTools)

    def _default_args(self, kernel_type="gaussian"):
        return {
            "collection": "sentinel-2-l2a",
            "spatial_extent": {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5},
            "temporal_extent": ["2024-06-01", "2024-08-31"],
            "output_format": "GTiff",
            "kernel_type": kernel_type,
        }

    def test_gaussian_kernel(self):
        """apply_kernel node with correct 3x3 gaussian matrix."""
        tools = self._make_tools()
        graph = tools._build_spatial_filter_graph(**self._default_args("gaussian"))
        assert "apply_kernel" in graph
        assert graph["apply_kernel"]["process_id"] == "apply_kernel"
        kernel = graph["apply_kernel"]["arguments"]["kernel"]
        assert kernel == [[1, 2, 1], [2, 4, 2], [1, 2, 1]]

    def test_sobel_kernel(self):
        """apply_kernel node with sobel_x matrix."""
        tools = self._make_tools()
        graph = tools._build_spatial_filter_graph(**self._default_args("sobel_x"))
        kernel = graph["apply_kernel"]["arguments"]["kernel"]
        assert kernel == [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]

    def test_kernel_factor(self):
        """Gaussian has factor=1/16, others have factor=1."""
        tools = self._make_tools()
        gaussian_graph = tools._build_spatial_filter_graph(**self._default_args("gaussian"))
        sobel_graph = tools._build_spatial_filter_graph(**self._default_args("sobel_x"))
        assert gaussian_graph["apply_kernel"]["arguments"]["factor"] == 1.0 / 16
        assert sobel_graph["apply_kernel"]["arguments"]["factor"] == 1.0

    def test_kernel_with_index(self):
        """Index computed before kernel applied."""
        tools = self._make_tools()
        args = self._default_args("gaussian")
        args["index_name"] = "ndvi"
        graph = tools._build_spatial_filter_graph(**args)
        assert "ndvi" in graph
        assert "apply_kernel" in graph
        # apply_kernel depends on reduce_time which depends on ndvi
        assert graph["apply_kernel"]["arguments"]["data"] == {"from_node": "reduce_time"}

    def test_kernel_5x5(self):
        """5x5 gaussian kernel has correct dimensions."""
        tools = self._make_tools()
        args = self._default_args("gaussian")
        args["kernel_size"] = 5
        graph = tools._build_spatial_filter_graph(**args)
        kernel = graph["apply_kernel"]["arguments"]["kernel"]
        assert len(kernel) == 5
        assert len(kernel[0]) == 5
        assert graph["apply_kernel"]["arguments"]["factor"] == 1.0 / 256


# ============================================================================
# Phase 3.2: Detection type additions
# ============================================================================

class TestDetectAnalysisTypePhase32:
    """Test new analysis type detection for multi-index, zonal, spatial filter."""

    def _make_tools(self):
        return OpenEOTools.__new__(OpenEOTools)

    def test_detect_multi_index(self):
        tools = self._make_tools()
        assert tools._detect_analysis_type("compute ndvi and ndwi") == "multi_index"

    def test_detect_multi_index_combined(self):
        tools = self._make_tools()
        assert tools._detect_analysis_type("combined indices analysis") == "multi_index"

    def test_detect_zonal_stats(self):
        tools = self._make_tools()
        assert tools._detect_analysis_type("zonal statistics for the watershed") == "zonal_stats"

    def test_detect_spatial_filter(self):
        tools = self._make_tools()
        assert tools._detect_analysis_type("smooth the ndvi map") == "spatial_filter"

    def test_detect_sharpen(self):
        tools = self._make_tools()
        assert tools._detect_analysis_type("sharpen satellite imagery") == "spatial_filter"

    def test_detect_edge_detect(self):
        tools = self._make_tools()
        assert tools._detect_analysis_type("edge detect on the image") == "spatial_filter"

    def test_extract_indices_from_description(self):
        tools = self._make_tools()
        found = tools._extract_indices_from_description("compute ndvi and ndwi for the region")
        assert "ndvi" in found
        assert "ndwi" in found

    def test_extract_indices_default_fallback(self):
        """Returns default ["ndvi", "ndwi"] if fewer than 2 found."""
        tools = self._make_tools()
        found = tools._extract_indices_from_description("compute vegetation index")
        assert found == ["ndvi", "ndwi"]

    def test_detect_kernel_type_gaussian(self):
        tools = self._make_tools()
        assert tools._detect_kernel_type("smooth the image") == "gaussian"

    def test_detect_kernel_type_sobel(self):
        tools = self._make_tools()
        assert tools._detect_kernel_type("edge detection") == "sobel_x"

    def test_detect_kernel_type_sharpen(self):
        tools = self._make_tools()
        assert tools._detect_kernel_type("sharpen features") == "sharpen"


# ============================================================================
# Phase 3.2: New tool definition tests
# ============================================================================

class TestNewToolDefinitions:
    """Test that new tools are properly defined and registered."""

    def test_aggregate_spatial_tool_exists(self):
        """openeo_aggregate_spatial in TOOL_DEFINITIONS."""
        from openeo_ai.sdk.client import TOOL_DEFINITIONS
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "openeo_aggregate_spatial" in names

    def test_apply_kernel_tool_exists(self):
        """openeo_apply_kernel in TOOL_DEFINITIONS."""
        from openeo_ai.sdk.client import TOOL_DEFINITIONS
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "openeo_apply_kernel" in names

    def test_tool_count_is_at_least_73(self):
        """Tool count should not regress below the baseline platform capability."""
        from openeo_ai.sdk.client import TOOL_DEFINITIONS
        assert len(TOOL_DEFINITIONS) >= 73, f"Expected at least 73 tools, got {len(TOOL_DEFINITIONS)}"

    def test_aggregate_spatial_schema(self):
        """openeo_aggregate_spatial has required fields in schema."""
        from openeo_ai.sdk.client import TOOL_DEFINITIONS
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "openeo_aggregate_spatial")
        required = tool["input_schema"]["required"]
        assert "collection" in required
        assert "spatial_extent" in required
        assert "temporal_extent" in required
        assert "geometries" in required

    def test_apply_kernel_schema(self):
        """openeo_apply_kernel has required fields in schema."""
        from openeo_ai.sdk.client import TOOL_DEFINITIONS
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "openeo_apply_kernel")
        required = tool["input_schema"]["required"]
        assert "collection" in required
        assert "spatial_extent" in required
        assert "temporal_extent" in required
        assert "kernel_type" in required


# ============================================================================
# _qa_last routing-order tests
# ============================================================================

class TestQaLastRouting:
    """qa inspects files earlier specialists produce - it must never run first.

    Live defect (Palisades run 2026-07-26): planner returned routing
    ['qa', 'data', 'ml', 'viz']; qa probed a dNBR result that did not exist
    yet and leaked an 'I cannot execute jobs' dead-end reply to the user.
    """

    def test_qa_first_moves_to_end(self):
        assert _qa_last(["qa", "data", "ml", "viz"]) == ["data", "ml", "viz", "qa"]

    def test_qa_middle_moves_to_end(self):
        assert _qa_last(["data", "qa", "viz"]) == ["data", "viz", "qa"]

    def test_qa_already_last_unchanged(self):
        routing = ["data", "ml", "qa"]
        assert _qa_last(routing) == ["data", "ml", "qa"]

    def test_no_qa_unchanged(self):
        assert _qa_last(["data", "viz"]) == ["data", "viz"]

    def test_qa_only_unchanged(self):
        # A pure inspection query ("check my last result") is legitimately qa-only.
        assert _qa_last(["qa"]) == ["qa"]

    def test_duplicate_qa_collapses_to_single_trailing(self):
        assert _qa_last(["qa", "data", "qa"]) == ["data", "qa"]

    def test_empty_routing_unchanged(self):
        assert _qa_last([]) == []


class TestPlannerPromptQaOrderingRule:
    """The planner prompt must forbid qa-first plans at the source."""

    def test_planner_prompt_pins_qa_ordering_rule(self):
        from openeo_ai.sdk.system_prompts import PLANNER_SYSTEM_PROMPT
        assert "qa must NEVER be the first specialist" in PLANNER_SYSTEM_PROMPT
