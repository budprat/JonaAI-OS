# ABOUTME: Optional offsite copies of saved results (R2 through rclone), restored when the local
# ABOUTME: copy is missing. Off unless configured; the local archive stays the primary copy.
"""Offsite copies of saved results.

Setup and limits: docs/deployment.md, "Offsite copies of saved results".

`JONA_RESULT_COPIES=r2` copies to the R2 bucket named by the `JONA_R2_*` settings.
`JONA_RESULT_COPIES=directory` copies to `JONA_RESULT_COPIES_DIR` instead, to try the
flow without R2. Anything else, or a missing setting, leaves copies off: every function
here then does nothing, which is a normal state, not an error.

Each saved result has one `result_copies` row whose status follows `RESULT_COPY`. Saving
queues it; a sync thread uploads, retries and enforces the budgets; deleting removes the
remote copy; `restore` brings the files back when the local folder is gone.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from openeo_ai.lifecycle import RESULT_COPY
from openeo_app.core.paths import result_root

logger = logging.getLogger(__name__)

# Retention limits, in decimal units as Cloudflare bills them.
#
# The R2 free allowance (10 GB-month of storage, 1M Class A and 10M Class B operations a
# month) belongs to the Cloudflare ACCOUNT, which may also hold other projects' buckets.
# These limits are JonaAI's SHARE of it, not a JonaAI-private 10 GB.
#
# What the hard stop can promise: only JonaAI's own usage. It counts bytes from the
# result_copies rows. It cannot see what other projects store, and seeing the account total
# would need an account-level API token, which this service must not hold. Watch the
# account total outside the service. On a paid plan, going over the allowance is billed;
# it doesn't fail closed, so nothing here stops the account from exceeding it.
#
# JONA_R2_HARD_LIMIT_GB, JONA_R2_SOFT_LIMIT_GB and JONA_R2_USER_LIMIT_GB override the
# defaults. A value that doesn't parse, or isn't positive, keeps the default.
FULL_RESOLUTION_BYTES = 50 * 10**6
SOFT_LIMIT_BYTES = 2 * 10**9
HARD_LIMIT_BYTES = 3 * 10**9
USER_LIMIT_BYTES = 1 * 10**9
DOWNSAMPLE_FACTORS = (1, 2, 4)


def _limit_bytes(setting: str, default: int) -> int:
    try:
        gigabytes = float(os.environ.get(setting, "").strip())
    except ValueError:
        return default
    return int(gigabytes * 10**9) if gigabytes > 0 else default


def _gb(value: int) -> str:
    return f"{value / 10**9:g} GB"

POLL_SECONDS = 30
SWEEP_INTERVAL = timedelta(days=7)
# The sweep only cleans up after a delete that failed, so it normally finds none or a few.
# More than this many orphans at once (or more than SWEEP_MAX_SHARE of all copies, if that
# is larger) looks like the database, not the bucket, being wrong, so it deletes nothing.
SWEEP_MAX_DELETES = 5
SWEEP_MAX_SHARE = 0.2
# Incomplete multipart uploads older than this are aborted, daily. Copies are below
# rclone's 200 MB multipart cutoff, so this is a safety net, not routine work.
STALE_UPLOAD_AGE = "24h"
CLEANUP_INTERVAL = timedelta(days=1)
# How soon a copy refused for space is checked again.
REFUSED_RECHECK = timedelta(days=1)
SIDE_FILES = ("metadata.json", "visualizations.json")
RCLONE_TIMEOUT_SECONDS = 600
_ERROR_CHARS = 500


class StoreError(RuntimeError):
    """The store couldn't complete an operation. The copy is retried later."""


class Refused(Exception):
    """This result can't be copied within the limits. It stays saved locally.

    `recheck` is true when the limit is shared space, which may free up later; a result
    that is simply too large is not tried again.
    """

    def __init__(self, reason: str, recheck: bool = False):
        super().__init__(reason)
        self.recheck = recheck


# ── Stores ────────────────────────────────────────────────────────────

class MemoryStore:
    """A store in memory, for tests."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}

    def put(self, local: Path, key: str) -> None:
        self.objects[key] = Path(local).read_bytes()

    def get(self, key: str, local: Path) -> bool:
        if key not in self.objects:
            return False
        Path(local).write_bytes(self.objects[key])
        return True

    def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self.objects if k.startswith(prefix)]:
            del self.objects[key]

    def list_keys(self, prefix: str) -> List[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))

    def abort_stale_uploads(self) -> None:
        """Nothing to abort: a memory store has no multipart uploads."""


Runner = Callable[..., subprocess.CompletedProcess]

# rclone's exit codes for a missing directory and a missing file.
_RCLONE_NOT_FOUND = {3, 4}


class RcloneStore:
    """A store reached through the rclone binary.

    The remote is configured only through RCLONE_CONFIG_* variables given to each rclone
    process, so credentials are never written to a file. The process gets just PATH and
    HOME from the service's environment, not its other secrets.
    """

    def __init__(self, root: str, config: Dict[str, str], runner: Runner = subprocess.run):
        self.root = root.rstrip("/")
        self._config = config
        self._runner = runner

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = {name: os.environ[name] for name in ("PATH", "HOME") if name in os.environ}
        env.update(self._config)
        try:
            return self._runner(
                ["rclone", "-q", *args], env=env, capture_output=True, text=True,
                timeout=RCLONE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StoreError(f"rclone {args[0]}: {exc}") from exc

    def _remote(self, key: str) -> str:
        return f"{self.root}/{key}"

    @staticmethod
    def _fail(args: Tuple[str, ...], result: subprocess.CompletedProcess) -> StoreError:
        detail = (result.stderr or "").strip()[-_ERROR_CHARS:]
        return StoreError(f"rclone {args[0]} exited {result.returncode}: {detail}")

    def put(self, local: Path, key: str) -> None:
        args = ("copyto", str(local), self._remote(key))
        result = self._run(*args)
        if result.returncode != 0:
            raise self._fail(args, result)

    def get(self, key: str, local: Path) -> bool:
        args = ("copyto", self._remote(key), str(local))
        result = self._run(*args)
        if result.returncode in _RCLONE_NOT_FOUND:
            return False
        if result.returncode != 0:
            raise self._fail(args, result)
        return True

    def delete_prefix(self, prefix: str) -> None:
        args = ("purge", self._remote(prefix.rstrip("/")))
        result = self._run(*args)
        if result.returncode not in (0, *_RCLONE_NOT_FOUND):
            raise self._fail(args, result)

    def list_keys(self, prefix: str) -> List[str]:
        args = ("lsf", "-R", "--files-only", self._remote(prefix.rstrip("/")))
        result = self._run(*args)
        if result.returncode in _RCLONE_NOT_FOUND:
            return []
        if result.returncode != 0:
            raise self._fail(args, result)
        base = prefix.rstrip("/") + "/"
        return sorted(base + line for line in result.stdout.splitlines() if line)

    def abort_stale_uploads(self) -> None:
        """Abort incomplete multipart uploads older than STALE_UPLOAD_AGE.

        Does what a bucket lifecycle rule would, through the S3 interface with the app's own
        token, so the service needs no account-level API access.
        """
        if self._config.get("RCLONE_CONFIG_JONA_TYPE") == "local":
            return  # a folder has no multipart uploads
        args = ("backend", "cleanup", self.root, "-o", f"max-age={STALE_UPLOAD_AGE}")
        result = self._run(*args)
        if result.returncode != 0:
            raise self._fail(args, result)


# ── Configuration ─────────────────────────────────────────────────────

R2_SETTINGS = ("JONA_R2_ENDPOINT", "JONA_R2_BUCKET", "JONA_R2_ACCESS_KEY_ID", "JONA_R2_SECRET_ACCESS_KEY")

_store_lock = threading.Lock()
_store_loaded = False
_store = None


def r2_settings_problem(endpoint: str, bucket: str) -> Optional[str]:
    """Why these R2 settings can't work, or None. Never quotes the values.

    The endpoint is the account endpoint alone. Pasting the dashboard's bucket URL makes
    every request name the bucket twice, which fails with not-found errors, not auth errors.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname:
        return "JONA_R2_ENDPOINT must be an https:// URL"
    if not parts.hostname.endswith(".r2.cloudflarestorage.com"):
        return "JONA_R2_ENDPOINT must be an <account_id>.r2.cloudflarestorage.com endpoint"
    if parts.path.strip("/") or parts.query or parts.fragment:
        return ("JONA_R2_ENDPOINT must not include a path; "
                "put the bucket name in JONA_R2_BUCKET instead")
    if "/" in bucket or ":" in bucket:
        return "JONA_R2_BUCKET must be a bucket name only"
    return None


def store_from_env():
    """The configured store, or None when copies are off. Logs why, never a value."""
    mode = os.environ.get("JONA_RESULT_COPIES", "").strip().lower()
    if mode in ("", "off", "0", "false", "no"):
        return None
    if mode not in ("r2", "directory"):
        logger.warning("Result copies off: JONA_RESULT_COPIES=%r is not r2 or directory", mode)
        return None
    if not shutil.which("rclone"):
        logger.warning("Result copies off: rclone is not installed")
        return None
    if mode == "directory":
        folder = os.environ.get("JONA_RESULT_COPIES_DIR", "").strip()
        if not folder:
            logger.warning("Result copies off: JONA_RESULT_COPIES_DIR is not set")
            return None
        logger.info("Result copies on: directory")
        return RcloneStore(f"jona:{folder}", {"RCLONE_CONFIG_JONA_TYPE": "local"})

    missing = [name for name in R2_SETTINGS if not os.environ.get(name, "").strip()]
    if missing:
        logger.warning("Result copies off: %s not set", ", ".join(missing))
        return None
    problem = r2_settings_problem(os.environ["JONA_R2_ENDPOINT"].strip(), os.environ["JONA_R2_BUCKET"].strip())
    if problem:
        logger.warning("Result copies off: %s", problem)
        return None
    config = {
        "RCLONE_CONFIG_JONA_TYPE": "s3",
        "RCLONE_CONFIG_JONA_PROVIDER": "Cloudflare",
        "RCLONE_CONFIG_JONA_ENDPOINT": os.environ["JONA_R2_ENDPOINT"].strip(),
        "RCLONE_CONFIG_JONA_ACCESS_KEY_ID": os.environ["JONA_R2_ACCESS_KEY_ID"].strip(),
        "RCLONE_CONFIG_JONA_SECRET_ACCESS_KEY": os.environ["JONA_R2_SECRET_ACCESS_KEY"].strip(),
        "RCLONE_CONFIG_JONA_REGION": "auto",
        # The token is meant to be scoped to one bucket, which can't create or check buckets.
        "RCLONE_CONFIG_JONA_NO_CHECK_BUCKET": "true",
    }
    logger.info("Result copies on: r2")
    return RcloneStore(f"jona:{os.environ['JONA_R2_BUCKET'].strip()}", config)


def get_store():
    """The store, read from the environment once."""
    global _store, _store_loaded
    with _store_lock:
        if not _store_loaded:
            _store = store_from_env()
            _store_loaded = True
        return _store


def set_store(store) -> None:
    """Replace the store (tests), or pass None to turn copies off."""
    global _store, _store_loaded
    with _store_lock:
        _store, _store_loaded = store, True


def enabled() -> bool:
    return get_store() is not None


# ── Keys and files ────────────────────────────────────────────────────

def prefix_for(save_id: str, user_id: Optional[str]) -> str:
    from .result_access import user_folder_name

    return f"results/{user_folder_name(user_id)}/{save_id}/"


def _is_geotiff(path: Path) -> bool:
    return path.suffix.lower() in (".tif", ".tiff")


def _write_cog(source: Path, target: Path, factor: int) -> None:
    """Write `source` as a compressed Cloud-Optimized GeoTIFF, `factor` times coarser."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.shutil import copy as rio_copy

    options = dict(driver="COG", COMPRESS="DEFLATE", PREDICTOR="YES", BLOCKSIZE=512,
                   OVERVIEWS="AUTO", BIGTIFF="IF_SAFER")
    if factor == 1:
        rio_copy(str(source), str(target), **options)
        return
    with rasterio.open(source) as src:
        width = max(1, src.width // factor)
        height = max(1, src.height // factor)
        data = src.read(out_shape=(src.count, height, width), resampling=Resampling.average)
        transform = src.transform * src.transform.scale(src.width / width, src.height / height)
        profile = src.profile.copy()
        profile.update(driver="GTiff", width=width, height=height, transform=transform)
        for key in ("blockxsize", "blockysize", "tiled", "compress", "interleave"):
            profile.pop(key, None)
        staged = target.with_suffix(".staged.tif")
        with rasterio.open(staged, "w", **profile) as dst:
            dst.write(data)
            dst.update_tags(**src.tags())
    try:
        rio_copy(str(staged), str(target), **options)
    finally:
        staged.unlink(missing_ok=True)


def prepare_result(source: Path, workdir: Path) -> Tuple[Path, int]:
    """The file to upload for a result, and how many times coarser it is.

    Raises Refused when it can't be brought under FULL_RESOLUTION_BYTES.
    """
    if not _is_geotiff(source):
        if source.stat().st_size > FULL_RESOLUTION_BYTES:
            raise Refused("larger than 50 MB and not a GeoTIFF, so it can't be stored coarser")
        return source, 1
    target = workdir / source.name
    for factor in DOWNSAMPLE_FACTORS:
        _write_cog(source, target, factor)
        if target.stat().st_size <= FULL_RESOLUTION_BYTES:
            return target, factor
    raise Refused(f"larger than 50 MB even {DOWNSAMPLE_FACTORS[-1]}x coarser")


# ── Rows ──────────────────────────────────────────────────────────────

def _session():
    from .job_archive import _get_session

    return _get_session()


def _move(row, target: str) -> None:
    RESULT_COPY.check(row.status, target)
    row.status = target
    row.updated_at = datetime.utcnow()


def queue(save_id: str, user_id: Optional[str]) -> None:
    """Ask for a saved result to be copied, or copied again after its files changed."""
    if not enabled():
        return
    from .models import ResultCopy

    session = _session()
    try:
        row = session.get(ResultCopy, save_id)
        if row is None:
            session.add(ResultCopy(save_id=save_id, user_id=user_id, status="pending"))
        elif row.status == "refused" and row.next_attempt_at is None:
            return  # too large to copy, and its result file never changes
        else:
            _move(row, "pending")
            row.attempts = 0
            row.next_attempt_at = None
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("Couldn't queue the copy of %s: %s", save_id, exc)
        return
    finally:
        session.close()
    _nudge()


def forget(save_id: str, user_id: Optional[str]) -> None:
    """Remove a deleted result's remote copy. The weekly sweep catches any that fail."""
    store = get_store()
    if store is None:
        return

    def _delete() -> None:
        try:
            store.delete_prefix(prefix_for(save_id, user_id))
        except StoreError as exc:
            logger.warning("Couldn't delete the copy of %s, the sweep will: %s", save_id, exc)

    threading.Thread(target=_delete, name=f"result-copy-delete-{save_id}", daemon=True).start()


def copied(save_ids: List[str]) -> set:
    """Which of these saved results have a copy offsite, from the copy rows alone.

    A copy exists once one was made (synced_at is set); a later refresh in progress or a
    failed refresh doesn't remove it. Empty when copies are off.
    """
    if not save_ids or not enabled():
        return set()
    from sqlalchemy import select

    from .models import ResultCopy

    try:
        session = _session()
        try:
            return set(session.execute(
                select(ResultCopy.save_id)
                .where(ResultCopy.save_id.in_(list(save_ids)))
                .where(ResultCopy.synced_at.isnot(None))
            ).scalars())
        finally:
            session.close()
    except Exception as exc:
        logger.warning("Couldn't read which results have copies: %s", exc)
        return set()


def status_of(save_id: str) -> Optional[Dict[str, object]]:
    """The copy's state for a saved result's detail, or None when copies are off."""
    if not enabled():
        return None
    from .models import ResultCopy

    try:
        session = _session()
        try:
            row = session.get(ResultCopy, save_id)
            return row.to_dict() if row else {"status": "pending"}
        finally:
            session.close()
    except Exception as exc:
        logger.debug("Copy status for %s unavailable: %s", save_id, exc)
        return None


def _stored_bytes(session, user_id: Optional[str] = None, excluding: Optional[str] = None) -> int:
    from sqlalchemy import func, select

    from .models import ResultCopy

    # Every copy that has reached the bucket, whatever its status now: a copy queued again,
    # or whose refresh failed, still holds its objects there.
    stmt = select(func.coalesce(func.sum(ResultCopy.bytes), 0)).where(ResultCopy.synced_at.isnot(None))
    if user_id is not None:
        stmt = stmt.where(ResultCopy.user_id == user_id)
    if excluding is not None:
        stmt = stmt.where(ResultCopy.save_id != excluding)
    return int(session.execute(stmt).scalar() or 0)


def _check_budget(session, save_id: str, user_id: Optional[str], size: int) -> None:
    """Refuse a copy of `size` bytes that would pass the hard or per-user limit."""
    hard = _limit_bytes("JONA_R2_HARD_LIMIT_GB", HARD_LIMIT_BYTES)
    soft = _limit_bytes("JONA_R2_SOFT_LIMIT_GB", SOFT_LIMIT_BYTES)
    per_user = _limit_bytes("JONA_R2_USER_LIMIT_GB", USER_LIMIT_BYTES)
    total = _stored_bytes(session, excluding=save_id) + size
    if total > hard:
        raise Refused(f"JonaAI's offsite copies have reached their {_gb(hard)} share", recheck=True)
    if user_id is not None and _stored_bytes(session, user_id, excluding=save_id) + size > per_user:
        raise Refused(f"this user's offsite copies have reached their {_gb(per_user)} limit", recheck=True)
    if total > soft:
        logger.warning(
            "Offsite copies use %.1f GB of JonaAI's %s share of the R2 account allowance "
            "(shared with the account; this count can't see other usage)",
            total / 10**9, _gb(hard),
        )


def sync_one(save_id: str) -> Optional[str]:
    """Copy one saved result. Returns its new status, or None if there was nothing to do.

    The row is read, the slow work runs with no database connection held, and the
    outcome is written only if the row's version hasn't changed meanwhile. A bundle
    queued during the upload leaves the row pending, so it runs again; a result deleted
    during the upload has whatever was just uploaded deleted too.
    """
    store = get_store()
    if store is None:
        return None
    from .models import ResultCopy, SavedResult

    session = _session()
    try:
        row = session.get(ResultCopy, save_id)
        saved = session.query(SavedResult).filter(SavedResult.save_id == save_id).one_or_none()
        if row is None or saved is None:
            return None
        version = row.version
        # A result's file never changes after saving, so a copy made once only needs its
        # side files refreshed.
        refresh_only = row.synced_at is not None
        known_size, known_factor = row.bytes, row.downsample
        prefix = prefix_for(save_id, saved.user_id)
        result = Path(saved.result_path)
        user_id = saved.user_id
        if not refresh_only:
            try:
                # Checked before converting, so a full budget costs no CPU.
                _check_budget(session, save_id, user_id, 0)
            except Refused as exc:
                return _finish(save_id, version, prefix, ("refused", exc))
    finally:
        session.close()

    try:
        if not result.exists():
            if refresh_only:
                # The copy is intact and there is nothing local to refresh it from.
                return _finish(save_id, version, prefix, ("synced", known_size, known_factor, False))
            raise Refused("the local result file is missing, so there is nothing to copy")
        side = [result.parent / name for name in SIDE_FILES if (result.parent / name).exists()]
        # Staged on disk beside the working results, not in the saved folder or in /tmp.
        staging = result_root() / ".copies"
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(staging)) as workdir:
            if refresh_only:
                upload, factor, size = None, known_factor, known_size
            else:
                upload, factor = prepare_result(result, Path(workdir))
                size = upload.stat().st_size + sum(path.stat().st_size for path in side)
                check = _session()
                try:
                    _check_budget(check, save_id, user_id, size)
                finally:
                    check.close()
            if upload is not None:
                store.put(upload, prefix + result.name)
            for path in side:
                store.put(path, prefix + path.name)
    except Refused as exc:
        return _finish(save_id, version, prefix, ("refused", exc))
    except Exception as exc:  # noqa: BLE001 — any failure is retried with backoff
        return _finish(save_id, version, prefix, ("failed", exc))
    return _finish(save_id, version, prefix, ("synced", size, factor, True))


def _finish(save_id: str, version: int, prefix: str, outcome: tuple) -> Optional[str]:
    """Write a copy's outcome if its row is still the version that was copied."""
    from sqlalchemy.orm.exc import StaleDataError

    from .models import ResultCopy, SavedResult

    session = _session()
    try:
        row = session.get(ResultCopy, save_id)
        still_saved = session.query(SavedResult.save_id).filter(SavedResult.save_id == save_id).first()
        if row is None or still_saved is None:
            # The saved result was deleted while it was being copied. Postgres cascades the
            # row away; elsewhere it is removed here.
            if row is not None:
                session.delete(row)
                session.commit()
            _delete_quietly(prefix)
            return None
        if row.version != version:
            logger.info("Copy of %s was queued again while running; it runs again", save_id)
            return row.status
        kind = outcome[0]
        if kind == "synced":
            _, size, factor, uploaded = outcome
            _move(row, "synced")
            row.attempts, row.last_error, row.next_attempt_at = 0, None, None
            if uploaded:
                row.bytes, row.downsample = size, factor
                row.synced_at = datetime.utcnow()
        elif kind == "refused":
            exc = outcome[1]
            _move(row, "refused")
            row.last_error = f"Not backed up: {exc}"
            row.next_attempt_at = datetime.utcnow() + REFUSED_RECHECK if exc.recheck else None
            logger.info("Copy of %s refused: %s", save_id, exc)
        else:
            from openeo_ai.intelligence.repository import delivery_retry_delay

            exc = outcome[1]
            _move(row, "failed")
            row.attempts += 1
            row.last_error = str(exc)[-_ERROR_CHARS:]
            row.next_attempt_at = datetime.utcnow() + delivery_retry_delay(row.attempts)
            logger.warning("Copy of %s failed (attempt %d): %s", save_id, row.attempts, exc)
        try:
            session.commit()
        except StaleDataError:
            # Queued again or deleted between the read above and this write.
            session.rollback()
            logger.info("Copy of %s changed while finishing; it runs again", save_id)
            return None
        return row.status
    finally:
        session.close()


def _delete_quietly(prefix: str) -> None:
    store = get_store()
    if store is None:
        return
    try:
        store.delete_prefix(prefix)
    except StoreError as exc:
        logger.warning("Couldn't delete %s, the sweep will: %s", prefix, exc)


def restore(saved) -> bool:
    """Bring a saved result's files back from its copy when the local file is missing."""
    store = get_store()
    if store is None:
        return False
    result = Path(saved.result_path)
    with _restore_lock(saved.save_id):
        if result.exists():
            return True
        from .models import ResultCopy

        session = _session()
        try:
            row = session.get(ResultCopy, saved.save_id)
            downsample = row.downsample if row and row.synced_at else None
        finally:
            session.close()
        if downsample is None:
            return False
        prefix = prefix_for(saved.save_id, saved.user_id)
        result.parent.mkdir(parents=True, exist_ok=True)
        staged = result.with_name(result.name + ".restoring")
        try:
            if not store.get(prefix + result.name, staged):
                logger.warning("Copy of %s is missing from the bucket", saved.save_id)
                _forget_missing_copy(saved.save_id)
                return False
            for name in SIDE_FILES:
                store.get(prefix + name, result.parent / name)
            staged.replace(result)
        except StoreError as exc:
            logger.warning("Couldn't restore %s: %s", saved.save_id, exc)
            return False
        finally:
            staged.unlink(missing_ok=True)
        if downsample > 1:
            _note_downsample(result.parent / "metadata.json", downsample)
        logger.info("Restored %s from its offsite copy", saved.save_id)
        return True


def _forget_missing_copy(save_id: str) -> None:
    """Stop claiming a copy the bucket doesn't have, so the list stops offering a restore.

    The row goes back to pending with no copy recorded. The next sync finds no local
    file either and refuses it for good, with the reason.
    """
    from sqlalchemy.orm.exc import StaleDataError

    from .models import ResultCopy

    session = _session()
    try:
        row = session.get(ResultCopy, save_id)
        if row is None:
            return
        _move(row, "pending")
        row.synced_at, row.bytes, row.next_attempt_at = None, 0, None
        row.last_error = "The offsite copy was missing when a restore was tried."
        session.commit()
    except StaleDataError:
        session.rollback()  # changed meanwhile; the next restore attempt checks again
    finally:
        session.close()


def _note_downsample(metadata_path: Path, factor: int) -> None:
    try:
        metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    except (OSError, ValueError):
        metadata = {}
    metadata["restored_copy"] = {
        "downsample": factor,
        "note": f"Restored from the offsite copy, which is {factor}x coarser than the original.",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2))


_restore_locks: Dict[str, threading.Lock] = {}
_restore_locks_guard = threading.Lock()


def _restore_lock(save_id: str) -> threading.Lock:
    with _restore_locks_guard:
        return _restore_locks.setdefault(save_id, threading.Lock())


# ── The sync loop ─────────────────────────────────────────────────────

def backfill(limit: int = 100) -> int:
    """Queue saved results that have no copy row, such as those saved before copies were on."""
    from sqlalchemy import select

    from .models import ResultCopy, SavedResult

    session = _session()
    try:
        missing = session.execute(
            select(SavedResult.save_id, SavedResult.user_id)
            .outerjoin(ResultCopy, ResultCopy.save_id == SavedResult.save_id)
            .where(ResultCopy.save_id.is_(None))
            .limit(limit)
        ).all()
        for save_id, user_id in missing:
            session.add(ResultCopy(save_id=save_id, user_id=user_id, status="pending"))
        session.commit()
        return len(missing)
    finally:
        session.close()


def due(limit: int = 5) -> List[str]:
    """Copies to attempt now: pending ones, and failed or refused ones whose wait is over."""
    from sqlalchemy import and_, or_, select

    from .models import ResultCopy

    now = datetime.utcnow()
    session = _session()
    try:
        return list(session.execute(
            select(ResultCopy.save_id)
            .where(or_(
                ResultCopy.status == "pending",
                and_(ResultCopy.status.in_(("failed", "refused")), ResultCopy.next_attempt_at <= now),
            ))
            .order_by(ResultCopy.created_at)
            .limit(limit)
        ).scalars())
    finally:
        session.close()


def sweep(allow_mass_delete: bool = False) -> int:
    """Delete remote copies whose saved result no longer exists. Returns how many.

    Refuses, deleting nothing, when it would remove every copy or more than the mass-delete
    limit. Started against an empty or wrong database (a mistyped POSTGRES_DB, a database
    restored empty, a dev machine holding the production R2 settings), every copy would
    look orphaned, and they are what a restore would then need. A person who has checked
    the database can call sweep(allow_mass_delete=True).
    """
    store = get_store()
    if store is None:
        return 0
    from sqlalchemy import select

    from .models import SavedResult

    prefixes: Dict[str, str] = {}
    for key in store.list_keys("results/"):
        parts = key.split("/")
        if len(parts) >= 4:
            prefixes.setdefault(parts[2], "/".join(parts[:3]) + "/")
    if not prefixes:
        return 0
    session = _session()
    try:
        kept = set(session.execute(
            select(SavedResult.save_id).where(SavedResult.save_id.in_(list(prefixes)))
        ).scalars())
    finally:
        session.close()
    orphans = [prefix for save_id, prefix in prefixes.items() if save_id not in kept]
    limit = max(SWEEP_MAX_DELETES, int(len(prefixes) * SWEEP_MAX_SHARE))
    if orphans and not allow_mass_delete and (len(orphans) == len(prefixes) or len(orphans) > limit):
        logger.warning(
            "Sweep refused: %d of %d offsite copies have no saved result, which looks like "
            "the database is wrong, not the bucket. Nothing deleted. Check the database, "
            "then run sweep(allow_mass_delete=True) if they really are orphans.",
            len(orphans), len(prefixes),
        )
        return 0
    for prefix in orphans:
        store.delete_prefix(prefix)
    if orphans:
        logger.info("Swept %d orphaned offsite copies", len(orphans))
    return len(orphans)


_wake_event = threading.Event()


def _nudge() -> None:
    _wake_event.set()


class CopySync:
    """Runs on its own thread: queues, uploads and sweeps until stopped."""

    def __init__(self, poll_seconds: float = POLL_SECONDS):
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._last_sweep: Optional[datetime] = None
        self._last_cleanup: Optional[datetime] = None

    def tick(self) -> None:
        backfill()
        for save_id in due():
            if self._stop.is_set():
                return
            sync_one(save_id)
        now = datetime.utcnow()
        if self._last_sweep is None or now - self._last_sweep >= SWEEP_INTERVAL:
            self._last_sweep = now
            sweep()
        if self._last_cleanup is None or now - self._last_cleanup >= CLEANUP_INTERVAL:
            self._last_cleanup = now
            store = get_store()
            if store is not None:
                try:
                    store.abort_stale_uploads()
                except StoreError as exc:
                    logger.warning("Couldn't abort stale uploads: %s", exc)

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 — keep the loop alive; retried next tick
                logger.warning("Result copy sync tick failed: %s", exc)
            _wake_event.wait(self.poll_seconds)
            _wake_event.clear()

    def stop(self) -> None:
        self._stop.set()
        _wake_event.set()


def start_sync() -> Optional[Tuple[CopySync, threading.Thread]]:
    """Start the sync thread when copies are on. Returns None when they are off."""
    if not enabled():
        return None
    sync = CopySync()
    thread = threading.Thread(target=sync.run_forever, name="result-copy-sync", daemon=True)
    thread.start()
    return sync, thread
