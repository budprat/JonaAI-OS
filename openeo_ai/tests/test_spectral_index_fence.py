# ABOUTME: Truth-debt fence — spectral_index_calculate refuses raw-DN index math and
# ABOUTME: redirects to the verified reflectance paths; the search catalog stays live.
import asyncio

from openeo_ai.tools.spectral_index_tools import (
    SPECTRAL_INDEX_TOOL_DEFINITIONS,
    create_spectral_index_tools,
)


def _call(tool, args):
    return asyncio.run(create_spectral_index_tools()[tool](args))


def test_calculate_refuses_kernel_index_with_redirect():
    out = _call("spectral_index_calculate",
                {"file_path": "/tmp/does-not-exist.tif", "index": "NDVI"})
    text = out["content"][0]["text"]
    assert "raw digital numbers" in text
    assert "agentic_decision_query" in text
    # The refusal happens before any file IO — a nonexistent path never errors.
    assert "Error computing" not in text


def test_calculate_refuses_non_kernel_index_as_unverified():
    out = _call("spectral_index_calculate",
                {"file_path": "/tmp/does-not-exist.tif", "index": "SAVI"})
    text = out["content"][0]["text"]
    assert "SAVI" in text
    assert "verified" in text


def test_calculate_refuses_dnbr_too():
    # The catalog's dNBR computed single-date NBR — the fence retires that lie.
    out = _call("spectral_index_calculate",
                {"file_path": "/tmp/does-not-exist.tif", "index": "dNBR"})
    text = out["content"][0]["text"]
    assert "dNBR" in text.upper() or "DNBR" in text.upper()
    assert "verified" in text


def test_calculate_writes_no_output_file(tmp_path):
    target = tmp_path / "ndvi_out.tif"
    _call("spectral_index_calculate",
          {"file_path": "/tmp/does-not-exist.tif", "index": "NDVI",
           "output_path": str(target)})
    assert not target.exists()


def test_search_catalog_stays_live():
    out = _call("spectral_index_search", {"query": "vegetation health"})
    assert "NDVI" in out["content"][0]["text"]


def test_calculate_definition_declares_the_fence():
    calc = next(d for d in SPECTRAL_INDEX_TOOL_DEFINITIONS
                if d["name"] == "spectral_index_calculate")
    assert "refuses" in calc["description"].lower()
    assert "agentic_decision_query" in calc["description"]
