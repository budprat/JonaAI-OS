# ABOUTME: GeoAI tools wrapping inference engine for Claude SDK integration.
# Provides segmentation, change detection, and canopy height tools.

"""
GeoAI tools for Claude SDK.

Provides tools for semantic segmentation, change detection,
and canopy height estimation using local models.
"""

import functools
import json
from pathlib import Path
from typing import Any, Dict

from ..geoai.model_registry import GeoAIModelUnavailable
from .path_security import (
    _ALLOWED_OUTPUT_DIR,
    PathSecurityError,
    validate_tool_input_path,
    validate_tool_output_path,
)

_INPUT_ARGS = ("input_path", "before_path", "after_path")

# The engine names its own default output next to the INPUT when output_path is None
# (inference.py segment/detect_change/estimate_canopy_height). Inputs may legitimately
# come from /tmp, /root/.openeo_jobs or the working directory, while outputs are only
# allowed under the result root, so that default wrote outside it and skipped
# validate_tool_output_path entirely. Supplying the same filename under the result root
# keeps the engine's naming and restores the invariant this decorator documents.
_DEFAULT_OUTPUT = {
    "geoai_segment": ("input_path", "_segmented"),
    "geoai_detect_change": ("before_path", "_change"),
    "geoai_estimate_canopy_height": ("input_path", "_height"),
}


def _refuse_unavailable_model(tool_name: str):
    """Answer a model that can't run with an error naming it and why, never with a guess.

    Paths come from the model's arguments, so inputs must pass the tool input check and an
    explicit output_path must stay under the result root, as for the export tools."""
    def wrap(handler):
        @functools.wraps(handler)
        async def run(args: Dict[str, Any]) -> Dict[str, Any]:
            try:
                args = dict(args)
                for key in _INPUT_ARGS:
                    if key in args:
                        args[key] = validate_tool_input_path(args[key])
                if args.get("output_path"):
                    args["output_path"] = validate_tool_output_path(args["output_path"])
                elif tool_name in _DEFAULT_OUTPUT:
                    source_key, suffix = _DEFAULT_OUTPUT[tool_name]
                    if args.get(source_key):
                        stem = Path(args[source_key]).stem
                        args["output_path"] = validate_tool_output_path(
                            str(_ALLOWED_OUTPUT_DIR / f"{stem}{suffix}.tif")
                        )
            except PathSecurityError as exc:
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": f"{tool_name}: {exc}", "code": "invalid_path", "tool": tool_name})}],
                    "isError": True,
                }
            try:
                return await handler(args)
            except GeoAIModelUnavailable as exc:
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": f"{tool_name} can't run: {exc}",
                        "code": "geoai_model_unavailable",
                        "tool": tool_name,
                        "model": exc.model,
                        "reason": exc.reason,
                    })}],
                    "isError": True,
                }
        return run
    return wrap


def create_geoai_tools(config) -> Dict[str, Any]:
    """
    Create GeoAI tools dict for Claude SDK.

    Tools are registered following the Claude SDK tool format:
    - Each tool returns {"content": [{"type": "text|image|visualization", ...}]}
    """
    from ..geoai.inference import GeoAIInference

    # Lazy initialization - will be created on first use
    _engine = None

    def get_engine():
        nonlocal _engine
        if _engine is None:
            _engine = GeoAIInference(models_path=config.geoai_models_path)
        return _engine

    @_refuse_unavailable_model("geoai_segment")
    async def _segment(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run semantic segmentation on satellite imagery.

        Args:
            input_path: Path to input GeoTIFF
            model: Model name (default: "segmentation_default")
            output_path: Optional output path

        Returns:
            Claude SDK format response with segmentation results
        """
        engine = get_engine()

        result = await engine.segment(
            input_path=args["input_path"],
            model=args.get("model", "segmentation_default"),
            output_path=args.get("output_path")
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result)
            }]
        }

    @_refuse_unavailable_model("geoai_detect_change")
    async def _detect_change(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect changes between two images.

        Args:
            before_path: Path to before image
            after_path: Path to after image
            model: Model name (default: "change_default")

        Returns:
            Claude SDK format response with change detection results
        """
        engine = get_engine()

        result = await engine.detect_change(
            before_path=args["before_path"],
            after_path=args["after_path"],
            model=args.get("model", "change_default"),
            output_path=args.get("output_path"),
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result)
            }]
        }

    @_refuse_unavailable_model("geoai_estimate_canopy_height")
    async def _estimate_canopy_height(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Estimate canopy height from RGB imagery.

        Args:
            input_path: Path to input GeoTIFF

        Returns:
            Claude SDK format response with height estimates
        """
        engine = get_engine()

        result = await engine.estimate_canopy_height(
            input_path=args["input_path"],
            output_path=args.get("output_path"),
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result)
            }]
        }

    return {
        "geoai_segment": _segment,
        "geoai_detect_change": _detect_change,
        "geoai_estimate_canopy_height": _estimate_canopy_height,
    }


# Standalone tool functions for testing
async def segment_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone segment tool."""
    from ..geoai.inference import GeoAIInference

    engine = GeoAIInference(models_path="openeo_ai/geoai/models/")
    result = await engine.segment(
        input_path=args["input_path"],
        model=args.get("model", "segmentation_default"),
        output_path=args.get("output_path")
    )

    return {
        "content": [{
            "type": "text",
            "text": json.dumps(result)
        }]
    }


async def detect_change_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone change detection tool."""
    from ..geoai.inference import GeoAIInference

    engine = GeoAIInference(models_path="openeo_ai/geoai/models/")
    result = await engine.detect_change(
        before_path=args["before_path"],
        after_path=args["after_path"],
        model=args.get("model", "change_default")
    )

    return {
        "content": [{
            "type": "text",
            "text": json.dumps(result)
        }]
    }
