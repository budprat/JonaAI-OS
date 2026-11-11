# ABOUTME: Proves _tool_matches rejects substring false positives and accepts
# ABOUTME: MCP-prefixed names; plus process-graph artifact scoring.
import uuid

from openeo_ai.evals.run_experiment import _bridge_session_id, _tool_matches


def test_bridge_session_id_is_valid_uuid():
    # ai_sessions.id is a Postgres uuid column; non-uuid session ids break
    # every experiment turn at the session lookup.
    sid = _bridge_session_id()
    assert str(uuid.UUID(sid)) == sid


def test_bridge_session_id_is_fresh_per_call():
    # Each experiment turn must get a fresh session (no cross-run resume).
    assert _bridge_session_id() != _bridge_session_id()


def test_tool_match_rejects_substring_false_positive():
    # "load" must NOT match "download_result" (old `expected in t` bug).
    assert _tool_matches("load", ["download_result"]) is False


def test_tool_match_exact():
    assert _tool_matches("openeo_execute", ["openeo_execute"]) is True


def test_tool_match_mcp_prefix():
    assert _tool_matches("openeo_execute", ["mcp__openeo__openeo_execute"]) is True


def test_tool_match_rejects_longer_suffix():
    # "openeo_execute" must NOT match "openeo_execute_and_download".
    assert _tool_matches("openeo_execute", ["openeo_execute_and_download"]) is False


from openeo_ai.evals.artifacts import score_process_graph  # noqa: E402


def _pg_event():
    return {
        "type": "process_graph",
        "data": {
            "nodes": [
                {
                    "id": "load1",
                    "process_id": "load_collection",
                    "arguments": {
                        "id": "SENTINEL2_L2A",
                        "spatial_extent": {"west": 50.0, "south": 29.0, "east": 50.5, "north": 29.5},
                        "temporal_extent": ["2024-01-01", "2024-03-31"],
                    },
                }
            ],
            "edges": [],
        },
        "title": "graph",
    }


def test_score_process_graph_extracts_collection_and_extents():
    result = score_process_graph([_pg_event()])
    assert result["collection"] == "SENTINEL2_L2A"
    assert result["temporal"] == ["2024-01-01", "2024-03-31"]
    assert result["bbox"] == [50.0, 29.0, 50.5, 29.5]


def test_score_process_graph_none_when_absent():
    result = score_process_graph([{"type": "map", "data": {}}])
    assert result == {"collection": None, "temporal": None, "bbox": None}


from openeo_ai.evals.golden_dataset import GoldenQuery  # noqa: E402


def test_golden_query_accepts_typed_expectations():
    q = GoldenQuery(
        id="x", category="c", query="q",
        expected_tools=["openeo_execute"], expected_output="map",
        expected_collection="SENTINEL2_L2A",
    )
    assert q.expected_collection == "SENTINEL2_L2A"
    assert q.expected_bbox == ()
    assert q.expected_temporal == ()
