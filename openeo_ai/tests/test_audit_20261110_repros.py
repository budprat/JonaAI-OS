# ABOUTME: Reproductions for findings from the 2026-11-10 audit, run against stubs and temp
# ABOUTME: databases only. Unfixed findings are strict xfails that assert the intended behaviour.
import ast
import asyncio
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openeo_ai.intelligence import routes as intelligence_routes
from openeo_ai.intelligence.worker import EarthIntelligenceWorker


class _OtherUsersQueue:
    """A repository whose due watches all belong to another user."""

    def enqueue_due_watches(self, limit=25, user_id=None):
        return [{"id": "obs-1", "user_id": "alice", "watch_id": "w-1", "asset_id": "farm-1"}]

    def claim_next_observation(self, **kwargs):
        return None

    def claim_pending_deliveries(self, limit=10, user_id=None):
        return []


def test_worker_status_shows_no_other_users_records():
    worker = EarthIntelligenceWorker(repository=_OtherUsersQueue(), worker_id="w")
    asyncio.run(worker.tick())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(earth_intelligence_worker=worker)))

    body = asyncio.run(intelligence_routes.worker_status(request, payload={"sub": "bob"}))

    assert "alice" not in repr(body)
    assert body["last_result"]["metrics"]["queued_due_count"] == 1
    assert body["last_result"]["failed"] is False


def test_worker_status_hides_a_failed_ticks_error_text():
    worker = EarthIntelligenceWorker(repository=_OtherUsersQueue(), worker_id="w")
    worker._last_result = {"error": "observation obs-1 of alice failed", "at": "t"}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(earth_intelligence_worker=worker)))

    body = asyncio.run(intelligence_routes.worker_status(request, payload={"sub": "bob"}))

    assert body["last_result"] == {"at": "t", "metrics": None, "failed": True}


# ── Synchronous OpenEO jobs take a job slot and time out (fixed) ───────


def _all_job_slots():
    from openeo_app.registers import jobs

    held = 0
    while jobs._JOB_SLOTS.acquire(blocking=False):
        held += 1
    return jobs, held


def _sync_register(jobs, execute):
    stub = SimpleNamespace(
        executor=SimpleNamespace(execute=execute),
        _get_output_format=lambda graph: "JSON",
        _create_response=lambda result, fmt: result,
    )
    stub._execute_sync_in_slot = lambda graph: jobs.ExecutableJobsRegister._execute_sync_in_slot(stub, graph)
    return stub


_BODY = SimpleNamespace(process=SimpleNamespace(process_graph={"n": {"process_id": "x", "arguments": {}}}))


def test_a_sync_job_waits_for_a_job_slot_and_is_refused_if_none_frees(monkeypatch):
    from fastapi import HTTPException

    jobs, held = _all_job_slots()
    monkeypatch.setattr(jobs, "_SYNC_JOB_TIMEOUT_SECONDS", 0.3)
    ran = threading.Event()
    stub = _sync_register(jobs, lambda **kw: ran.set())
    try:
        with pytest.raises(HTTPException) as refused:
            jobs.ExecutableJobsRegister.process_sync_job(stub, _BODY, SimpleNamespace(user_id="u"))
        assert refused.value.status_code == 503
        assert not ran.is_set(), "the sync job ran while every job slot was taken"
    finally:
        for _ in range(held):
            jobs._JOB_SLOTS.release()


def test_a_sync_job_runs_once_a_slot_frees(monkeypatch):
    jobs, held = _all_job_slots()
    monkeypatch.setattr(jobs, "_SYNC_JOB_TIMEOUT_SECONDS", 5)
    stub = _sync_register(jobs, lambda **kw: "result")
    threading.Timer(0.2, jobs._JOB_SLOTS.release).start()  # another job finishes
    try:
        assert jobs.ExecutableJobsRegister.process_sync_job(stub, _BODY, SimpleNamespace(user_id="u")) == "result"
    finally:
        for _ in range(held - 1):
            jobs._JOB_SLOTS.release()


def test_a_sync_job_times_out_and_keeps_its_slot_until_the_work_ends(monkeypatch):
    # The executor's own SIGALRM timeout can't be set off the main thread, where FastAPI runs
    # this route, so before the fix a sync job ran as long as it liked.
    from fastapi import HTTPException

    from openeo_app.registers import jobs

    monkeypatch.setattr(jobs, "_SYNC_JOB_TIMEOUT_SECONDS", 0.5)
    done = threading.Event()
    stub = _sync_register(jobs, lambda **kw: time.sleep(1.5) or done.set())
    free_before = jobs._JOB_SLOTS._value
    started = time.monotonic()
    with pytest.raises(HTTPException) as timed_out:
        jobs.ExecutableJobsRegister.process_sync_job(stub, _BODY, SimpleNamespace(user_id="u"))
    assert time.monotonic() - started < 1.2
    assert "timed out after 0.5s" in str(timed_out.value.detail)
    assert jobs._JOB_SLOTS._value == free_before - 1  # still computing, so still holding memory
    assert done.wait(3)
    time.sleep(0.05)
    assert jobs._JOB_SLOTS._value == free_before


# ── Public-API routes that start analyses enforce the tier's concurrent limit (fixed) ──


def _batch(monkeypatch, locations, active):
    from openeo_ai.api import router as api_router
    from openeo_ai.api.auth import AuthenticatedClient
    from openeo_ai.api.schemas import BatchRequest
    from openeo_ai.storage import job_archive

    started = []

    async def fake_analysis(job, state):
        started.append(job["job_id"])
        await asyncio.sleep(3600)

    monkeypatch.setattr(api_router, "_execute_analysis", fake_analysis)
    monkeypatch.setattr(api_router, "check_rate_limit", lambda *a: (True, {"concurrent_active": active}))
    monkeypatch.setattr(api_router, "add_rate_limit_headers", lambda *a: None)
    monkeypatch.setattr(api_router, "increment_concurrent", lambda *a: None)
    counter = iter(range(1000))
    monkeypatch.setattr(api_router.job_manager, "create_job", lambda **kw: {"job_id": f"j{next(counter)}"})
    monkeypatch.setattr(job_archive, "create_batch", lambda batch: None)
    body = BatchRequest(
        query="NDVI for each field",
        locations=[{"id": f"loc{i}", "bbox": {"west": i, "south": 0, "east": i + 0.1, "north": 0.1}}
                   for i in range(locations)],
    )
    client = AuthenticatedClient(client_id="c", user_id="u", tier="free", scopes=["batch"])

    async def submit():
        try:
            return await api_router.submit_batch(body, SimpleNamespace(app=SimpleNamespace(state=None)),
                                                  SimpleNamespace(headers={}), client)
        finally:
            await asyncio.sleep(0.05)
            for task in asyncio.all_tasks() - {asyncio.current_task()}:
                task.cancel()

    return asyncio.run(submit()), started


def test_a_batch_over_the_tiers_concurrent_limit_is_refused_before_starting_anything(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _batch(monkeypatch, locations=50, active=0)  # free tier allows 2
    assert refused.value.status_code == 429
    assert "would start 50 more" in refused.value.detail["detail"]


def test_a_batch_within_the_limit_starts_each_location(monkeypatch):
    response, started = _batch(monkeypatch, locations=2, active=0)
    assert response["total_locations"] == 2 and len(started) == 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _batch(monkeypatch, locations=2, active=1)  # one already running: 1 + 2 > 2


def test_every_route_that_starts_an_analysis_checks_the_limit_first():
    # /analyze/sync and /templates/{id}/execute used to skip it.
    source = (Path(__file__).resolve().parents[1] / "api/router.py").read_text()
    routes = [n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef)]
    starting = []
    for route in routes:
        calls = [c.func.id for c in ast.walk(route) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
        if "increment_concurrent" in calls:
            starting.append(route.name)
            assert "_check_concurrent_limit" in calls, route.name
    assert {"submit_analysis", "submit_analysis_sync", "execute_template", "submit_batch"} <= set(starting)


# ── Tables the migrations create without row-level security ────────────

_VERSIONS = Path(__file__).resolve().parents[2] / "openeo_app/psql/alembic/versions"


def _tables_by_rls():
    created, protected = set(), set()
    for path in _VERSIONS.glob("*.py"):
        text = path.read_text()
        created |= set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? (\w+)", text))
        created |= set(re.findall(r"op\.create_table\(\s*['\"](\w+)", text))
        protected |= set(re.findall(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY", text))
        # Loops such as `for table in USER_TABLES: ... ALTER TABLE {table} ENABLE ...`
        for name in re.findall(r"for table in (\w+):\n\s+op\.execute\(f\"ALTER TABLE \{table\} ENABLE", text):
            for node in ast.parse(text).body:
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name:
                    protected |= set(ast.literal_eval(node.value))
    return created, protected


def test_the_rls_scan_sees_the_known_protected_tables():
    created, protected = _tables_by_rls()
    assert {"saved_results", "observations", "result_copies", "api_keys"} <= protected
    assert {"jobs", "batch_runs", "learned_trajectories"} <= created


def test_every_migrated_table_has_row_level_security():
    created, protected = _tables_by_rls()
    from openeo_ai.storage.models import Base

    unmigrated = {t for t in Base.metadata.tables if t not in created}
    assert sorted(created - protected) == [] and "plan_trajectories" not in unmigrated, (
        f"no RLS: {sorted(created - protected)}; never migrated: {sorted(unmigrated)}")


# ── The turn's Langfuse root span ─────────────────────────────────────


def _span_names_by_method():
    source = (Path(__file__).resolve().parents[1] / "sdk/claude_sdk_bridge.py").read_text()
    bridge = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "ClaudeSDKBridge")
    names = {}
    for node in bridge.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and getattr(deco.func, "id", "") == "_lf_observe":
                    names[node.name] = next(k.value.value for k in deco.keywords if k.arg == "name")
    return names


def test_the_chat_root_span_wraps_query_not_the_budget_tag():
    # _query reads the trace id from this span; on the budget tag there was none, so the
    # `trace` event never fired and the judge was skipped (tid=MISSING in the journal).
    names = _span_names_by_method()
    assert names.get("query") == "jonaai.chat.query"
    assert "_record_llm_budget" not in names


# ── GeoAI tools without a model: they refuse (fixed 2026-11-10) ──
# These tools used to fall back to StubModel and report random numbers as results. Refusing
# is the intended behaviour: don't turn these back into a fallback.


def _uniform_scene(path):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.open(path, "w", driver="GTiff", height=64, width=64, count=3, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(10, 50, 0.001, 0.001)) as dst:
        dst.write(np.ones((3, 64, 64), dtype="float32"))
    return path


@pytest.mark.parametrize("tool,model,args", [
    ("geoai_segment", "segmentation_default", {"input_path": "scene.tif"}),
    ("geoai_detect_change", "change_default", {"before_path": "scene.tif", "after_path": "scene.tif"}),
    ("geoai_estimate_canopy_height", "canopy_height_default", {"input_path": "scene.tif"}),
])
def test_geoai_tools_without_a_model_refuse_rather_than_make_up_results(tmp_path, monkeypatch, tool, model, args):
    import json

    from openeo_ai.sdk.client import OpenEOAIConfig
    from openeo_ai.tools.geoai_tools import create_geoai_tools

    # As in production: GEOAI_MODELS_PATH defaults to a relative path, and the service runs
    # from /var/lib/jonaai, where it doesn't exist, so the default models have no file.
    monkeypatch.delenv("GEOAI_MODELS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    _uniform_scene(tmp_path / "scene.tif")

    out = asyncio.run(create_geoai_tools(OpenEOAIConfig())[tool](args))
    body = json.loads(out["content"][0]["text"])

    assert out["isError"] is True
    assert body["code"] == "geoai_model_unavailable"
    assert (body["tool"], body["model"], body["reason"]) == (tool, model, "no_model_file")
    assert model in body["error"] and "statistics" not in body
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scene.tif"]  # no made-up output file


@pytest.mark.parametrize("args", [
    {"input_path": "scene.tif", "output_path": "/var/lib/jonaai/elsewhere.tif"},
    {"input_path": "/etc/hostname"},
])
def test_geoai_tools_refuse_paths_outside_the_allowed_folders(tmp_path, monkeypatch, args):
    import json

    from openeo_ai.sdk.client import OpenEOAIConfig
    from openeo_ai.tools.geoai_tools import create_geoai_tools

    monkeypatch.chdir(tmp_path)
    _uniform_scene(tmp_path / "scene.tif")
    out = asyncio.run(create_geoai_tools(OpenEOAIConfig())["geoai_segment"](args))

    assert out["isError"] is True
    assert json.loads(out["content"][0]["text"])["code"] == "invalid_path"


def test_a_model_with_a_file_but_no_ml_framework_refuses(tmp_path, monkeypatch):
    from openeo_ai.geoai.model_registry import GeoAIModelUnavailable, ModelInfo, ModelRegistry

    (tmp_path / "model.onnx").write_bytes(b"")
    registry = ModelRegistry()
    registry._models["seg"] = ModelInfo(name="seg", task="segmentation", version="1",
                                        input_bands=["red"], path=str(tmp_path))
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # import raises ImportError, as on this host

    with pytest.raises(GeoAIModelUnavailable) as refused:
        registry.load_model("seg")
    assert (refused.value.model, refused.value.reason) == ("seg", "missing_framework")
    assert "seg" in str(refused.value)


def test_a_configured_path_without_a_model_file_refuses(tmp_path):
    from openeo_ai.geoai.model_registry import GeoAIModelUnavailable, ModelInfo, ModelRegistry

    registry = ModelRegistry()
    registry._models["seg"] = ModelInfo(name="seg", task="segmentation", version="1",
                                        input_bands=["red"], path=str(tmp_path))
    with pytest.raises(GeoAIModelUnavailable) as refused:
        registry.load_model("seg")
    assert refused.value.reason == "no_model_file"


def test_only_an_explicit_argument_allows_the_random_stub(monkeypatch):
    from openeo_ai.geoai.inference import GeoAIInference
    from openeo_ai.geoai.model_registry import GeoAIModelUnavailable, ModelRegistry, StubModel

    for name in ("GEOAI_ALLOW_STUB", "GEOAI_STUB", "OPENEO_DEV_MODE"):
        monkeypatch.setenv(name, "true")  # no setting can turn the stub back on
    with pytest.raises(GeoAIModelUnavailable):
        ModelRegistry().load_model("segmentation_default")
    with pytest.raises(GeoAIModelUnavailable):
        GeoAIInference().registry.load_model("segmentation_default")

    assert isinstance(ModelRegistry(allow_stub=True).load_model("segmentation_default"), StubModel)
    assert isinstance(GeoAIInference(allow_stub=True).registry.load_model("change_default"), StubModel)


# ── Users can re-queue work a worker is still running ──────────────────


def test_an_api_caller_cannot_requeue_running_work():
    # Fixed: lifecycle.USER_SOURCES / check_user_move. The worker's retries keep these moves.
    from openeo_ai.lifecycle import DELIVERY, OBSERVATION, USER_SETTABLE, IllegalTransition, check_user_move

    def caller_may(lifecycle, current, target):
        if target not in USER_SETTABLE[lifecycle.name]:
            return False
        try:
            check_user_move(lifecycle, current, target)
            return True
        except IllegalTransition:
            return False

    assert caller_may(OBSERVATION, "failed", "queued") and caller_may(OBSERVATION, "cancelled", "queued")
    assert caller_may(DELIVERY, "failed", "pending")
    assert OBSERVATION.can("running", "queued") and DELIVERY.can("sending", "pending")  # worker retries

    open_moves = [m for m in [(OBSERVATION, "running", "queued"), (DELIVERY, "sending", "pending")]
                  if caller_may(*m)]
    assert not open_moves, [f"{lc.name}: {a}->{b}" for lc, a, b in open_moves]
