# ABOUTME: Unit tests for OpenEO AI tools.
# Tests tool functions, error handling, and integration with Claude SDK.

"""
Unit tests for OpenEO AI tools.

Tests:
- Tool execution and response formatting
- Error handling in tool functions
- Integration with validation and error handler
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openeo_ai.utils.error_handler import (
    ErrorHandler,
    ErrorType,
    Severity,
    handle_error,
    check_oversight,
)
from openeo_ai.utils.schema_validator import (
    validate_tool_input,
    validate_process_graph,
)


# ============================================================================
# Error Handler Tests
# ============================================================================

class TestErrorHandler:
    """Tests for error handler functionality."""

    def test_classify_validation_error(self):
        """Test classification of validation errors."""
        handler = ErrorHandler()

        errors = [
            ValueError("missing required argument 'id'"),
            ValueError("invalid format for date"),
            TypeError("expected string, got int"),
        ]

        for error in errors:
            result = handler.classify_error(error)
            assert result == ErrorType.VALIDATION, f"Failed for: {error}"

    def test_classify_network_error(self):
        """Test classification of network errors."""
        handler = ErrorHandler()

        errors = [
            ConnectionError("Failed to connect"),
            OSError("Network unreachable"),
            Exception("connection refused"),
        ]

        for error in errors:
            result = handler.classify_error(error)
            assert result == ErrorType.NETWORK, f"Failed for: {error}"

    def test_classify_data_quality_error(self):
        """Test classification of data quality errors."""
        handler = ErrorHandler()

        errors = [
            ValueError("no data found"),
            Exception("empty result"),
            ValueError("no items returned from STAC"),
        ]

        for error in errors:
            result = handler.classify_error(error)
            assert result == ErrorType.DATA_QUALITY, f"Failed for: {error}"

    def test_classify_permission_error(self):
        """Test classification of permission errors."""
        handler = ErrorHandler()

        errors = [
            PermissionError("permission denied"),
            Exception("access denied"),
            Exception("unauthorized"),
        ]

        for error in errors:
            result = handler.classify_error(error)
            assert result == ErrorType.PERMISSION, f"Failed for: {error}"

    def test_classify_resource_error(self):
        """Test classification of resource errors."""
        handler = ErrorHandler()

        errors = [
            MemoryError("out of memory"),
            Exception("memory limit exceeded"),
            Exception("request too large"),
        ]

        for error in errors:
            result = handler.classify_error(error)
            assert result == ErrorType.RESOURCE, f"Failed for: {error}"

    def test_get_severity(self):
        """Test severity assignment."""
        handler = ErrorHandler()

        assert handler.get_severity(ErrorType.VALIDATION) == Severity.WARNING
        assert handler.get_severity(ErrorType.DATA_QUALITY) == Severity.WARNING
        assert handler.get_severity(ErrorType.NETWORK) == Severity.ERROR
        assert handler.get_severity(ErrorType.PERMISSION) == Severity.CRITICAL
        assert handler.get_severity(ErrorType.RESOURCE) == Severity.CRITICAL

    def test_handle_exception(self, validation_error):
        """Test handling an exception."""
        handler = ErrorHandler()

        result = handler.handle_exception(validation_error)

        assert result.error_type == ErrorType.VALIDATION
        assert result.severity == Severity.WARNING
        assert "missing required" in result.message.lower()
        assert len(result.suggestions) > 0

    def test_handle_exception_with_context(self, validation_error):
        """Test handling with context."""
        from openeo_ai.utils.error_handler import ErrorContext

        handler = ErrorHandler()
        context = ErrorContext(
            tool_name="openeo_generate_graph",
            tool_input={"description": "test"}
        )

        result = handler.handle_exception(validation_error, context)

        assert result.context == context
        assert result.context.tool_name == "openeo_generate_graph"

    def test_requires_oversight_large_extent(self):
        """Test oversight requirement for large extents."""
        handler = ErrorHandler()

        tool_input = {
            "spatial_extent": {
                "west": -10,
                "south": 30,
                "east": 10,
                "north": 50  # 20 degrees wide
            }
        }

        needs_oversight, reason = handler.requires_oversight("openeo_start_job", tool_input)
        assert needs_oversight is True
        assert "extent" in reason.lower()

    def test_requires_oversight_normal_extent(self):
        """Test no oversight for normal extents."""
        handler = ErrorHandler()

        tool_input = {
            "spatial_extent": {
                "west": 11.0,
                "south": 46.0,
                "east": 11.1,
                "north": 46.1  # 0.1 degrees
            }
        }

        needs_oversight, reason = handler.requires_oversight("openeo_start_job", tool_input)
        assert needs_oversight is False

    def test_to_dict(self, validation_error):
        """Test conversion to dictionary."""
        handler = ErrorHandler()
        result = handler.handle_exception(validation_error)

        result_dict = result.to_dict()

        assert "error_type" in result_dict
        assert "severity" in result_dict
        assert "message" in result_dict
        assert "suggestions" in result_dict
        assert result_dict["error_type"] == "validation"

    def test_to_user_message(self, validation_error):
        """Test conversion to user message."""
        handler = ErrorHandler()
        result = handler.handle_exception(validation_error)

        message = result.to_user_message()

        assert "Validation Error" in message
        assert "Suggestions" in message


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_handle_error(self, validation_error):
        """Test handle_error function."""
        result = handle_error(
            validation_error,
            tool_name="openeo_validate_graph",
            operation="validating process graph"
        )

        assert result.error_type == ErrorType.VALIDATION
        assert result.context.tool_name == "openeo_validate_graph"

    def test_check_oversight(self):
        """Test check_oversight function."""
        needs_oversight, reason = check_oversight(
            "openeo_start_job",
            {"spatial_extent": {"west": -10, "south": 30, "east": 10, "north": 50}}
        )
        assert needs_oversight is True

        needs_oversight, reason = check_oversight(
            "openeo_list_collections",
            {}
        )
        assert needs_oversight is False


# ============================================================================
# Schema Validator Tests
# ============================================================================

class TestSchemaValidator:
    """Tests for schema validation functionality."""

    def test_validate_process_graph_valid(self, simple_process_graph):
        """Test validation of valid process graph."""
        result = validate_process_graph(simple_process_graph)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_validate_process_graph_invalid(self, invalid_process_graph):
        """Test validation of invalid process graph."""
        result = validate_process_graph(invalid_process_graph)

        assert result.valid is False
        assert len(result.errors) > 0

    def test_validate_process_graph_missing_result(self):
        """Test validation catches missing result node."""
        pg = {
            "load1": {
                "process_id": "load_collection",
                "arguments": {"id": "sentinel-2-l2a"}
            }
        }

        result = validate_process_graph(pg)

        assert result.valid is False
        error_messages = [e.message for e in result.errors]
        assert any("result" in m.lower() for m in error_messages)

    def test_validate_process_graph_circular(self, cyclic_process_graph):
        """Test validation catches circular references."""
        result = validate_process_graph(cyclic_process_graph)

        assert result.valid is False
        error_messages = [e.message for e in result.errors]
        assert any("circular" in m.lower() for m in error_messages)

    def test_validate_tool_input_valid(self, generate_graph_input):
        """Test validation of valid tool input."""
        result = validate_tool_input("openeo_generate_graph", generate_graph_input)

        assert result.valid is True

    def test_validate_tool_input_missing_required(self):
        """Test validation catches missing required fields."""
        tool_input = {
            "description": "Calculate NDVI"
            # Missing: collection, spatial_extent, temporal_extent
        }

        result = validate_tool_input("openeo_generate_graph", tool_input)

        assert result.valid is False
        assert len(result.errors) >= 3  # At least 3 missing fields

    def test_validate_tool_input_invalid_extent(self):
        """Test validation of invalid spatial extent."""
        tool_input = {
            "description": "Test",
            "collection": "sentinel-2-l2a",
            "spatial_extent": {
                "west": 200,  # Invalid: > 180
                "south": 46,
                "east": 11,
                "north": 47
            },
            "temporal_extent": ["2024-01-01", "2024-01-31"]
        }

        result = validate_tool_input("openeo_generate_graph", tool_input)

        # Should have validation error for west > 180
        assert result.valid is False or len(result.warnings) > 0


class TestDeepValidation:
    """Tests for deep semantic validation."""

    def test_deep_validate_invalid_bands(self):
        """Test deep validation catches invalid band names."""
        from openeo_ai.utils.schema_validator import deep_validate_graph

        pg = {
            "load1": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "sentinel-2-l2a",
                    "spatial_extent": {"west": 11, "south": 46, "east": 11.1, "north": 46.1},
                    "temporal_extent": ["2024-06-01", "2024-06-30"],
                    "bands": ["B04", "B08"]  # Old-style band names
                }
            },
            "save1": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "load1"}, "format": "GTiff"},
                "result": True
            }
        }

        result = deep_validate_graph(pg)

        # Should catch invalid bands
        assert result.valid is False
        error_messages = [e.message for e in result.errors]
        assert any("band" in m.lower() for m in error_messages)

    def test_deep_validate_invalid_date(self):
        """Test deep validation catches invalid date format."""
        from openeo_ai.utils.schema_validator import deep_validate_graph

        pg = {
            "load1": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "sentinel-2-l2a",
                    "spatial_extent": {"west": 11, "south": 46, "east": 11.1, "north": 46.1},
                    "temporal_extent": ["June 1 2024", "2024-06-30"]  # Invalid format
                }
            },
            "save1": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "load1"}, "format": "GTiff"},
                "result": True
            }
        }

        result = deep_validate_graph(pg)

        # Should catch invalid date format
        error_messages = [e.message for e in result.errors]
        assert any("date" in m.lower() or "format" in m.lower() for m in error_messages)

    def test_deep_validate_large_extent_warning(self):
        """Test deep validation warns about large extents."""
        from openeo_ai.utils.schema_validator import deep_validate_graph

        pg = {
            "load1": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "sentinel-2-l2a",
                    "spatial_extent": {"west": 0, "south": 40, "east": 10, "north": 50},  # 10 degrees
                    "temporal_extent": ["2024-06-01", "2024-06-30"],
                    "bands": ["red", "nir"]
                }
            },
            "save1": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "load1"}, "format": "GTiff"},
                "result": True
            }
        }

        result = deep_validate_graph(pg)

        # Should warn about large extent
        assert any("large" in w.lower() or "extent" in w.lower() for w in result.warnings)


# ============================================================================
# Integration Tests
# ============================================================================

class TestToolExecution:
    """Integration tests for tool execution."""

    @pytest.mark.asyncio
    async def test_validate_graph_tool(self, simple_process_graph):
        """Test the validate_graph tool function."""
        from openeo_ai.tools.validation_tools import validate_graph_tool

        result = await validate_graph_tool({"process_graph": simple_process_graph})

        assert "content" in result
        content = result["content"]
        assert len(content) > 0

        # Parse the JSON response
        text = content[0].get("text", "{}")
        data = json.loads(text)
        assert "valid" in data
        assert data["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_graph_tool_invalid(self, invalid_process_graph):
        """Test validate_graph tool with invalid input."""
        from openeo_ai.tools.validation_tools import validate_graph_tool

        result = await validate_graph_tool({"process_graph": invalid_process_graph})

        content = result["content"]
        text = content[0].get("text", "{}")
        data = json.loads(text)

        assert data["valid"] is False
        assert len(data["errors"]) > 0


class TestToolWithErrorHandler:
    """Test tools integrated with error handler."""

    @pytest.mark.asyncio
    async def test_tool_error_handling(self):
        """Test that tool errors are properly handled."""
        from openeo_ai.utils.error_handler import ErrorHandler, ErrorContext

        handler = ErrorHandler()

        # Simulate a tool error
        try:
            raise ValueError("Missing required argument 'id'")
        except Exception as e:
            context = ErrorContext(
                tool_name="openeo_create_job",
                tool_input={"title": "Test"}
            )
            result = handler.handle_exception(e, context)

            assert result.error_type == ErrorType.VALIDATION
            assert result.context.tool_name == "openeo_create_job"
            assert len(result.suggestions) > 0


# ============================================================================
# Temporal Reduction Tests
# ============================================================================

class TestTemporalReduction:
    """Test time_reducer='none' skips temporal reduction in all graph builders."""

    BBOX = {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5}
    DATES = ["2024-01-01", "2024-03-01"]

    @pytest.fixture
    def tools(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        return OpenEOTools(openeo_url="http://localhost:8080/openeo/1.2")

    def test_index_graph_no_reduce(self, tools):
        """time_reducer='none' skips reduce_time in index graph."""
        graph = tools._build_index_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
            index_name="ndvi", time_reducer="none",
        )
        assert "reduce_time" not in graph
        assert graph["save"]["arguments"]["data"]["from_node"] == "ndvi"

    def test_index_graph_default_has_reduce(self, tools):
        """Default time_reducer adds reduce_time node."""
        graph = tools._build_index_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
            index_name="ndvi",
        )
        assert "reduce_time" in graph

    def test_index_graph_max_reducer(self, tools):
        """time_reducer='max' uses max in reduce_time."""
        graph = tools._build_index_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
            index_name="ndvi", time_reducer="max",
        )
        assert "reduce_time" in graph
        reducer_pg = graph["reduce_time"]["arguments"]["reducer"]["process_graph"]
        assert "max" in reducer_pg

    def test_evi_graph_no_reduce(self, tools):
        """time_reducer='none' skips reduce_time in EVI graph."""
        graph = tools._build_evi_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
            time_reducer="none",
        )
        assert "reduce_time" not in graph
        # EVI formula should wire to "load" directly
        assert graph["evi"]["arguments"]["data"]["from_node"] == "load"

    def test_evi_graph_default_has_reduce(self, tools):
        """Default EVI graph has reduce_time."""
        graph = tools._build_evi_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
        )
        assert "reduce_time" in graph

    def test_temporal_composite_no_reduce(self, tools):
        """time_reducer='none' skips both reduce_time and aggregate_time."""
        graph = tools._build_temporal_composite(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
            time_reducer="none",
        )
        assert "reduce_time" not in graph
        assert "aggregate_time" not in graph
        assert graph["save"]["arguments"]["data"]["from_node"] == "load"

    def test_temporal_composite_default_has_reduce(self, tools):
        """Default temporal composite has reduce_time."""
        graph = tools._build_temporal_composite(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
        )
        assert "reduce_time" in graph

    def test_multi_index_graph_no_reduce(self, tools):
        """time_reducer='none' skips reduce_time in multi-index graph."""
        graph = tools._build_multi_index_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
            indices=["ndvi", "ndwi"], time_reducer="none",
        )
        assert "reduce_time" not in graph

    def test_spatial_filter_graph_no_reduce(self, tools):
        """time_reducer='none' skips reduce_time in spatial filter graph."""
        graph = tools._build_spatial_filter_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES, "GTiff",
            time_reducer="none",
        )
        assert "reduce_time" not in graph
        # apply_kernel should wire to load
        assert graph["apply_kernel"]["arguments"]["data"]["from_node"] == "load"

    def test_zonal_stats_graph_no_reduce(self, tools):
        """time_reducer='none' skips reduce_time in zonal stats graph."""
        geom = {
            "type": "Polygon",
            "coordinates": [[[11.0, 46.0], [11.5, 46.0], [11.5, 46.5], [11.0, 46.5], [11.0, 46.0]]]
        }
        graph = tools._build_zonal_stats_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES, "JSON",
            geometries=geom, time_reducer="none",
        )
        assert "reduce_time" not in graph
        # aggregate_spatial should wire to load
        assert graph["aggregate_spatial"]["arguments"]["data"]["from_node"] == "load"

    @pytest.mark.asyncio
    async def test_generate_graph_raw_fallback(self, tools):
        """time_reducer='none' on generic fallback produces load → save."""
        graph = await tools.generate_process_graph(
            "download raw satellite imagery",
            "sentinel-2-l2a", self.BBOX, self.DATES,
            time_reducer="none",
        )
        assert "reduce_time" not in graph
        assert "load" in graph
        assert "save" in graph
        assert graph["save"]["arguments"]["data"]["from_node"] == "load"

    @pytest.mark.asyncio
    async def test_generate_graph_default_fallback_has_reduce(self, tools):
        """Default generate_process_graph on generic fallback adds reduce_time."""
        graph = await tools.generate_process_graph(
            "download raw satellite imagery",
            "sentinel-2-l2a", self.BBOX, self.DATES,
        )
        assert "reduce_time" in graph
        assert graph["save"]["arguments"]["data"]["from_node"] == "reduce_time"


# ============================================================================
# Compose Graph Tests
# ============================================================================

class TestComposeGraph:
    """Tests for openeo_compose_graph composable pipeline builder."""

    BBOX = {"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5}
    DATES = ["2024-01-01", "2024-06-01"]

    @pytest.fixture
    def tools(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        return OpenEOTools(openeo_url="http://localhost:8080/openeo/1.2")

    def test_simple_load_reduce_save(self, tools):
        """Minimal workflow: load → reduce_time → save with auto-wiring."""
        graph = tools.compose_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES,
            steps=[
                {"op": "load", "bands": ["red", "nir"]},
                {"op": "reduce_time", "reducer": "median"},
                {"op": "save"},
            ],
        )
        assert "load" in graph
        assert "reduce_time" in graph
        assert "save" in graph
        # Auto-wiring: reduce_time reads from load
        assert graph["reduce_time"]["arguments"]["data"]["from_node"] == "load"
        # Save reads from reduce_time
        assert graph["save"]["arguments"]["data"]["from_node"] == "reduce_time"
        assert graph["save"].get("result") is True

    def test_index_workflow(self, tools):
        """load → index → reduce_time → save."""
        graph = tools.compose_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES,
            steps=[
                {"op": "load"},
                {"op": "index", "index_name": "ndwi"},
                {"op": "reduce_time"},
                {"op": "save"},
            ],
        )
        assert "load" in graph
        assert "ndwi" in graph
        assert "reduce_time" in graph
        assert "save" in graph
        # NDWI uses normalized_difference, check wiring
        assert graph["ndwi"]["arguments"]["x"]["from_node"] == "ndwi_x"
        assert graph["reduce_time"]["arguments"]["data"]["from_node"] == "ndwi"

    def test_cloud_masked_index(self, tools):
        """load → cloud_mask → index → reduce_time → save."""
        graph = tools.compose_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES,
            steps=[
                {"op": "load"},
                {"op": "cloud_mask"},
                {"op": "index", "index_name": "ndvi"},
                {"op": "reduce_time", "reducer": "mean"},
                {"op": "save"},
            ],
        )
        assert "load" in graph
        assert "apply_mask" in graph
        assert "ndvi" in graph
        assert "reduce_time" in graph
        # NDVI uses built-in ndvi process, should wire to apply_mask
        assert graph["ndvi"]["arguments"]["data"]["from_node"] == "apply_mask"
        # Reducer should be mean
        reducer_pg = graph["reduce_time"]["arguments"]["reducer"]["process_graph"]
        assert "mean" in reducer_pg

    def test_spatial_filter_on_index(self, tools):
        """load → index → spatial_filter → reduce_time → save."""
        graph = tools.compose_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES,
            steps=[
                {"op": "load"},
                {"op": "index", "index_name": "ndvi"},
                {"op": "spatial_filter", "kernel_type": "gaussian_3"},
                {"op": "reduce_time"},
                {"op": "save"},
            ],
        )
        assert "apply_kernel" in graph
        # Kernel reads from ndvi
        assert graph["apply_kernel"]["arguments"]["data"]["from_node"] == "ndvi"
        # reduce_time reads from apply_kernel
        assert graph["reduce_time"]["arguments"]["data"]["from_node"] == "apply_kernel"

    def test_monthly_aggregation(self, tools):
        """load → index → aggregate_time(month) → save."""
        graph = tools.compose_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES,
            steps=[
                {"op": "load"},
                {"op": "index", "index_name": "ndvi"},
                {"op": "aggregate_time", "period": "month"},
                {"op": "save"},
            ],
        )
        assert "aggregate_time" in graph
        agg = graph["aggregate_time"]
        assert agg["process_id"] == "aggregate_temporal_period"
        assert agg["arguments"]["period"] == "month"
        assert agg["arguments"]["data"]["from_node"] == "ndvi"
        assert graph["save"]["arguments"]["data"]["from_node"] == "aggregate_time"

    def test_full_pipeline(self, tools):
        """All 6 ops: load → cloud_mask → index → spatial_filter → reduce_time → save."""
        graph = tools.compose_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES,
            steps=[
                {"op": "load"},
                {"op": "cloud_mask"},
                {"op": "index", "index_name": "nbr"},
                {"op": "spatial_filter", "kernel_type": "laplacian"},
                {"op": "reduce_time", "reducer": "max"},
                {"op": "save", "output_format": "netCDF"},
            ],
        )
        assert "load" in graph
        assert "apply_mask" in graph
        assert "nbr" in graph
        assert "apply_kernel" in graph
        assert "reduce_time" in graph
        assert "save" in graph
        # Verify chain
        assert graph["apply_kernel"]["arguments"]["data"]["from_node"] == "nbr"
        assert graph["reduce_time"]["arguments"]["data"]["from_node"] == "apply_kernel"
        assert graph["save"]["arguments"]["format"] == "netCDF"

    def test_band_auto_detection(self, tools):
        """Bands auto-resolved from index step when load has no explicit bands."""
        graph = tools.compose_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES,
            steps=[
                {"op": "load"},
                {"op": "index", "index_name": "ndwi"},
                {"op": "reduce_time"},
                {"op": "save"},
            ],
        )
        load_bands = graph["load"]["arguments"]["bands"]
        assert "green" in load_bands
        assert "nir" in load_bands

    def test_explicit_bands_override(self, tools):
        """Explicit bands in load step override auto-detection."""
        graph = tools.compose_graph(
            "sentinel-2-l2a", self.BBOX, self.DATES,
            steps=[
                {"op": "load", "bands": ["red", "green", "blue"]},
                {"op": "reduce_time"},
                {"op": "save"},
            ],
        )
        load_bands = graph["load"]["arguments"]["bands"]
        assert load_bands == ["red", "green", "blue"]

    def test_error_empty_steps(self, tools):
        """Empty steps raises ValueError."""
        with pytest.raises(ValueError, match="Steps array cannot be empty"):
            tools.compose_graph(
                "sentinel-2-l2a", self.BBOX, self.DATES, steps=[]
            )

    def test_error_load_not_first(self, tools):
        """Missing load as first step raises ValueError."""
        with pytest.raises(ValueError, match="First step must be 'load'"):
            tools.compose_graph(
                "sentinel-2-l2a", self.BBOX, self.DATES,
                steps=[
                    {"op": "index", "index_name": "ndvi"},
                    {"op": "save"},
                ],
            )

    def test_error_save_not_last(self, tools):
        """Missing save as last step raises ValueError."""
        with pytest.raises(ValueError, match="Last step must be 'save'"):
            tools.compose_graph(
                "sentinel-2-l2a", self.BBOX, self.DATES,
                steps=[
                    {"op": "load"},
                    {"op": "index", "index_name": "ndvi"},
                ],
            )

    def test_error_unknown_index(self, tools):
        """Unknown index name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown index"):
            tools.compose_graph(
                "sentinel-2-l2a", self.BBOX, self.DATES,
                steps=[
                    {"op": "load"},
                    {"op": "index", "index_name": "nonexistent"},
                    {"op": "save"},
                ],
            )

    def test_error_missing_period(self, tools):
        """aggregate_time without period raises ValueError."""
        with pytest.raises(ValueError, match="requires 'period'"):
            tools.compose_graph(
                "sentinel-2-l2a", self.BBOX, self.DATES,
                steps=[
                    {"op": "load"},
                    {"op": "aggregate_time"},
                    {"op": "save"},
                ],
            )


# ============================================================================
# Raw Graph Tests
# ============================================================================

class TestRawGraph:
    """Tests for openeo_raw_graph passthrough with validation."""

    @pytest.fixture
    def tools(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        return OpenEOTools(openeo_url="http://localhost:8080/openeo/1.2")

    def test_valid_graph_passthrough(self, tools):
        """Valid graph passes through unchanged."""
        graph = {
            "load": {
                "process_id": "load_collection",
                "arguments": {"id": "sentinel-2-l2a"},
            },
            "save": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "load"}, "format": "GTiff"},
                "result": True,
            },
        }
        errors = tools._validate_graph_schema(graph)
        assert errors == []

    def test_invalid_graph_returns_errors(self, tools):
        """Invalid graph returns validation errors."""
        graph = {
            "load": {
                "process_id": "load_collection",
                "arguments": {"id": "sentinel-2-l2a"},
            },
            # Missing result node
        }
        errors = tools._validate_graph_schema(graph)
        assert len(errors) > 0
        assert any("result" in e.lower() for e in errors)

    def test_broken_reference_detected(self, tools):
        """Graph with broken from_node reference is caught."""
        graph = {
            "load": {
                "process_id": "load_collection",
                "arguments": {"id": "sentinel-2-l2a"},
            },
            "save": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "nonexistent"}, "format": "GTiff"},
                "result": True,
            },
        }
        errors = tools._validate_graph_schema(graph)
        assert any("nonexistent" in e for e in errors)

    @pytest.mark.asyncio
    async def test_type_inference_failure_degrades_gracefully(self, monkeypatch):
        """If GraphValidator spec loading raises, openeo_raw_graph must fall
        back to structural-only validation instead of crashing.

        Regression: the except block logged via `logger`, which was never
        defined in this module, so the fallback path raised NameError.
        """
        import types as _types

        import openeo_ai.validation.graph_validator as gv
        from openeo_ai.tools.openeo_tools import create_openeo_tools

        def _raise(*_args, **_kwargs):
            raise RuntimeError("specs unavailable")

        monkeypatch.setattr(gv, "build_default_process_specs", _raise)

        handlers = create_openeo_tools(
            _types.SimpleNamespace(openeo_url="http://localhost:8080/openeo/1.2")
        )
        graph = {
            "load": {
                "process_id": "load_collection",
                "arguments": {"id": "sentinel-2-l2a"},
            },
            "save": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "load"}, "format": "GTiff"},
                "result": True,
            },
        }
        result = await handlers["openeo_raw_graph"]({"process_graph": graph})
        payload = json.loads(result["content"][0]["text"])
        assert payload.get("graph") == graph
        assert "error" not in payload


# ============================================================================
# Algorithm Template Tests
# ============================================================================

class TestAlgorithm:
    """Tests for openeo_algorithm pre-built templates."""

    BBOX = {"west": 77.0, "south": 28.0, "east": 77.5, "north": 28.5}
    DATES = ["2024-01-01", "2024-03-01"]
    PRE_EVENT = ["2024-01-01", "2024-06-01"]
    POST_EVENT = ["2024-07-01", "2024-12-01"]

    @pytest.fixture
    def tools(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools
        return OpenEOTools(openeo_url="http://localhost:8080/openeo/1.2")

    def test_snow_cover_graph_structure(self, tools):
        """Snow cover produces NDSI thresholding + NDVI exclusion."""
        graph = tools.build_algorithm_graph(
            "snow_cover", "sentinel-2-l2a", self.BBOX,
            temporal_extent=self.DATES,
        )
        # Must have NDSI computation
        assert "ndsi" in graph
        assert "ndsi_green" in graph
        assert "ndsi_swir" in graph
        # Must have thresholding
        assert "snow_mask" in graph
        assert graph["snow_mask"]["process_id"] == "gt"
        assert graph["snow_mask"]["arguments"]["y"] == 0.4
        # Must have NDVI vegetation exclusion
        assert "ndvi" in graph
        assert "veg_mask" in graph
        assert graph["veg_mask"]["process_id"] == "lt"
        # Must have boolean AND
        assert "snow_combined" in graph
        assert graph["snow_combined"]["process_id"] == "and"
        # Must have save with result=True
        assert graph["save"].get("result") is True

    def test_snow_cover_custom_thresholds(self, tools):
        """Snow cover accepts custom NDSI/NDVI thresholds."""
        graph = tools.build_algorithm_graph(
            "snow_cover", "sentinel-2-l2a", self.BBOX,
            temporal_extent=self.DATES,
            ndsi_threshold=0.6, ndvi_threshold=0.3,
        )
        assert graph["snow_mask"]["arguments"]["y"] == 0.6
        assert graph["veg_mask"]["arguments"]["y"] == 0.3

    def test_snow_cover_cloud_mask(self, tools):
        """Snow cover with cloud masking includes SCL nodes."""
        graph = tools.build_algorithm_graph(
            "snow_cover", "sentinel-2-l2a", self.BBOX,
            temporal_extent=self.DATES, cloud_mask=True,
        )
        assert "apply_mask" in graph
        assert "scl" in graph["load"]["arguments"]["bands"]

    def test_burn_severity_dual_temporal(self, tools):
        """Burn severity uses pre/post event periods."""
        graph = tools.build_algorithm_graph(
            "burn_severity", "sentinel-2-l2a", self.BBOX,
            pre_event=self.PRE_EVENT, post_event=self.POST_EVENT,
        )
        # Must have two load nodes with correct temporal extents
        assert "load1" in graph
        assert "load2" in graph
        assert graph["load1"]["arguments"]["temporal_extent"] == self.PRE_EVENT
        assert graph["load2"]["arguments"]["temporal_extent"] == self.POST_EVENT
        # Must have change detection via merge_cubes
        assert "change" in graph
        assert graph["change"]["process_id"] == "merge_cubes"
        assert graph["save"].get("result") is True

    def test_flood_extent_threshold(self, tools):
        """Flood extent applies water index thresholding."""
        graph = tools.build_algorithm_graph(
            "flood_extent", "sentinel-2-l2a", self.BBOX,
            temporal_extent=self.DATES,
        )
        # Must have water index (MNDWI by default)
        assert "mndwi" in graph
        # Must have flood thresholding
        assert "flood_mask" in graph
        assert graph["flood_mask"]["process_id"] == "gt"
        assert graph["flood_mask"]["arguments"]["y"] == 0.3
        assert graph["save"].get("result") is True

    def test_flood_extent_custom_index(self, tools):
        """Flood extent can use NDWI instead of MNDWI."""
        graph = tools.build_algorithm_graph(
            "flood_extent", "sentinel-2-l2a", self.BBOX,
            temporal_extent=self.DATES, water_index="ndwi",
        )
        assert "ndwi" in graph
        assert graph["flood_mask"]["arguments"]["x"]["from_node"] == "ndwi"

    def test_urban_growth_dual_temporal(self, tools):
        """Urban growth uses pre/post periods with NDBI."""
        graph = tools.build_algorithm_graph(
            "urban_growth", "sentinel-2-l2a", self.BBOX,
            pre_event=self.PRE_EVENT, post_event=self.POST_EVENT,
        )
        assert "load1" in graph
        assert "load2" in graph
        assert graph["load1"]["arguments"]["temporal_extent"] == self.PRE_EVENT
        assert graph["load2"]["arguments"]["temporal_extent"] == self.POST_EVENT
        assert "change" in graph
        assert graph["save"].get("result") is True

    def test_error_unknown_algorithm(self, tools):
        """Unknown algorithm raises ValueError."""
        with pytest.raises(ValueError, match="Unknown algorithm"):
            tools.build_algorithm_graph(
                "nonexistent", "sentinel-2-l2a", self.BBOX,
                temporal_extent=self.DATES,
            )

    def test_error_dual_temporal_missing_periods(self, tools):
        """Dual-temporal algorithm without pre/post raises ValueError."""
        with pytest.raises(ValueError, match="requires pre_event and post_event"):
            tools.build_algorithm_graph(
                "burn_severity", "sentinel-2-l2a", self.BBOX,
                temporal_extent=self.DATES,  # Wrong — needs pre/post
            )

    def test_error_single_temporal_missing_dates(self, tools):
        """Single-period algorithm without temporal_extent raises ValueError."""
        with pytest.raises(ValueError, match="requires temporal_extent"):
            tools.build_algorithm_graph(
                "snow_cover", "sentinel-2-l2a", self.BBOX,
                # Missing temporal_extent
            )
