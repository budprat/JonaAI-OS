# ABOUTME: Tests the nightly backup's optional offsite recovery copy: off when unset, loud when half
# ABOUTME: set up, and with rclone's local backend, a sync that keeps upstream deletions for 90 days.
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("backup_results", REPO / "scripts/backup_results.py")
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)

FULL = {
    "JONA_RECOVERY_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
    "JONA_RECOVERY_BUCKET": "jonaai-recovery",
    "JONA_RECOVERY_ACCESS_KEY_ID": "id-value",
    "JONA_RECOVERY_SECRET_ACCESS_KEY": "secret-value",
}


class TestSettings:
    def test_absent_is_off(self):
        assert backup.recovery_config({}) is None

    def test_half_set_up_fails_loudly_naming_what_is_missing(self):
        with pytest.raises(RuntimeError, match="JONA_RECOVERY_SECRET_ACCESS_KEY"):
            backup.recovery_config({k: v for k, v in FULL.items() if k != "JONA_RECOVERY_SECRET_ACCESS_KEY"})

    @pytest.mark.parametrize("change", [
        {"JONA_RECOVERY_ENDPOINT": "https://acct.r2.cloudflarestorage.com/jonaai-recovery"},
        {"JONA_RECOVERY_ENDPOINT": "http://acct.r2.cloudflarestorage.com"},
        {"JONA_RECOVERY_BUCKET": "acct/jonaai-recovery"},
        {"JONA_R2_BUCKET": "jonaai-recovery"},  # the app's own bucket: the app could delete it
    ])
    def test_mistakes_are_refused(self, change):
        with pytest.raises(RuntimeError):
            backup.recovery_config({**FULL, **change})

    def test_full_settings_configure_a_separate_remote(self):
        config = backup.recovery_config({**FULL, "JONA_R2_BUCKET": "jonaai-results"})
        assert config["root"] == "recovery:jonaai-recovery"
        assert config["env"]["RCLONE_CONFIG_RECOVERY_SECRET_ACCESS_KEY"] == "secret-value"
        assert "secret" not in config["root"]


def _snapshot(base: Path, stamp: str, files: dict) -> Path:
    snapshot = base / stamp
    (snapshot / "files").mkdir(parents=True)
    for name, text in files.items():
        path = snapshot / "files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (snapshot / "tables.jsonl.gz").write_bytes(b"dump-" + stamp.encode())
    return snapshot


@pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone is not installed")
class TestPushWithRcloneLocalBackend:
    """The real rclone binary against a folder: no network, no credentials."""

    def _config(self, remote: Path):
        return {"root": f"recovery:{remote}", "env": {"RCLONE_CONFIG_RECOVERY_TYPE": "local"}}

    def test_an_upstream_deletion_is_kept_and_dumps_are_dated(self, tmp_path):
        remote = tmp_path / "remote"
        config = self._config(remote)
        day1 = _snapshot(tmp_path / "local", "2026-11-09", {"results/a1/result.tif": "A", "results/b2/result.tif": "B"})
        backup.push_offsite(str(day1), "2026-11-09", config)

        day2 = _snapshot(tmp_path / "local", "2026-11-10", {"results/a1/result.tif": "A"})  # b2 deleted
        backup.push_offsite(str(day2), "2026-11-10", config)

        assert (remote / "files/results/a1/result.tif").read_text() == "A"
        assert not (remote / "files/results/b2").exists()
        assert (remote / "replaced/2026-11-10/results/b2/result.tif").read_text() == "B"
        assert sorted(p.name for p in (remote / "database").iterdir()) == [
            "2026-11-09.jsonl.gz", "2026-11-10.jsonl.gz"]

    def test_pruning_goes_by_the_date_in_the_name(self, tmp_path):
        remote = tmp_path / "remote"
        old = remote / "replaced/2026-07-01/results/x"
        old.mkdir(parents=True)
        (old / "result.tif").write_text("old")
        (remote / "database").mkdir(parents=True)
        (remote / "database/2026-07-01.jsonl.gz").write_text("old dump")
        recent = remote / "replaced/2026-11-01/results/y"
        recent.mkdir(parents=True)
        os.utime(recent, (0, 0))  # an ancient file date must not get it pruned
        (recent / "result.tif").write_text("recent")

        day = _snapshot(tmp_path / "local", "2026-11-09", {"results/a1/result.tif": "A"})
        backup.push_offsite(str(day), "2026-11-09", self._config(remote))

        assert not (remote / "replaced/2026-07-01").exists()
        assert not (remote / "database/2026-07-01.jsonl.gz").exists()
        assert (remote / "replaced/2026-11-01/results/y/result.tif").exists()


class TestMainWhenOff:
    def test_no_settings_means_no_offsite_step(self, tmp_path, monkeypatch):
        if shutil.which("rsync") is None:
            pytest.skip("rsync is not installed")
        source = tmp_path / "source"
        (source / "results/a1").mkdir(parents=True)
        (source / "results/a1/result.tif").write_text("A")
        monkeypatch.setattr(backup, "SOURCE", str(source) + "/")
        monkeypatch.setattr(backup, "dump_tables", lambda path: Path(path).write_bytes(b"") or 0)
        monkeypatch.setattr(backup.os.path, "ismount", lambda path: True)
        monkeypatch.setenv("JONA_BACKUP_DIR", str(tmp_path / "backups" / "jonaai"))
        for name in backup.RECOVERY_SETTINGS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(backup, "push_offsite", lambda *a, **k: pytest.fail("pushed offsite while off"))

        assert backup.main() == 0


ISOLATION_ENV = (*backup.RECOVERY_SETTINGS, "JONA_R2_ENDPOINT", "JONA_R2_BUCKET",
                 "JONA_R2_ACCESS_KEY_ID", "JONA_R2_SECRET_ACCESS_KEY")


@pytest.mark.skipif(
    os.environ.get("JONA_RECOVERY_ISOLATION_CHECK") != "1"
    or not all(os.environ.get(name) for name in ISOLATION_ENV)
    or shutil.which("rclone") is None,
    reason="needs real credentials: set JONA_RECOVERY_ISOLATION_CHECK=1 with both the app's "
           "JONA_R2_* and the JONA_RECOVERY_* settings loaded (as root, from both env files)",
)
def test_each_token_is_refused_on_the_other_bucket():
    """The point of the recovery bucket: the app's token can't touch it, and the reverse."""
    env = os.environ

    def lists(key: str, secret: str, endpoint: str, bucket: str) -> int:
        child = {name: env[name] for name in ("PATH", "HOME") if name in env}
        child.update({
            "RCLONE_CONFIG_PROBE_TYPE": "s3", "RCLONE_CONFIG_PROBE_PROVIDER": "Cloudflare",
            "RCLONE_CONFIG_PROBE_ENDPOINT": env[endpoint], "RCLONE_CONFIG_PROBE_REGION": "auto",
            "RCLONE_CONFIG_PROBE_ACCESS_KEY_ID": env[key], "RCLONE_CONFIG_PROBE_SECRET_ACCESS_KEY": env[secret],
        })
        return subprocess.run(["rclone", "-q", "lsf", f"probe:{env[bucket]}"], env=child,
                              capture_output=True, timeout=60).returncode

    app_on_recovery = lists("JONA_R2_ACCESS_KEY_ID", "JONA_R2_SECRET_ACCESS_KEY",
                            "JONA_RECOVERY_ENDPOINT", "JONA_RECOVERY_BUCKET")
    recovery_on_app = lists("JONA_RECOVERY_ACCESS_KEY_ID", "JONA_RECOVERY_SECRET_ACCESS_KEY",
                            "JONA_R2_ENDPOINT", "JONA_R2_BUCKET")
    assert app_on_recovery != 0, "the app's token can list the recovery bucket"
    assert recovery_on_app != 0, "the recovery token can list the app's bucket"
