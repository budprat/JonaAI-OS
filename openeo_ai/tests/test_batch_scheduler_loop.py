# ABOUTME: Stored schedules fire: the app starts SchedulerLoop, which runs each due schedule once
# ABOUTME: as a scheduled batch owned by the schedule's user.
import asyncio
from types import SimpleNamespace

from openeo_ai.batch import routes
from openeo_ai.batch.batch_store import BatchStore
from openeo_ai.batch.models import (LocationInput, ScheduleCreate, ScheduleFrequency, WorkflowTemplateCreate,
                                    init_batch_db)
from openeo_ai.batch.scheduler import ScheduleStore, SchedulerLoop
from openeo_ai.batch.template_store import TemplateStore


def _due_schedule(tmp_path, monkeypatch):
    db = str(tmp_path / "batch.db")
    init_batch_db(db)
    template = TemplateStore(db_path=db).create_template(
        WorkflowTemplateCreate(name="NDVI", process_graph={"save": {"process_id": "save_result", "arguments": {}, "result": True}}),
        user_id="owner",
    )
    schedules, batches = ScheduleStore(db_path=db), BatchStore(db_path=db)
    started = []
    monkeypatch.setattr(routes, "_get_schedule_store", lambda: schedules)
    monkeypatch.setattr(routes, "_get_batch_store", lambda: batches)
    monkeypatch.setattr(routes, "_get_executor", lambda: SimpleNamespace(start_batch_async=started.append))
    sched = schedules.create_schedule(ScheduleCreate(
        template_id=template.id, name="weekly", frequency=ScheduleFrequency.WEEKLY,
        locations=[LocationInput(name="Farm", spatial_extent={"west": 1, "south": 2, "east": 3, "north": 4})],
    ), "owner")
    conn = schedules._conn()
    try:
        conn.execute("UPDATE schedules SET next_run_at = ? WHERE id = ?", ("2000-01-01T00:00:00", sched.id))
        conn.commit()
    finally:
        conn.close()
    return schedules, batches, sched, started


def _run_loop_briefly(store):
    async def go():
        loop = SchedulerLoop(store, routes.fire_due_schedule, poll_interval_seconds=0.05)
        await loop.start()
        await asyncio.sleep(0.3)
        await loop.stop()
    asyncio.run(go())


def test_a_due_schedule_fires_once_as_a_scheduled_batch_for_its_owner(tmp_path, monkeypatch):
    schedules, batches, sched, started = _due_schedule(tmp_path, monkeypatch)

    _run_loop_briefly(schedules)

    assert len(started) == 1, "fired once, then rescheduled into the future"
    batch = batches.get_batch_job(started[0])
    assert batch.user_id == "owner"
    assert batch.trigger_type.value == "scheduled"
    after = schedules.get_schedule(sched.id)
    assert after.runs_completed == 1
    assert after.next_run_at > "2000-01-02"


def test_the_app_starts_the_scheduler_unless_it_is_switched_off(monkeypatch):
    from openeo_ai import web_interface

    monkeypatch.delenv("JONA_SCHEDULER", raising=False)
    assert web_interface._scheduler_enabled()
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("JONA_SCHEDULER", off)
        assert not web_interface._scheduler_enabled()
    hooks = [h.__name__ for h in web_interface.app.router.on_startup]
    assert "_startup_scheduler" in hooks
