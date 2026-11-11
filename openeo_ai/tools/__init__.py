# ABOUTME: Tools module providing Claude SDK tool functions.
# Exports OpenEO, job, validation, GeoAI, visualization, UDF, notebook, and result analysis tools.

"""Tools module for OpenEO AI Assistant."""

from .openeo_tools import OpenEOTools, create_openeo_tools, list_collections_tool
from .job_tools import JobTools, create_job_tools
from .validation_tools import ValidationTools, validate_graph_tool, validate_scf_tool
from .viz_tools import create_viz_tools
from .geoai_tools import create_geoai_tools
from .udf_tools import (
    create_udf_tools,
    udf_register_tool,
    udf_execute_tool,
    udf_list_tool,
    udf_validate_tool,
    UDF_TOOL_DEFINITIONS,
)
from .notebook_tools import (
    NotebookGenerator,
    create_notebook_tools,
    export_notebook_tool,
    NOTEBOOK_TOOL_DEFINITIONS,
)
from .result_analysis_tools import (
    create_result_analysis_tools,
    RESULT_ANALYSIS_TOOL_DEFINITIONS,
)
from .spectral_index_tools import (
    create_spectral_index_tools,
    SPECTRAL_INDEX_TOOL_DEFINITIONS,
)
from .temporal_analysis_tools import (
    create_temporal_analysis_tools,
    TEMPORAL_ANALYSIS_TOOL_DEFINITIONS,
)
from .export_tools import (
    create_export_tools,
    EXPORT_TOOL_DEFINITIONS,
)
from .comparison_tools import (
    create_comparison_tools,
    COMPARISON_TOOL_DEFINITIONS,
)
from .discovery_tools import (
    create_discovery_tools,
    DISCOVERY_TOOL_DEFINITIONS,
)

__all__ = [
    "OpenEOTools",
    "create_openeo_tools",
    "list_collections_tool",
    "JobTools",
    "create_job_tools",
    "ValidationTools",
    "validate_graph_tool",
    "create_viz_tools",
    "create_geoai_tools",
    "create_udf_tools",
    "udf_register_tool",
    "udf_execute_tool",
    "udf_list_tool",
    "udf_validate_tool",
    "UDF_TOOL_DEFINITIONS",
    "NotebookGenerator",
    "create_notebook_tools",
    "export_notebook_tool",
    "NOTEBOOK_TOOL_DEFINITIONS",
    "create_result_analysis_tools",
    "RESULT_ANALYSIS_TOOL_DEFINITIONS",
    "create_spectral_index_tools",
    "SPECTRAL_INDEX_TOOL_DEFINITIONS",
    "create_temporal_analysis_tools",
    "TEMPORAL_ANALYSIS_TOOL_DEFINITIONS",
    "create_export_tools",
    "EXPORT_TOOL_DEFINITIONS",
    "create_comparison_tools",
    "COMPARISON_TOOL_DEFINITIONS",
    "create_discovery_tools",
    "DISCOVERY_TOOL_DEFINITIONS",
]
