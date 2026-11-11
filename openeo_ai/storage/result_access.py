# ABOUTME: Decides whether a user may read a result file or job. Shared by the raster
# ABOUTME: endpoints and agent tools, so both enforce one rule; it denies by default.
"""Per-user access to results.

A file is readable only if it traces to something the user owns: their OpenEO job,
a saved result they own or can read through a project, their evidence package, or
their output folder. Directory allow-listing alone let any signed-in user read
another user's rasters by path.
"""

import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from openeo_app.core.paths import LEGACY_RESULT_ROOT, result_root, user_outputs_root

logger = logging.getLogger(__name__)

USER_OUTPUT_ROOT = user_outputs_root()


def openeo_user_uuid(user_id: str) -> str:
    """The OpenEO job owner id for a login name; mirrors DevAuthenticator."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"dev-{user_id}"))


def user_folder_name(user_id: Optional[str]) -> str:
    """A filesystem-safe folder name for a user's outputs."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", user_id or "system")


def user_output_dir(user_id: Optional[str]) -> Path:
    return USER_OUTPUT_ROOT / user_folder_name(user_id)


def _result_roots() -> list:
    return list({LEGACY_RESULT_ROOT.resolve(), result_root().resolve()})


def _component_after(path: Path, base: Path) -> Optional[str]:
    """The first path component below base, or None if path isn't under it."""
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return None
    return parts[0] if len(parts) > 1 else None


def can_read_job(job_id: str, user_id: str) -> bool:
    """True if this user owns the OpenEO job. Unknown jobs and errors deny."""
    try:
        job_uuid = uuid.UUID(str(job_id))
        from openeo_fastapi.client.psql.models import JobORM

        from .job_archive import _get_session

        session = _get_session()
        try:
            job = session.get(JobORM, job_uuid)
            return bool(job) and str(job.user_id) == openeo_user_uuid(user_id)
        finally:
            session.close()
    except Exception as exc:
        logger.warning("Job access check failed, denying: %s", exc)
        return False


def can_read_saved_result(save_id: str, user_id: str) -> bool:
    """True if the user owns the saved result or can read it through a project."""
    try:
        from ..web_routes import _saved_job_owner_for_read

        return _saved_job_owner_for_read(save_id, user_id) is not None
    except Exception as exc:
        logger.warning("Saved result access check failed, denying: %s", exc)
        return False


def _in_user_evidence(path: Path, user_id: str) -> bool:
    """True if one of the user's evidence packages references this file."""
    try:
        from sqlalchemy import String, cast, select

        from .job_archive import _get_session
        from .models import EvidencePackage

        session = _get_session()
        try:
            query = (
                select(EvidencePackage.artifacts)
                .where(EvidencePackage.user_id == user_id)
                .where(cast(EvidencePackage.artifacts, String).contains(str(path), autoescape=True))
            )
            # The filter is only a shortlist: a path must be a whole value, not part of one.
            return any(str(path) in artifact_paths(row[0]) for row in session.execute(query))
        finally:
            session.close()
    except Exception as exc:
        logger.warning("Evidence access check failed, denying: %s", exc)
        return False


def artifact_paths(artifacts) -> set:
    """Every value in an evidence record that names a file, resolved."""
    found = set()
    pending = [artifacts]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
        elif isinstance(value, str) and value.startswith(("/", "./", "../", "~")):
            try:
                found.add(str(Path(value).resolve()))
            except (OSError, RuntimeError, ValueError):
                pass
    return found


def owns_saved_result(save_id: str, user_id: str) -> bool:
    """True only for the owner. Project members may read a saved result but not change it."""
    try:
        from sqlalchemy import select

        from .job_archive import _get_session
        from .models import SavedResult

        session = _get_session()
        try:
            owner = session.execute(select(SavedResult.user_id).where(SavedResult.save_id == save_id)).scalar()
            return owner is not None and owner == user_id
        finally:
            session.close()
    except Exception as exc:
        logger.warning("Saved result owner check failed, denying: %s", exc)
        return False


def can_read_path(raw_path: str, user_id: str) -> bool:
    """True if this file traces to something the user may read."""
    try:
        path = Path(raw_path).resolve()
    except (OSError, RuntimeError, ValueError):
        return False

    if _component_after(path, USER_OUTPUT_ROOT.resolve()) == user_folder_name(user_id):
        return True

    save_id = _component_after(path, (Path.home() / ".openeo_jobs" / "results").resolve())
    if save_id:
        return can_read_saved_result(save_id, user_id)

    for root in _result_roots():
        job_id = _component_after(path, root / "results")
        if job_id and can_read_job(job_id, user_id):
            return True

    return _in_user_evidence(path, user_id)
