# ABOUTME: A GeoAI call with no output_path used to write next to its input, which may be
# ABOUTME: /tmp or the cwd; outputs belong under the result root. Covers all three tools.
from pathlib import Path

import pytest

from openeo_ai.tools.geoai_tools import _DEFAULT_OUTPUT, _refuse_unavailable_model
from openeo_ai.tools.path_security import _ALLOWED_INPUT_DIRS, _ALLOWED_OUTPUT_DIR


def test_the_roots_really_do_differ():
    """If inputs were restricted to the result root, none of this would matter."""
    outside = [d for d in _ALLOWED_INPUT_DIRS if d != _ALLOWED_OUTPUT_DIR]
    assert outside, "inputs are confined to the result root, so this test is moot"
    assert Path("/tmp") in _ALLOWED_INPUT_DIRS
    assert _ALLOWED_OUTPUT_DIR != Path("/tmp")


@pytest.mark.parametrize("tool_name", sorted(_DEFAULT_OUTPUT))
def test_an_omitted_output_path_is_placed_under_the_result_root(tool_name, tmp_path, monkeypatch):
    source_key, suffix = _DEFAULT_OUTPUT[tool_name]
    scene = Path("/tmp") / "audit_pass7_scene.tif"
    scene.write_bytes(b"\x00")          # only the path is validated, not the content
    try:
        captured = {}

        @_refuse_unavailable_model(tool_name)
        async def handler(args):
            captured.update(args)
            return {"content": [], "isError": False}

        args = {source_key: str(scene)}
        if tool_name == "geoai_detect_change":
            args["after_path"] = str(scene)

        import asyncio
        result = asyncio.run(handler(args))
        assert result.get("isError") is not True, result

        out = Path(captured["output_path"])
        assert out.is_absolute()
        out.relative_to(_ALLOWED_OUTPUT_DIR)          # raises if it escaped
        assert out.name == f"{scene.stem}{suffix}.tif"
        assert not str(out).startswith("/tmp/"), "output still lands beside the input"
    finally:
        scene.unlink(missing_ok=True)


def test_an_explicit_output_path_outside_the_result_root_is_still_refused(tmp_path):
    @_refuse_unavailable_model("geoai_segment")
    async def handler(args):
        return {"content": [], "isError": False}

    scene = Path("/tmp") / "audit_pass7_scene2.tif"
    scene.write_bytes(b"\x00")
    try:
        import asyncio, json
        out = asyncio.run(handler({"input_path": str(scene),
                                   "output_path": "/tmp/escape.tif"}))
        assert out["isError"] is True
        assert json.loads(out["content"][0]["text"])["code"] == "invalid_path"
    finally:
        scene.unlink(missing_ok=True)
