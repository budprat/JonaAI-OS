# ABOUTME: Regression tests for defects found in the 2026-11-10 audit: a finished public-API
# ABOUTME: job's status and HTML report, triggering a schedule, and limit settings.
import asyncio
from datetime import datetime
from types import SimpleNamespace

from openeo_ai.api.jobs import JobManager
from openeo_ai.api.schemas import JobStatusResponse


def test_a_completed_job_status_validates():
    """A finished job stores the agent's text as its summary; the status response must accept it."""
    now = datetime.utcnow()
    job = {
        "job_id": "j1", "status": "completed", "progress": 100.0, "current_step": None,
        "steps_completed": 3, "steps_total": 3, "created_at": now, "started_at": now,
        "completed_at": now, "error": None, "result_summary": "NDVI rose 12% over the period.",
    }
    response = JobStatusResponse(**JobManager.to_status_response(job))
    assert response.result_summary == "NDVI rose 12% over the period."


def test_triggering_a_schedule_with_locations_starts_a_batch(tmp_path, monkeypatch):
    from openeo_ai.batch import routes
    from openeo_ai.batch.batch_store import BatchStore
    from openeo_ai.batch.models import LocationInput, ScheduleCreate, ScheduleFrequency, init_batch_db
    from openeo_ai.batch.models import WorkflowTemplateCreate
    from openeo_ai.batch.scheduler import ScheduleStore
    from openeo_ai.batch.template_store import TemplateStore

    db = str(tmp_path / "batch.db")
    init_batch_db(db)
    template = TemplateStore(db_path=db).create_template(  # creates the batch schema too
        WorkflowTemplateCreate(name="NDVI", process_graph={"save": {"process_id": "save_result", "arguments": {}, "result": True}}),
        user_id="user-1",
    )
    schedules, batches = ScheduleStore(db_path=db), BatchStore(db_path=db)
    started = []
    monkeypatch.setattr(routes, "_get_schedule_store", lambda: schedules)
    monkeypatch.setattr(routes, "_get_batch_store", lambda: batches)
    monkeypatch.setattr(routes, "_get_executor", lambda: SimpleNamespace(start_batch_async=started.append))

    sched = schedules.create_schedule(ScheduleCreate(
        template_id=template.id, name="weekly", frequency=ScheduleFrequency.WEEKLY,
        locations=[LocationInput(name="Farm", spatial_extent={"west": 1, "south": 2, "east": 3, "north": 4})],
    ), "user-1")
    request = SimpleNamespace(state=SimpleNamespace(user_id="user-1"))

    result = asyncio.run(routes.trigger_schedule_now(sched.id, request))

    assert result["triggered"] is True
    assert started == [result["batch_job_id"]]


def test_limit_settings_that_dont_parse_keep_the_default(monkeypatch):
    """CLAUDE.md: a limit that doesn't parse keeps the default rather than failing startup."""
    from openeo_app.core.limits import positive_int_setting
    from openeo_app.registers.jobs import _max_concurrent_jobs

    for bad in ("5x", "", "0", "-3"):
        monkeypatch.setenv("MAX_STAC_ITEMS", bad)
        assert positive_int_setting("MAX_STAC_ITEMS", 5) == 5
    monkeypatch.setenv("MAX_STAC_ITEMS", "8")
    assert positive_int_setting("MAX_STAC_ITEMS", 5) == 8

    monkeypatch.setenv("OPENEO_MAX_CONCURRENT_JOBS", "two")
    assert _max_concurrent_jobs() == 4
    monkeypatch.setenv("OPENEO_MAX_CONCURRENT_JOBS", "0")
    assert _max_concurrent_jobs() == 1  # unchanged: zero still means one at a time


def test_the_html_report_escapes_the_query_summary_and_graph():
    from openeo_ai.api.router import _generate_html_report

    html = _generate_html_report({
        "job_id": "j1", "query": "<img src=x onerror=alert(1)>", "created_at": datetime(2026, 11, 10),
        "result_summary": "<script>steal()</script>", "quality_metrics": {"<b>": "<i>"},
        "process_graph": {"n": {"process_id": "</pre><script>x()</script>"}},
    })

    assert "<script>" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;steal()&lt;/script&gt;" in html


def test_openeo_url_defaults_match_the_port_the_app_listens_on(monkeypatch):
    # The app serves /openeo on 8080 (deploy unit, start-local.sh, .env.example); two defaults said 8000.
    monkeypatch.delenv("OPENEO_URL", raising=False)
    from openeo_ai.batch.executor import BatchExecutor
    from openeo_ai.sdk.client import OpenEOAIConfig

    assert OpenEOAIConfig().openeo_url == "http://localhost:8080/openeo/1.1.0"
    assert "localhost:8000" not in __import__("inspect").getsource(BatchExecutor)
