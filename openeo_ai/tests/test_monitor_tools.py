# ABOUTME: Tests for the spine/monitoring agent tools — user-scoped create/manage/
# ABOUTME: triage of assets, watches, signals, deliveries, and evidence chains.

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from openeo_ai.intelligence import repository as repo_mod
from openeo_ai.intelligence.repository import EarthIntelligenceRepository
from openeo_ai.sdk.user_context import set_current_user_id, get_current_user_id
from openeo_ai.storage import job_archive
from openeo_ai.tools.monitor_tools import create_monitor_tools


@pytest.fixture
def repo():
    # In-memory intelligence DB (SingletonThreadPool default shares one conn in
    # this test thread — no TestClient here, so no cross-thread concern).
    job_archive._engine = create_engine("sqlite:///:memory:")
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)
    job_archive._tables_ready = False
    repo_mod._tables_ready = False
    return EarthIntelligenceRepository()


@pytest.fixture
def tools():
    return create_monitor_tools(config=None)


@pytest.fixture(autouse=True)
def _reset_user():
    set_current_user_id(None)
    yield
    from openeo_ai.sdk import user_context
    user_context._current_user_ref.set(None)


async def _call(tools, tool, **args):
    result = await tools[tool](args)
    return json.loads(result["content"][0]["text"])


# ── user_context ──────────────────────────────────────────────────────

def test_context_var_isolates_value():
    set_current_user_id("user-x")
    assert get_current_user_id() == "user-x"
    set_current_user_id(None)
    assert get_current_user_id() is None


def test_bind_user_ref_reflects_mutation():
    # The production mechanism: the reader task freezes the REF object at connect;
    # the bridge mutates ref['value'] each turn, so handlers read the CURRENT user.
    from openeo_ai.sdk import user_context

    ref = {"value": "user-a"}
    user_context.bind_user_ref(ref)
    assert user_context.get_current_user_id() == "user-a"
    ref["value"] = "user-b"  # bridge updates the same object next turn
    assert user_context.get_current_user_id() == "user-b"


def test_clamp_limit_bounds():
    from openeo_ai.tools.monitor_tools import _clamp_limit

    assert _clamp_limit({"limit": 10 ** 9}, 50) == 200   # clamped to max
    assert _clamp_limit({"limit": "abc"}, 50) == 50      # defensive int parse
    assert _clamp_limit({"limit": -5}, 50) == 1          # floor
    assert _clamp_limit({}, 50) == 50                    # default


# ── create_asset ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_asset_stamps_user(repo, tools):
    set_current_user_id("user-a")
    out = await _call(tools, "monitor_create_asset", name="Farm Alpha", bbox=[76.0, 8.0, 76.1, 8.1])
    assert out.get("id")
    row = repo.get_asset("user-a", out["id"])
    assert row is not None and row["user_id"] == "user-a"


@pytest.mark.asyncio
async def test_create_asset_unknown_field_is_structured_error(repo, tools):
    set_current_user_id("user-a")
    out = await _call(tools, "monitor_create_asset", name="X", bbox=[0, 0, 1, 1], nonsense=1)
    assert out["error"] == "invalid_input"  # never a TypeError escaping


@pytest.mark.asyncio
async def test_create_asset_requires_auth(repo, tools):
    set_current_user_id(None)
    out = await _call(tools, "monitor_create_asset", name="X", bbox=[0, 0, 1, 1])
    assert out["error"] == "not_authenticated"


# ── create_watch ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_watch_rejects_empty_graph(repo, tools):
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    out = await _call(tools, "monitor_create_watch", asset_id=asset["id"], name="W")
    assert out["error"] == "empty_process_graph"  # dud watch rejected at creation


@pytest.mark.asyncio
async def test_create_watch_other_users_asset_denied(repo, tools):
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    set_current_user_id("user-b")
    out = await _call(
        tools, "monitor_create_watch",
        asset_id=asset["id"], name="W",
        process_graph={"load": {"process_id": "load_collection"}},
    )
    assert out["error"] == "asset_not_found"


@pytest.mark.asyncio
async def test_create_watch_folds_alert_targets_into_policy(repo, tools):
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    out = await _call(
        tools, "monitor_create_watch",
        asset_id=asset["id"], name="W",
        process_graph={"load": {"process_id": "load_collection"}},
        alert_targets=[{"channel": "webhook", "target": "https://example.test/hook"}],
    )
    watches = repo.list_watches("user-a", asset_id=asset["id"])
    policy = watches[0]["quality_policy"]
    assert policy["alert_targets"] == [{"channel": "webhook", "target": "https://example.test/hook"}]
    assert "worker_active" in out  # honesty field present


@pytest.mark.asyncio
async def test_create_watch_folds_friendly_alert_intent(repo, tools):
    # The agent expresses "alert if NDVI drops 0.1" via flat params.
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    await _call(
        tools, "monitor_create_watch",
        asset_id=asset["id"], name="W",
        process_graph={"load": {"process_id": "load_collection"}},
        alert_metric="NDVI", alert_direction="drop", alert_threshold=0.1,
    )
    policy = repo.list_watches("user-a", asset_id=asset["id"])[0]["quality_policy"]
    assert policy["metric"] == "NDVI"
    assert policy["direction"] == "drop"
    assert policy["threshold"] == 0.1


@pytest.mark.asyncio
async def test_create_watch_weekly_plus_alert_is_live_and_alerting(repo, tools):
    # Together: the flagship "weekly, alert if drops" produces a watch
    # that both runs (next_run_at set) and has an alert policy.
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    await _call(
        tools, "monitor_create_watch",
        asset_id=asset["id"], name="Weekly", cadence="P1W",
        process_graph={"load": {"process_id": "load_collection"}},
        alert_metric="NDVI", alert_direction="drop", alert_threshold=0.1,
        alert_targets=[{"channel": "webhook", "target": "https://example.test/hook"}],
    )
    w = repo.list_watches("user-a", asset_id=asset["id"])[0]
    assert w["next_run_at"] is not None and w["trigger_type"] == "scheduled"
    assert w["quality_policy"]["threshold"] == 0.1
    assert w["quality_policy"]["alert_targets"]


@pytest.mark.asyncio
async def test_create_watch_rejects_non_numeric_threshold(repo, tools):
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    out = await _call(
        tools, "monitor_create_watch",
        asset_id=asset["id"], name="W",
        process_graph={"load": {"process_id": "load_collection"}},
        alert_metric="NDVI", alert_threshold="lots",
    )
    assert out.get("error") == "invalid_input"


# ── watch lifecycle (gap-filler) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_pause_and_resume_watch(repo, tools):
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    watch = repo.create_watch("user-a", {
        "asset_id": asset["id"], "name": "W",
        "process_graph": {"load": {"process_id": "load_collection"}},
    })
    await _call(tools, "monitor_pause_watch", watch_id=watch["id"])
    assert repo.list_watches("user-a", asset_id=asset["id"])[0]["status"] == "paused"
    await _call(tools, "monitor_resume_watch", watch_id=watch["id"])
    assert repo.list_watches("user-a", asset_id=asset["id"])[0]["status"] == "active"


# ── signals + triage (gap-filler) ─────────────────────────────────────

def _seed_signal(repo, user_id, asset_id, severity="warning", metric="NDVI"):
    return repo.create_signal(user_id, {
        "asset_id": asset_id, "metric": metric, "value": 0.4,
        "severity": severity, "explanation": "NDVI dropped.",
    })


@pytest.mark.asyncio
async def test_asset_briefing_newest_signals_first(repo, tools):
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    _seed_signal(repo, "user-a", asset["id"], "info", "NDVI")
    _seed_signal(repo, "user-a", asset["id"], "critical", "NDWI")
    out = await _call(tools, "monitor_asset_briefing", asset_id=asset["id"])
    sigs = out["signals"]
    assert len(sigs) == 2
    assert sigs[0]["severity"] == "critical"  # newest first
    assert {"severity", "metric"} <= set(sigs[0])


@pytest.mark.asyncio
async def test_signal_acknowledge_then_resolve(repo, tools):
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    sig = _seed_signal(repo, "user-a", asset["id"])
    await _call(tools, "signal_acknowledge", signal_id=sig["id"])
    assert repo.list_signals("user-a", asset_id=asset["id"])[0]["status"] == "acknowledged"
    await _call(tools, "signal_resolve", signal_id=sig["id"])
    assert repo.list_signals("user-a", asset_id=asset["id"])[0]["status"] == "resolved"


@pytest.mark.asyncio
async def test_list_deliveries_omits_target(repo, tools):
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    sig = _seed_signal(repo, "user-a", asset["id"])
    repo.create_delivery("user-a", {
        "signal_id": sig["id"], "channel": "webhook",
        "target": "https://example.test/secret-hook",
    })
    out = await _call(tools, "monitor_list_deliveries")
    assert out["deliveries"], "expected at least one delivery"
    d = out["deliveries"][0]
    assert d["channel"] == "webhook"
    assert "target" not in d  # never surface the (secret-bearing) target


@pytest.mark.asyncio
async def test_portfolio_briefing_ranks_by_worst_severity(repo, tools):
    set_current_user_id("user-a")
    a1 = repo.create_asset("user-a", {"name": "calm", "bbox": [0, 0, 1, 1]})
    a2 = repo.create_asset("user-a", {"name": "alarm", "bbox": [2, 2, 3, 3]})
    _seed_signal(repo, "user-a", a1["id"], "info")
    _seed_signal(repo, "user-a", a2["id"], "critical")
    out = await _call(tools, "monitor_portfolio_briefing")
    assets = out["assets"]
    assert len(assets) == 2
    assert assets[0]["name"] == "alarm"  # worst severity first


# ── tenancy (reads AND writes) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_assets_is_user_scoped(repo, tools):
    repo.create_asset("user-a", {"name": "A-owned", "bbox": [0, 0, 1, 1]})
    set_current_user_id("user-b")
    out = await _call(tools, "monitor_list_assets")
    assert all(a["name"] != "A-owned" for a in out["assets"])


# ── evidence chain + saved_jobs scoping (the fixed IDOR) ──────────────

def _insert_saved_result(save_id, user_id, result_path):
    from datetime import datetime

    from openeo_ai.storage.models import SavedResult

    session = job_archive._SessionLocal()
    try:
        session.add(SavedResult(
            save_id=save_id, title="t", result_path=str(result_path),
            user_id=user_id, created_at=datetime.utcnow(),
        ))
        session.commit()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_evidence_chain_denies_other_user(repo, tools, tmp_path):
    job_archive._get_session().close()  # trigger SavedResult table creation
    f = tmp_path / "r.tif"
    f.write_bytes(b"x")
    _insert_saved_result("bbbb2222", "user-a", f)
    set_current_user_id("user-b")
    out = await _call(tools, "evidence_get_chain", save_id="bbbb2222")
    assert out["error"] == "not_found"
    set_current_user_id("user-a")
    ok = await _call(tools, "evidence_get_chain", save_id="bbbb2222")
    assert ok["save_id"] == "bbbb2222"


@pytest.mark.asyncio
async def test_saved_jobs_list_is_user_scoped(repo, tmp_path):
    # Regression pin for the fixed cross-tenant saved-jobs leak.
    from openeo_ai.tools.saved_jobs_tools import create_saved_jobs_tools

    job_archive._get_session().close()
    f = tmp_path / "r.tif"
    f.write_bytes(b"x")
    _insert_saved_result("aaaa1111", "user-a", f)
    sj = create_saved_jobs_tools(None)

    set_current_user_id("user-b")
    other = json.loads((await sj["saved_jobs_list"]({}))["content"][0]["text"])
    assert all(j["save_id"] != "aaaa1111" for j in other["jobs"])

    set_current_user_id("user-a")
    mine = json.loads((await sj["saved_jobs_list"]({}))["content"][0]["text"])
    assert any(j["save_id"] == "aaaa1111" for j in mine["jobs"])


@pytest.mark.asyncio
async def test_cross_user_write_denied(repo, tools):
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    sig = _seed_signal(repo, "user-a", asset["id"])
    watch = repo.create_watch("user-a", {
        "asset_id": asset["id"], "name": "W",
        "process_graph": {"load": {"process_id": "load_collection"}},
    })
    set_current_user_id("user-b")
    r1 = await _call(tools, "signal_resolve", signal_id=sig["id"])
    r2 = await _call(tools, "monitor_pause_watch", watch_id=watch["id"])
    assert r1["error"] == "signal_not_found"
    assert r2["error"] == "watch_not_found"
    # user-a's rows are untouched
    set_current_user_id("user-a")
    assert repo.list_signals("user-a", asset_id=asset["id"])[0]["status"] == "open"
    assert repo.list_watches("user-a", asset_id=asset["id"])[0]["status"] == "active"


@pytest.mark.asyncio
async def test_update_watch_rejects_protected_fields(repo, tools):
    # Mass-assignment guard: user_id/id/asset_id are not WatchUpdate fields, so
    # _reject_extra blocks them before they reach repository.setattr.
    set_current_user_id("user-a")
    asset = repo.create_asset("user-a", {"name": "A", "bbox": [0, 0, 1, 1]})
    watch = repo.create_watch("user-a", {
        "asset_id": asset["id"], "name": "W",
        "process_graph": {"load": {"process_id": "load_collection"}},
    })
    out = await _call(tools, "monitor_update_watch", watch_id=watch["id"], user_id="user-b")
    assert out["error"] == "invalid_input"
    # watch still belongs to user-a (never reassigned)
    assert repo.list_watches("user-a", asset_id=asset["id"])[0]["id"] == watch["id"]


@pytest.mark.asyncio
async def test_saved_jobs_fail_closed_without_user(repo, tmp_path):
    from openeo_ai.tools.saved_jobs_tools import create_saved_jobs_tools

    job_archive._get_session().close()
    f = tmp_path / "r.tif"
    f.write_bytes(b"x")
    _insert_saved_result("cccc3333", "user-a", f)
    sj = create_saved_jobs_tools(None)

    set_current_user_id(None)  # no authenticated user this turn
    listed = json.loads((await sj["saved_jobs_list"]({}))["content"][0]["text"])
    assert listed["jobs"] == []  # fail closed, NOT all-tenants
    deleted = json.loads((await sj["saved_jobs_delete"]({"save_id": "cccc3333"}))["content"][0]["text"])
    assert deleted["deleted"] is False
    assert job_archive.get_job("cccc3333", user_id="user-a") is not None  # row survives
