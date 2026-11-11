# ABOUTME: Tests persistence + ownership gating of the saved-result visualization
# ABOUTME: bundle (charts/tables/process-graph/report) used to repopulate tabs on load.

import json

from openeo_ai.storage import job_archive


SAMPLE_BUNDLE = {
    "visualizations": [
        {
            "id": "c1",
            "type": "chart",
            "title": "NDVI Time Series",
            "data": {"chartType": "line", "series": [{"name": "NDVI", "values": [0.3, 0.5]}]},
        },
        {
            "id": "t1",
            "type": "table",
            "title": "Band Stats",
            "data": {"headers": ["Band", "Mean"], "rows": [["red", 0.4]]},
        },
    ],
    "reportSections": [
        {"id": "r1", "type": "interpretation", "title": "Interpretation", "content": "Healthy vegetation"},
    ],
}


# ── Pure read/write round-trip ──

def test_write_read_visualizations_roundtrip(tmp_path):
    result_path = tmp_path / "result.tif"
    result_path.write_bytes(b"geotiff")

    job_archive.write_visualizations(str(result_path), SAMPLE_BUNDLE)

    # File lands next to the result, mirroring metadata.json
    bundle_file = tmp_path / "visualizations.json"
    assert bundle_file.exists()
    assert json.loads(bundle_file.read_text()) == SAMPLE_BUNDLE

    assert job_archive.read_visualizations(str(result_path)) == SAMPLE_BUNDLE


def test_read_visualizations_missing_returns_none(tmp_path):
    result_path = tmp_path / "result.tif"
    result_path.write_bytes(b"geotiff")
    assert job_archive.read_visualizations(str(result_path)) is None


def test_read_visualizations_corrupt_returns_none(tmp_path):
    result_path = tmp_path / "result.tif"
    result_path.write_bytes(b"geotiff")
    (tmp_path / "visualizations.json").write_text("{ not valid json")
    assert job_archive.read_visualizations(str(result_path)) is None


def test_write_visualizations_is_atomic_overwrite(tmp_path):
    result_path = tmp_path / "result.tif"
    result_path.write_bytes(b"geotiff")
    job_archive.write_visualizations(str(result_path), {"visualizations": [], "reportSections": []})
    job_archive.write_visualizations(str(result_path), SAMPLE_BUNDLE)
    assert job_archive.read_visualizations(str(result_path)) == SAMPLE_BUNDLE
    # No leftover temp file
    assert not list(tmp_path.glob("visualizations.json.*"))


# ── Ownership-gated save/get (get_job enforces user_id) ──

def test_save_visualizations_denied_when_no_access(tmp_path, monkeypatch):
    monkeypatch.setattr(job_archive, "get_job", lambda save_id, user_id=None: None)
    ok = job_archive.save_visualizations("abcd1234", SAMPLE_BUNDLE, user_id="user-x")
    assert ok is False


def test_save_and_get_visualizations_when_owner(tmp_path, monkeypatch):
    result_path = tmp_path / "result.tif"
    result_path.write_bytes(b"geotiff")
    job = {"save_id": "abcd1234", "result_path": str(result_path)}
    monkeypatch.setattr(job_archive, "get_job", lambda save_id, user_id=None: job)

    assert job_archive.save_visualizations("abcd1234", SAMPLE_BUNDLE, user_id="owner") is True
    assert job_archive.get_visualizations("abcd1234", user_id="owner") == SAMPLE_BUNDLE


def test_get_visualizations_denied_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(job_archive, "get_job", lambda save_id, user_id=None: None)
    assert job_archive.get_visualizations("abcd1234", user_id="intruder") is None
