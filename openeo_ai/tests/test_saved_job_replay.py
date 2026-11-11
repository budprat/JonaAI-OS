# ABOUTME: Replay marking on saved-job WS events — replayed visualization events carry
# ABOUTME: replay=True so the frontend keeps them out of the running turn's flush bundle.
import asyncio

# web_interface is imported inside each test (repo convention): a module-level
# import runs at collection time and its global state breaks unrelated tests.


class _Capture:
    def __init__(self):
        self.events = []

    async def send_json(self, obj):
        self.events.append(obj)


def _saved_job(tmp_path):
    tif = tmp_path / "result.tif"
    tif.write_bytes(b"")
    return {
        "result_path": str(tif),
        "title": "NDVI Kings County",
        "colormap": "RdYlGn",
        "bounds": [[36.0, -119.9], [36.2, -119.7]],
        "vmin": 0.0,
        "vmax": 1.0,
    }


def test_send_saved_job_marks_every_replayed_visualization(monkeypatch, tmp_path):
    from openeo_ai.storage import result_access
    monkeypatch.setattr(result_access, "can_read_saved_result", lambda save_id, user_id: True)
    import openeo_ai.storage.job_archive as job_archive
    from openeo_ai.web_interface import _send_saved_job

    # Without the replay marker, a mid-run "Load on Map" makes these events
    # indistinguishable from live ones and they cross-contaminate the running
    # turn's persisted bundle (both directions).
    monkeypatch.setattr(job_archive, "get_job", lambda sid: _saved_job(tmp_path))
    monkeypatch.setattr(job_archive, "read_visualizations", lambda p: {
        "visualizations": [
            {"type": "chart", "title": "NDVI trend", "data": {"chartType": "line"}},
            {"type": "map", "title": "bundle maps are skipped", "data": {}},
        ],
        "reportSections": [
            {"type": "report", "title": "Analysis Report", "content": "…"},
        ],
    })

    ws = _Capture()
    assert asyncio.run(_send_saved_job(ws, "abcd1234", "user-1")) is True

    viz_events = [e for e in ws.events if e["type"] == "visualization"]
    # The GeoTIFF map plus the replayed bundle chart (bundle maps never replay).
    assert len(viz_events) == 2
    assert all(e.get("replay") is True for e in viz_events)
    # Replayed report sections stay non-live.
    rs = next(e for e in ws.events if e["type"] == "report_sections")
    assert "live" not in rs


def test_send_saved_job_missing_save_id_sends_error(monkeypatch):
    from openeo_ai.storage import result_access
    monkeypatch.setattr(result_access, "can_read_saved_result", lambda save_id, user_id: True)
    import openeo_ai.storage.job_archive as job_archive
    from openeo_ai.web_interface import _send_saved_job

    monkeypatch.setattr(job_archive, "get_job", lambda sid: None)

    ws = _Capture()
    assert asyncio.run(_send_saved_job(ws, "nope0000", "user-1")) is False
    assert ws.events[0]["type"] == "error"
