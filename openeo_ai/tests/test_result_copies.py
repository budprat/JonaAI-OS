# ABOUTME: Tests offsite copies of saved results without R2 or a network: a memory store for the
# ABOUTME: flow, rclone's local backend for the real binary, and a fake runner for its commands.
import importlib.util
import json
import logging
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from openeo_ai import lifecycle
from openeo_ai.storage import job_archive
from openeo_ai.storage import result_copies as rc
from openeo_ai.storage.models import ResultCopy

REPO = Path(__file__).resolve().parents[2]


def _geotiff(path: Path, size: int = 64, noise: bool = False) -> Path:
    rng = np.random.default_rng(0)
    data = rng.random((size, size), dtype=np.float32) if noise else np.full((size, size), 0.5, np.float32)
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(10.0, 50.0, 0.001, 0.001),
    ) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture()
def archive(monkeypatch, tmp_path):
    """job_archive on SQLite in a temp folder, with copies going to a memory store."""
    job_archive._engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)
    job_archive._tables_ready = False
    job_archive._get_session().close()
    ResultCopy.__table__.create(bind=job_archive._engine, checkfirst=True)
    monkeypatch.setattr(job_archive, "_RESULTS_DIR", tmp_path / "archive")
    monkeypatch.setenv("RESULT_STORAGE_PATH", str(tmp_path / "working"))
    monkeypatch.setattr(job_archive, "_auto_interpret", lambda *a, **k: None)
    monkeypatch.setattr(job_archive, "_auto_assign_project", lambda *a, **k: None)
    store = rc.MemoryStore()
    rc.set_store(store)
    yield store
    rc._store_loaded = False
    rc._store = None
    job_archive._engine = None
    job_archive._SessionLocal = None
    job_archive._tables_ready = False


def _save(tmp_path, user="alice", **kwargs):
    source = _geotiff(tmp_path / f"src-{time.time_ns()}.tif", **kwargs)
    save_id = job_archive.save_job(str(source), "NDVI test", user_id=user)
    assert save_id
    return save_id


def _copy(save_id):
    session = job_archive._get_session()
    try:
        return session.get(ResultCopy, save_id)
    finally:
        session.close()


# ── Configuration ──

class TestConfiguration:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        for name in ("JONA_RESULT_COPIES", "JONA_RESULT_COPIES_DIR", *rc.R2_SETTINGS):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(rc.shutil, "which", lambda name: "/usr/bin/rclone")

    def test_off_by_default(self):
        assert rc.store_from_env() is None

    def test_unknown_mode_is_off(self, monkeypatch):
        monkeypatch.setenv("JONA_RESULT_COPIES", "s3")
        assert rc.store_from_env() is None

    def test_missing_r2_settings_are_named_but_values_never_logged(self, monkeypatch, caplog):
        monkeypatch.setenv("JONA_RESULT_COPIES", "r2")
        monkeypatch.setenv("JONA_R2_SECRET_ACCESS_KEY", "sekrit-value")
        with caplog.at_level(logging.INFO):
            assert rc.store_from_env() is None
        assert "JONA_R2_ENDPOINT" in caplog.text
        assert "sekrit-value" not in caplog.text

    def test_complete_r2_settings_configure_rclone_without_logging_values(self, monkeypatch, caplog):
        monkeypatch.setenv("JONA_RESULT_COPIES", "r2")
        for name in rc.R2_SETTINGS:
            monkeypatch.setenv(name, f"value-of-{name}")
        monkeypatch.setenv("JONA_R2_ENDPOINT", "https://value-of-account.r2.cloudflarestorage.com")
        with caplog.at_level(logging.INFO):
            store = rc.store_from_env()
        assert isinstance(store, rc.RcloneStore)
        assert store.root == "jona:value-of-JONA_R2_BUCKET"
        assert store._config["RCLONE_CONFIG_JONA_PROVIDER"] == "Cloudflare"
        assert store._config["RCLONE_CONFIG_JONA_NO_CHECK_BUCKET"] == "true"
        assert "value-of" not in caplog.text

    @pytest.mark.parametrize("endpoint,bucket,problem", [
        ("https://acct.r2.cloudflarestorage.com", "jonaai-results", None),
        ("https://acct.r2.cloudflarestorage.com/", "jonaai-results", None),
        ("https://acct.eu.r2.cloudflarestorage.com", "jonaai-results", None),
        ("https://acct.r2.cloudflarestorage.com/jonaai-results", "jonaai-results", "must not include a path"),
        ("http://acct.r2.cloudflarestorage.com", "jonaai-results", "https://"),
        ("https://s3.amazonaws.com", "jonaai-results", "r2.cloudflarestorage.com"),
        ("https://acct.r2.cloudflarestorage.com", "acct/jonaai-results", "bucket name only"),
    ])
    def test_r2_settings_are_checked(self, endpoint, bucket, problem):
        found = rc.r2_settings_problem(endpoint, bucket)
        assert (found is None) if problem is None else (problem in found)

    def test_a_bucket_url_as_endpoint_turns_copies_off_without_quoting_it(self, monkeypatch, caplog):
        monkeypatch.setenv("JONA_RESULT_COPIES", "r2")
        for name in rc.R2_SETTINGS:
            monkeypatch.setenv(name, "secret-ish")
        monkeypatch.setenv("JONA_R2_ENDPOINT", "https://acct123.r2.cloudflarestorage.com/jonaai-results")
        monkeypatch.setenv("JONA_R2_BUCKET", "jonaai-results")
        with caplog.at_level(logging.INFO):
            assert rc.store_from_env() is None
        assert "must not include a path" in caplog.text
        assert "acct123" not in caplog.text and "secret-ish" not in caplog.text

    def test_no_rclone_means_off(self, monkeypatch):
        monkeypatch.setenv("JONA_RESULT_COPIES", "directory")
        monkeypatch.setenv("JONA_RESULT_COPIES_DIR", "/x")
        monkeypatch.setattr(rc.shutil, "which", lambda name: None)
        assert rc.store_from_env() is None

    def test_directory_mode_uses_the_local_backend(self, monkeypatch):
        monkeypatch.setenv("JONA_RESULT_COPIES", "directory")
        monkeypatch.setenv("JONA_RESULT_COPIES_DIR", "/srv/copies")
        store = rc.store_from_env()
        assert store.root == "jona:/srv/copies"
        assert store._config == {"RCLONE_CONFIG_JONA_TYPE": "local"}


class TestCopiesOff:
    def test_saving_and_reading_work_unchanged(self, archive, tmp_path):
        rc.set_store(None)
        save_id = _save(tmp_path)
        assert _copy(save_id) is None
        assert rc.status_of(save_id) is None
        assert rc.start_sync() is None
        assert job_archive.get_job_detail(save_id, user_id="alice")["backup"] is None


# ── Copy, refresh, restore ──

class TestCopyFlow:
    def test_save_queues_and_sync_uploads_a_cog(self, archive, tmp_path):
        save_id = _save(tmp_path)
        assert _copy(save_id).status == "pending"

        assert rc.sync_one(save_id) == "synced"

        row = _copy(save_id)
        assert (row.downsample, row.attempts, row.last_error) == (1, 0, None)
        prefix = f"results/alice/{save_id}/"
        assert set(archive.objects) == {prefix + name for name in (row_name(save_id), "metadata.json")}
        assert row.bytes == sum(len(v) for v in archive.objects.values())
        assert list((tmp_path / "working" / ".copies").iterdir()) == []
        assert [p.name for p in (job_archive._RESULTS_DIR / save_id).iterdir() if p.is_dir()] == []
        staged = tmp_path / "uploaded.tif"
        staged.write_bytes(archive.objects[prefix + row_name(save_id)])
        with rasterio.open(staged) as src:
            assert src.profile["tiled"] and src.compression.name.lower() == "deflate"

    def test_a_new_bundle_uploads_only_the_side_files(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        result_key = f"results/alice/{save_id}/{row_name(save_id)}"
        del archive.objects[result_key]

        assert job_archive.save_visualizations(save_id, {"charts": [1]}, user_id="alice")
        assert _copy(save_id).status == "pending"
        assert rc.sync_one(save_id) == "synced"

        assert result_key not in archive.objects
        assert json.loads(archive.objects[f"results/alice/{save_id}/visualizations.json"]) == {"charts": [1]}

    def test_a_lost_result_is_restored_when_opened(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        folder = job_archive._RESULTS_DIR / save_id
        shutil.rmtree(folder)

        assert job_archive.restore_saved_result(save_id) is True

        job = job_archive.get_job(save_id, user_id="alice")
        assert job is not None
        with rasterio.open(job["result_path"]) as src:
            assert src.read(1).shape == (64, 64)
        assert (folder / "metadata.json").exists()
        assert not list(folder.glob("*.restoring"))

    def test_a_coarser_copy_says_so_after_restore(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        session = job_archive._get_session()
        session.get(ResultCopy, save_id).downsample = 2
        session.commit()
        session.close()
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)

        job_archive.restore_saved_result(save_id)

        metadata = json.loads((job_archive._RESULTS_DIR / save_id / "metadata.json").read_text())
        assert metadata["restored_copy"]["downsample"] == 2

    def test_without_a_copy_a_lost_result_stays_missing(self, archive, tmp_path):
        save_id = _save(tmp_path)
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        assert job_archive.restore_saved_result(save_id) is False
        assert job_archive.get_job(save_id, user_id="alice") is None

    def test_reading_never_downloads(self, archive, tmp_path, monkeypatch):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        monkeypatch.setattr(archive, "get", lambda *a: pytest.fail("get_job downloaded"))

        assert job_archive.get_job(save_id, user_id="alice") is None
        assert job_archive.get_job_detail(save_id, user_id="alice") is None
        # The access check still sees the lost result, so its owner can open it.
        assert job_archive.get_job(save_id, require_file=False)["user_id"] == "alice"

    def test_restoring_a_present_result_downloads_nothing(self, archive, tmp_path, monkeypatch):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        monkeypatch.setattr(archive, "get", lambda *a: pytest.fail("downloaded a present result"))
        assert job_archive.restore_saved_result(save_id) is True


class TestRestoreKeepsTheRecordsTrue:
    def test_a_copy_missing_from_the_bucket_stops_being_offered(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        archive.objects.clear()  # someone emptied the bucket
        listed = {j["save_id"]: j for j in job_archive.list_jobs(user_id="alice", include_restorable=True)}
        assert listed[save_id]["restorable"] is True

        assert job_archive.restore_saved_result(save_id) is False

        listed = {j["save_id"]: j for j in job_archive.list_jobs(user_id="alice", include_restorable=True)}
        assert save_id not in listed
        row = _copy(save_id)
        assert (row.status, row.synced_at, row.bytes) == ("pending", None, 0)
        assert "missing" in row.last_error
        assert rc.sync_one(save_id) == "refused"  # nothing local to copy either; final

    def test_a_restore_records_the_restored_files_size(self, archive, tmp_path, monkeypatch):
        save_id = _save(tmp_path, size=512, noise=True)
        original = job_archive.get_job(save_id)["size_bytes"]
        monkeypatch.setattr(rc, "FULL_RESOLUTION_BYTES", original // 2)
        assert rc.sync_one(save_id) == "synced"
        assert _copy(save_id).downsample > 1
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)

        assert job_archive.restore_saved_result(save_id) is True

        job = job_archive.get_job(save_id)
        assert job["size_bytes"] == Path(job["result_path"]).stat().st_size < original


class TestListing:
    def _listed(self, include_restorable=True):
        return {job["save_id"]: job for job in job_archive.list_jobs(user_id="alice", include_restorable=include_restorable)}

    def test_lost_results_with_a_copy_are_listed_as_restorable(self, archive, tmp_path):
        copied_then_lost = _save(tmp_path)
        rc.sync_one(copied_then_lost)
        lost_without_copy = _save(tmp_path)
        present_with_copy = _save(tmp_path)
        rc.sync_one(present_with_copy)
        present_without_copy = _save(tmp_path)
        for save_id in (copied_then_lost, lost_without_copy):
            shutil.rmtree(job_archive._RESULTS_DIR / save_id)

        listed = self._listed()

        assert (listed[copied_then_lost]["available"], listed[copied_then_lost]["restorable"]) == (False, True)
        assert lost_without_copy not in listed
        assert (listed[present_with_copy]["available"], listed[present_with_copy]["restorable"]) == (True, True)
        assert (listed[present_without_copy]["available"], listed[present_without_copy]["restorable"]) == (True, False)

    def test_restorable_comes_from_the_copy_row_not_the_missing_file(self, archive, tmp_path):
        save_id = _save(tmp_path)  # queued, never copied
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        assert save_id not in self._listed()

        rc.sync_one(save_id)  # refused: nothing local to copy
        assert save_id not in self._listed()

    def test_a_copy_being_refreshed_is_still_restorable(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        rc.queue(save_id, "alice")
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        assert self._listed()[save_id]["restorable"] is True

    def test_other_callers_see_the_old_shape(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        kept = _save(tmp_path)

        listed = self._listed(include_restorable=False)

        assert set(listed) == {kept}
        assert "available" not in listed[kept] and "restorable" not in listed[kept]

    def test_with_copies_off_lost_results_stay_hidden(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        rc.set_store(None)
        assert save_id not in self._listed()


class TestOpening:
    def test_loading_restores_after_the_access_check_and_off_the_loop(self, archive, tmp_path, monkeypatch):
        import asyncio
        import threading

        import openeo_ai.web_interface as wi
        from openeo_ai.storage import result_access

        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        calls = []
        real_restore = job_archive.restore_saved_result

        def restore(sid):
            calls.append(("restore", threading.current_thread() is threading.main_thread()))
            return real_restore(sid)

        monkeypatch.setattr(job_archive, "restore_saved_result", restore)
        monkeypatch.setattr(result_access, "can_read_saved_result",
                            lambda sid, who: calls.append(("check", who)) or who == "alice")
        sent = []

        class Socket:
            async def send_json(self, data, **kwargs):
                sent.append(data)

        assert asyncio.run(wi._send_saved_job(Socket(), save_id, "bob")) is False
        assert calls == [("check", "bob")]  # no restore for someone who can't read it

        calls.clear()
        assert asyncio.run(wi._send_saved_job(Socket(), save_id, "alice")) is True
        assert calls == [("check", "alice"), ("restore", False)]
        assert (job_archive._RESULTS_DIR / save_id).exists()

    def test_detail_reports_the_backup(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        assert job_archive.get_job_detail(save_id, user_id="alice")["backup"]["status"] == "synced"


def row_name(save_id):
    return Path(job_archive.get_job(save_id)["result_path"]).name


# ── Limits ──

class TestLimits:
    def test_hard_limit_refuses_and_rechecks_later(self, archive, tmp_path, monkeypatch):
        first = _save(tmp_path)
        rc.sync_one(first)
        monkeypatch.setattr(rc, "HARD_LIMIT_BYTES", _copy(first).bytes + 10)
        second = _save(tmp_path, user="bob")

        assert rc.sync_one(second) == "refused"

        row = _copy(second)
        assert "share" in row.last_error and row.next_attempt_at is not None
        assert not any(second in key for key in archive.objects)
        assert job_archive.get_job(second) is not None  # still saved locally

    def test_a_copy_queued_for_a_refresh_still_counts_against_the_limit(self, archive, tmp_path, monkeypatch):
        first = _save(tmp_path)
        rc.sync_one(first)
        monkeypatch.setattr(rc, "HARD_LIMIT_BYTES", _copy(first).bytes + 10)
        rc.queue(first, "alice")  # e.g. its bundle changed; its objects stay in the bucket
        assert _copy(first).status == "pending"

        assert rc.sync_one(_save(tmp_path, user="bob")) == "refused"

    def test_per_user_limit(self, archive, tmp_path, monkeypatch):
        first = _save(tmp_path)
        rc.sync_one(first)
        monkeypatch.setattr(rc, "USER_LIMIT_BYTES", _copy(first).bytes + 10)
        assert rc.sync_one(_save(tmp_path)) == "refused"
        assert rc.sync_one(_save(tmp_path, user="bob")) == "synced"

    def test_defaults_are_jonaais_agreed_share(self):
        assert (rc.HARD_LIMIT_BYTES, rc.SOFT_LIMIT_BYTES, rc.USER_LIMIT_BYTES) == (3 * 10**9, 2 * 10**9, 10**9)

    @pytest.mark.parametrize("value,expected", [
        ("", 3 * 10**9), ("4", 4 * 10**9), ("0.5", 5 * 10**8), ("0", 3 * 10**9), ("-1", 3 * 10**9), ("lots", 3 * 10**9),
    ])
    def test_limits_can_be_set_and_bad_values_keep_the_default(self, monkeypatch, value, expected):
        monkeypatch.setenv("JONA_R2_HARD_LIMIT_GB", value)
        assert rc._limit_bytes("JONA_R2_HARD_LIMIT_GB", rc.HARD_LIMIT_BYTES) == expected

    def test_the_setting_drives_the_refusal(self, archive, tmp_path, monkeypatch):
        first = _save(tmp_path)
        rc.sync_one(first)
        monkeypatch.setenv("JONA_R2_HARD_LIMIT_GB", str((_copy(first).bytes + 10) / 10**9))
        second = _save(tmp_path, user="bob")
        assert rc.sync_one(second) == "refused"
        assert "share" in _copy(second).last_error

    def test_too_large_is_refused_for_good(self, archive, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "FULL_RESOLUTION_BYTES", 10)
        save_id = _save(tmp_path)

        assert rc.sync_one(save_id) == "refused"
        assert _copy(save_id).next_attempt_at is None
        assert save_id not in rc.due()

        rc.queue(save_id, "alice")  # a new bundle doesn't make the result smaller
        assert _copy(save_id).status == "refused"

    def test_large_results_are_stored_coarser(self, tmp_path, monkeypatch):
        source = _geotiff(tmp_path / "big.tif", size=512, noise=True)
        for name in ("full", "smaller"):
            (tmp_path / name).mkdir()
        full, factor = rc.prepare_result(source, tmp_path / "full")
        assert factor == 1
        monkeypatch.setattr(rc, "FULL_RESOLUTION_BYTES", full.stat().st_size - 1)

        smaller, factor = rc.prepare_result(source, tmp_path / "smaller")

        assert factor in (2, 4)
        with rasterio.open(smaller) as src:
            assert src.width == 512 // factor
            assert src.crs.to_epsg() == 4326


# ── Failures, delete, sweep, backfill ──

class _BrokenStore(rc.MemoryStore):
    def put(self, local, key):
        raise rc.StoreError("rclone copyto exited 1: network unreachable")


class TestFailuresAndHousekeeping:
    def test_a_failed_upload_backs_off(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.set_store(_BrokenStore())

        assert rc.sync_one(save_id) == "failed"

        row = _copy(save_id)
        assert row.attempts == 1 and "network unreachable" in row.last_error
        assert row.next_attempt_at > datetime.utcnow()
        assert save_id not in rc.due()

        session = job_archive._get_session()
        session.get(ResultCopy, save_id).next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        session.commit()
        session.close()
        assert save_id in rc.due()

    def test_a_result_lost_before_its_first_copy_is_not_retried(self, archive, tmp_path):
        save_id = _save(tmp_path)
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)

        assert rc.sync_one(save_id) == "refused"

        row = _copy(save_id)
        assert "local result file is missing" in row.last_error
        assert row.next_attempt_at is None and save_id not in rc.due()

    def test_a_synced_copy_queued_after_the_local_files_went_stays_synced(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        rc.queue(save_id, "alice")
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)

        assert rc.sync_one(save_id) == "synced"
        assert any(save_id in key for key in archive.objects)

    def test_a_bundle_queued_during_the_upload_runs_again(self, archive, tmp_path, monkeypatch):
        save_id = _save(tmp_path)
        original_put = archive.put

        def put(local, key):
            if key.endswith("metadata.json") and not getattr(put, "done", False):
                put.done = True
                job_archive.save_visualizations(save_id, {"charts": ["late"]}, user_id="alice")
            original_put(local, key)

        monkeypatch.setattr(archive, "put", put)

        assert rc.sync_one(save_id) == "pending"  # not marked synced over the newer request
        assert rc.sync_one(save_id) == "synced"
        bundle = json.loads(archive.objects[f"results/alice/{save_id}/visualizations.json"])
        assert bundle == {"charts": ["late"]}

    def test_a_result_deleted_during_the_upload_leaves_nothing_behind(self, archive, tmp_path, monkeypatch):
        save_id = _save(tmp_path)
        original_put = archive.put

        def put(local, key):
            original_put(local, key)
            if key.endswith("metadata.json"):
                job_archive.delete_job(save_id, user_id="alice")

        monkeypatch.setattr(archive, "put", put)

        assert rc.sync_one(save_id) is None
        assert not any(save_id in key for key in archive.objects)
        assert _copy(save_id) is None

    def test_deleting_a_result_deletes_its_copy(self, archive, tmp_path):
        save_id = _save(tmp_path)
        rc.sync_one(save_id)
        assert job_archive.delete_job(save_id, user_id="alice")
        deadline = time.time() + 5
        while any(save_id in key for key in archive.objects) and time.time() < deadline:
            time.sleep(0.02)
        assert not any(save_id in key for key in archive.objects)

    def test_sweep_removes_only_orphans(self, archive, tmp_path):
        kept = _save(tmp_path)
        rc.sync_one(kept)
        archive.objects["results/bob/gone1234/result.tif"] = b"x"

        assert rc.sweep() == 1

        assert "results/bob/gone1234/result.tif" not in archive.objects
        assert any(kept in key for key in archive.objects)

    def test_sweep_never_deletes_every_copy(self, archive, tmp_path, caplog):
        # The empty-database case: nothing in saved_results matches, so all look orphaned.
        archive.objects["results/a/aaaa1111/result.tif"] = b"x"
        archive.objects["results/b/bbbb2222/result.tif"] = b"x"
        with caplog.at_level(logging.WARNING):
            assert rc.sweep() == 0
        assert len(archive.objects) == 2
        assert "Sweep refused: 2 of 2" in caplog.text

    def test_sweep_refuses_more_than_the_mass_delete_limit(self, archive, tmp_path):
        kept = _save(tmp_path)
        rc.sync_one(kept)
        for n in range(rc.SWEEP_MAX_DELETES + 1):
            archive.objects[f"results/x/gone{n:04d}/result.tif"] = b"x"
        assert rc.sweep() == 0
        assert sum(1 for key in archive.objects if "/gone" in key) == rc.SWEEP_MAX_DELETES + 1

    def test_a_person_can_allow_a_mass_delete(self, archive, tmp_path):
        archive.objects["results/a/aaaa1111/result.tif"] = b"x"
        assert rc.sweep(allow_mass_delete=True) == 1
        assert archive.objects == {}

    def test_results_saved_while_off_are_backfilled(self, archive, tmp_path):
        store = archive
        rc.set_store(None)
        save_id = _save(tmp_path)
        rc.set_store(store)

        assert rc.backfill() == 1
        assert _copy(save_id).status == "pending"

    def test_stale_uploads_are_aborted_daily(self, archive, monkeypatch):
        calls = []
        monkeypatch.setattr(archive, "abort_stale_uploads", lambda: calls.append(1), raising=False)
        sync = rc.CopySync()
        sync.tick()
        sync.tick()
        assert calls == [1]  # once per day, not once per tick
        sync._last_cleanup -= rc.CLEANUP_INTERVAL
        sync.tick()
        assert calls == [1, 1]

    def test_a_failed_cleanup_doesnt_stop_the_tick(self, archive, tmp_path, monkeypatch):
        def fail():
            raise rc.StoreError("rclone backend exited 1: AccessDenied")

        monkeypatch.setattr(archive, "abort_stale_uploads", fail, raising=False)
        save_id = _save(tmp_path)
        rc.CopySync().tick()
        assert _copy(save_id).status == "synced"

    def test_one_tick_copies_and_sweeps(self, archive, tmp_path):
        save_id = _save(tmp_path)
        archive.objects["results/x/orphan99/result.tif"] = b"x"
        rc.CopySync().tick()
        assert _copy(save_id).status == "synced"
        assert "results/x/orphan99/result.tif" not in archive.objects


# ── Lifecycle and migration ──

class TestDeclarations:
    def test_lifecycle_is_registered(self):
        assert lifecycle.RESULT_COPY in lifecycle.ALL

    def test_migration_matches_the_lifecycle(self):
        path = REPO / "openeo_app/psql/alembic/versions/phase20_result_copies.py"
        spec = importlib.util.spec_from_file_location("phase20", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert set(module.STATUSES) == lifecycle.RESULT_COPY.states
        assert module.down_revision == "phase19_status_lifecycles"

    def test_database_refuses_an_undeclared_status(self, archive, tmp_path):
        save_id = _save(tmp_path)
        session = job_archive._get_session()
        try:
            session.get(ResultCopy, save_id).status = "uploading"
            with pytest.raises(IntegrityError):
                session.commit()
        finally:
            session.rollback()
            session.close()


# ── The rclone store ──

@pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone is not installed")
class TestRcloneLocalBackend:
    """The real binary against rclone's local backend: no network, no credentials."""

    def test_put_get_list_delete(self, tmp_path):
        store = rc.RcloneStore(f"jona:{tmp_path / 'remote'}", {"RCLONE_CONFIG_JONA_TYPE": "local"})
        source = tmp_path / "a.json"
        source.write_text("{}")

        store.put(source, "results/u/s1/a.json")
        store.put(source, "results/u/s1/b.json")

        assert store.list_keys("results/") == ["results/u/s1/a.json", "results/u/s1/b.json"]
        assert store.get("results/u/s1/a.json", tmp_path / "back.json")
        assert (tmp_path / "back.json").read_text() == "{}"
        assert store.get("results/u/s1/missing.json", tmp_path / "x") is False

        store.delete_prefix("results/u/s1/")
        assert store.list_keys("results/") == []
        store.delete_prefix("results/u/s1/")  # already gone is fine
        store.abort_stale_uploads()  # nothing to do for a folder, and no error

    def test_save_copy_and_restore_end_to_end(self, archive, tmp_path):
        rc.set_store(rc.RcloneStore(f"jona:{tmp_path / 'remote'}", {"RCLONE_CONFIG_JONA_TYPE": "local"}))
        save_id = _save(tmp_path)

        assert rc.sync_one(save_id) == "synced"
        shutil.rmtree(job_archive._RESULTS_DIR / save_id)
        assert job_archive.restore_saved_result(save_id) is True
        job = job_archive.get_job(save_id, user_id="alice")

        assert job is not None
        with rasterio.open(job["result_path"]) as src:
            assert src.read(1).shape == (64, 64)


class TestRcloneCommands:
    def _store(self, runner):
        return rc.RcloneStore("jona:bucket", {"RCLONE_CONFIG_JONA_TYPE": "s3"}, runner=runner)

    def test_child_gets_only_path_home_and_the_remote(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "not-for-rclone")
        seen = {}

        def runner(args, env, **kwargs):
            seen.update(args=args, env=env)
            return subprocess.CompletedProcess(args, 0, "", "")

        self._store(runner).put(Path("/x/a.tif"), "results/u/s/a.tif")

        assert seen["args"] == ["rclone", "-q", "copyto", "/x/a.tif", "jona:bucket/results/u/s/a.tif"]
        assert set(seen["env"]) <= {"PATH", "HOME", "RCLONE_CONFIG_JONA_TYPE"}

    def test_stale_upload_cleanup_command(self):
        seen = {}

        def runner(args, **kwargs):
            seen["args"] = args
            return subprocess.CompletedProcess(args, 0, "", "")

        self._store(runner).abort_stale_uploads()
        assert seen["args"] == ["rclone", "-q", "backend", "cleanup", "jona:bucket", "-o", "max-age=24h"]

    def test_failures_and_timeouts_raise_store_errors(self):
        def failing(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, "", "AccessDenied")

        def hanging(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 600)

        with pytest.raises(rc.StoreError, match="exited 1: AccessDenied"):
            self._store(failing).put(Path("/x"), "k")
        with pytest.raises(rc.StoreError):
            self._store(hanging).get("k", Path("/x"))


def test_a_save_id_collision_never_touches_the_existing_result(archive, tmp_path, monkeypatch):
    import uuid as real_uuid
    from types import SimpleNamespace

    first = _save(tmp_path)
    folder = job_archive._RESULTS_DIR / first
    before = sorted(p.name for p in folder.iterdir())
    ids = iter([f"{first}0000-0000", "fresh000-0000"])  # the first draw collides
    monkeypatch.setattr(job_archive, "uuid", SimpleNamespace(uuid4=lambda: next(ids, None) or real_uuid.uuid4()))

    second = _save(tmp_path)

    assert second == "fresh000"
    assert sorted(p.name for p in folder.iterdir()) == before  # untouched, not overwritten or deleted
    assert job_archive.get_job(first)["save_id"] == first


_REAL_AUTO_ASSIGN = job_archive._auto_assign_project  # the archive fixture stubs it out


def test_two_users_saving_the_same_place_each_get_their_own_project(archive, tmp_path):
    from openeo_ai.storage.models import Project

    ids = [_REAL_AUTO_ASSIGN(_save(tmp_path, user=user), "Paris NDVI Monitoring", user_id=user)
           for user in ("alice", "bob")]

    assert all(ids) and ids[0] != ids[1]
    session = job_archive._get_session()
    try:
        owners = {p.id: p.user_id for p in session.query(Project).all()}
    finally:
        session.close()
    assert owners == {ids[0]: "alice", ids[1]: "bob"}
    assert all(len(i) <= 36 for i in ids)
