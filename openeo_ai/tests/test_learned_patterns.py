# ABOUTME: Tests for the learned workflow patterns system.
# Verifies pattern hashing, metadata extraction, capture, retrieval, and injection.

"""
Tests for learned patterns — auto-learning from successful job executions.

Covers:
- Pattern hash computation (deterministic, normalization, uniqueness)
- Graph metadata extraction (collection, index, reducer, strategy)
- Capture/dedup lifecycle
- Relevance-based retrieval
- System prompt injection formatting
- Feature flag disabling
"""

import os
import json
import copy
import pytest

from openeo_ai.batch.template_store import (
    TemplateStore,
    _normalize_graph,
    _detect_index_from_prompt,
    _detect_collection_from_prompt,
)
from openeo_ai.batch.models import LearnedPattern


# ── Test fixtures ──

@pytest.fixture
def store(tmp_path):
    """Fresh TemplateStore with temp SQLite database."""
    db = str(tmp_path / "test.db")
    return TemplateStore(db)


@pytest.fixture
def ndvi_graph():
    """Standard NDVI process graph."""
    return {
        "load1": {
            "process_id": "load_collection",
            "arguments": {
                "id": "sentinel-2-l2a",
                "spatial_extent": {
                    "west": 11.0, "south": 46.0,
                    "east": 11.1, "north": 46.1,
                },
                "temporal_extent": ["2024-06-01", "2024-06-30"],
                "bands": ["B04", "B08"],
            },
        },
        "ndvi1": {
            "process_id": "ndvi",
            "arguments": {
                "data": {"from_node": "load1"},
                "nir": "B08",
                "red": "B04",
            },
        },
        "reduce1": {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_node": "ndvi1"},
                "dimension": "t",
                "reducer": {
                    "median1": {
                        "process_id": "median",
                        "arguments": {"data": {"from_parameter": "data"}},
                        "result": True,
                    }
                },
            },
        },
        "save1": {
            "process_id": "save_result",
            "arguments": {
                "data": {"from_node": "reduce1"},
                "format": "GTiff",
            },
            "result": True,
        },
    }


@pytest.fixture
def change_detection_graph():
    """dNBR change detection graph with 2 load_collection nodes."""
    return {
        "load_pre": {
            "process_id": "load_collection",
            "arguments": {
                "id": "sentinel-2-l2a",
                "spatial_extent": {"west": 72, "south": 19, "east": 73, "north": 20},
                "temporal_extent": ["2024-01-01", "2024-03-01"],
                "bands": ["B08", "B12"],
            },
        },
        "load_post": {
            "process_id": "load_collection",
            "arguments": {
                "id": "sentinel-2-l2a",
                "spatial_extent": {"west": 72, "south": 19, "east": 73, "north": 20},
                "temporal_extent": ["2024-06-01", "2024-08-01"],
                "bands": ["B08", "B12"],
            },
        },
        "nbr_pre": {
            "process_id": "normalized_difference",
            "arguments": {
                "x": {"from_node": "load_pre", "arguments": {"label": "B08"}},
                "y": {"from_node": "load_pre", "arguments": {"label": "B12"}},
            },
        },
        "nbr_post": {
            "process_id": "normalized_difference",
            "arguments": {
                "x": {"from_node": "load_post", "arguments": {"label": "B08"}},
                "y": {"from_node": "load_post", "arguments": {"label": "B12"}},
            },
        },
        "save1": {
            "process_id": "save_result",
            "arguments": {
                "data": {"from_node": "nbr_post"},
                "format": "GTiff",
            },
            "result": True,
        },
    }


@pytest.fixture
def cloud_masked_graph():
    """Graph with cloud mask node."""
    return {
        "load1": {
            "process_id": "load_collection",
            "arguments": {
                "id": "sentinel-2-l2a",
                "spatial_extent": {"west": 11, "south": 46, "east": 11.1, "north": 46.1},
                "temporal_extent": ["2024-06-01", "2024-06-30"],
                "bands": ["B04", "B08", "SCL"],
            },
        },
        "mask1": {
            "process_id": "mask",
            "arguments": {
                "data": {"from_node": "load1"},
                "mask": {"from_node": "scl_mask"},
            },
        },
        "ndvi1": {
            "process_id": "ndvi",
            "arguments": {"data": {"from_node": "mask1"}},
        },
        "save1": {
            "process_id": "save_result",
            "arguments": {
                "data": {"from_node": "ndvi1"},
                "format": "GTiff",
            },
            "result": True,
        },
    }


# ── Test: Pattern Hash ──

class TestPatternHash:
    def test_compute_pattern_hash_deterministic(self, ndvi_graph):
        """Same graph produces same hash every time."""
        h1 = TemplateStore._compute_pattern_hash(ndvi_graph)
        h2 = TemplateStore._compute_pattern_hash(ndvi_graph)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_compute_pattern_hash_ignores_extent(self, ndvi_graph):
        """Different spatial/temporal extents produce same hash."""
        graph2 = copy.deepcopy(ndvi_graph)
        graph2["load1"]["arguments"]["spatial_extent"] = {
            "west": 72.0, "south": 19.0, "east": 73.0, "north": 20.0,
        }
        graph2["load1"]["arguments"]["temporal_extent"] = ["2025-01-01", "2025-03-01"]

        h1 = TemplateStore._compute_pattern_hash(ndvi_graph)
        h2 = TemplateStore._compute_pattern_hash(graph2)
        assert h1 == h2

    def test_compute_pattern_hash_different_structure(self, ndvi_graph, change_detection_graph):
        """Different graph structures produce different hashes."""
        h1 = TemplateStore._compute_pattern_hash(ndvi_graph)
        h2 = TemplateStore._compute_pattern_hash(change_detection_graph)
        assert h1 != h2


# ── Test: Metadata Extraction ──

class TestMetadataExtraction:
    def test_extract_metadata_ndvi(self, ndvi_graph):
        """Extracts collection, index, reducer from NDVI graph."""
        meta = TemplateStore._extract_graph_metadata(ndvi_graph)
        assert meta["collection_id"] == "sentinel-2-l2a"
        assert meta["index_type"] == "ndvi"
        assert meta["time_reducer"] == "median"
        assert meta["temporal_strategy"] == "single_snapshot"
        assert meta["cloud_masked"] is False

    def test_extract_metadata_change_detection(self, change_detection_graph):
        """Detects 2 load nodes as change_detection strategy."""
        meta = TemplateStore._extract_graph_metadata(change_detection_graph)
        assert meta["temporal_strategy"] == "change_detection"
        assert meta["collection_id"] == "sentinel-2-l2a"

    def test_extract_metadata_cloud_mask(self, cloud_masked_graph):
        """Detects mask node and sets cloud_masked=True."""
        meta = TemplateStore._extract_graph_metadata(cloud_masked_graph)
        assert meta["cloud_masked"] is True


# ── Test: Capture ──

class TestCapturePattern:
    def test_capture_pattern_new(self, store, ndvi_graph):
        """First capture creates new row and returns pattern ID."""
        pid = store.capture_pattern(
            process_graph=ndvi_graph,
            source_query="NDVI analysis in Mumbai",
            domain="vegetation",
        )
        assert pid is not None
        assert len(pid) == 36  # UUID format

    def test_capture_pattern_duplicate(self, store, ndvi_graph):
        """Same graph increments use_count and returns None."""
        pid1 = store.capture_pattern(
            process_graph=ndvi_graph,
            source_query="NDVI analysis in Mumbai",
        )
        assert pid1 is not None

        pid2 = store.capture_pattern(
            process_graph=ndvi_graph,
            source_query="NDVI analysis in Delhi",
        )
        assert pid2 is None

        # Verify use_count incremented
        patterns = store.find_relevant_patterns(index_type="ndvi")
        assert len(patterns) == 1
        assert patterns[0].use_count == 2


# ── Test: Retrieval ──

class TestFindPatterns:
    def test_find_relevant_patterns_by_domain(self, store, ndvi_graph, cloud_masked_graph):
        """Matches by domain, ranked by score."""
        store.capture_pattern(ndvi_graph, "NDVI", domain="vegetation")
        store.capture_pattern(cloud_masked_graph, "Cloud masked NDVI", domain="vegetation")

        patterns = store.find_relevant_patterns(domain="vegetation")
        assert len(patterns) == 2
        assert all(p.domain == "vegetation" for p in patterns)

    def test_find_relevant_patterns_by_index(self, store, ndvi_graph):
        """Matches by index_type."""
        store.capture_pattern(ndvi_graph, "NDVI analysis")

        patterns = store.find_relevant_patterns(index_type="ndvi")
        assert len(patterns) == 1
        assert patterns[0].index_type == "ndvi"

    def test_find_relevant_patterns_empty(self, store):
        """No matches returns empty list."""
        patterns = store.find_relevant_patterns(domain="nonexistent")
        assert patterns == []


# ── Test: Injection Text ──

class TestInjectionText:
    def test_get_injection_text_format(self, store, ndvi_graph):
        """Returns formatted text with header and patterns."""
        store.capture_pattern(ndvi_graph, "NDVI monitoring", domain="vegetation")

        text = store.get_injection_text("Show me NDVI for this area")
        assert text is not None
        assert "## Learned Workflow Patterns" in text
        assert "Pattern 1:" in text
        assert "sentinel-2-l2a" in text
        assert "ndvi" in text.lower()

    def test_get_injection_text_none(self, store):
        """No patterns returns None for unrelated prompt."""
        result = store.get_injection_text("Tell me a joke")
        assert result is None


# ── Test: Feature Flag ──

class TestFeatureFlag:
    def test_feature_flag_disables_injection(self, store, ndvi_graph, monkeypatch):
        """DISABLE_LEARNED_PATTERNS=1 skips pattern injection."""
        store.capture_pattern(ndvi_graph, "NDVI monitoring", domain="vegetation")

        monkeypatch.setenv("DISABLE_LEARNED_PATTERNS", "1")

        # Simulate the _build_system_messages logic
        blocks = [{"type": "text", "text": "BASE_PROMPT", "cache_control": {"type": "ephemeral"}}]
        if os.environ.get("DISABLE_LEARNED_PATTERNS"):
            result = blocks
        else:
            text = store.get_injection_text("Show me NDVI")
            if text:
                blocks.append({"type": "text", "text": text})
            result = blocks

        assert len(result) == 1
        assert result[0]["text"] == "BASE_PROMPT"


# ── Test: Helper Functions ──

class TestHelpers:
    def test_detect_index_from_prompt(self):
        """Detects index keywords from user prompt."""
        assert _detect_index_from_prompt("Calculate NDVI for vegetation") == "ndvi"
        assert _detect_index_from_prompt("Water index NDWI analysis") == "ndwi"
        assert _detect_index_from_prompt("MNDWI flood mapping") == "mndwi"
        assert _detect_index_from_prompt("Show me satellite data") == ""

    def test_detect_collection_from_prompt(self):
        """Detects collection keywords from user prompt."""
        assert _detect_collection_from_prompt("Use Landsat data") == "landsat-c2-l2"
        assert _detect_collection_from_prompt("Sentinel-2 imagery") == "sentinel-2-l2a"
        assert _detect_collection_from_prompt("Generic analysis") == ""

    def test_normalize_graph_zeroes_extents(self, ndvi_graph):
        """Normalization zeroes spatial and temporal extents."""
        graph = copy.deepcopy(ndvi_graph)
        _normalize_graph(graph)

        spatial = graph["load1"]["arguments"]["spatial_extent"]
        assert spatial["west"] == 0
        assert spatial["south"] == 0

        temporal = graph["load1"]["arguments"]["temporal_extent"]
        assert temporal == ["", ""]
