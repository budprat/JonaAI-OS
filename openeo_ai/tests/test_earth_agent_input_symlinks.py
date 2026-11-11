# ABOUTME: P4-B3/B4 — validate_input_file checked symlinks on the already-resolved path,
# ABOUTME: so the check could never fire and a .tif symlink smuggled in any file type.
import shutil
import tempfile
from pathlib import Path

import pytest

from openeo_ai.earth_agent.security import EarthAgentSecurityError, PathValidator


@pytest.fixture()
def jail():
    """An isolated tree with its own allowed root. Never touches a real result root."""
    root = Path(tempfile.mkdtemp(prefix="ea_symlink_test_"))
    allowed = root / "allowed"
    allowed.mkdir()
    outside = root / "outside"
    outside.mkdir()
    try:
        yield root, allowed, outside
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_real_file_still_validates(jail):
    _, allowed, _ = jail
    data = allowed / "scene.tif"
    data.write_bytes(b"x")
    assert PathValidator(allowed_base_dirs=[allowed]).validate_input_file(data) == data.resolve()


def test_a_tif_symlink_cannot_smuggle_a_blocked_file_type(jail):
    """The hole. A direct .env was refused; a .tif symlink to it was RETURNED.

    Fixture content is dummy text — no real secret or credential file is involved.
    """
    _, allowed, _ = jail
    blocked = allowed / "config.env"
    blocked.write_text("DUMMY=not-a-real-secret\n")
    link = allowed / "innocent.tif"
    link.symlink_to(blocked)
    v = PathValidator(allowed_base_dirs=[allowed])

    with pytest.raises(EarthAgentSecurityError, match="Blocked file type"):
        v.validate_input_file(blocked)          # the honest path was always refused

    with pytest.raises(EarthAgentSecurityError):
        v.validate_input_file(link)             # ...and now the dishonest one is too

    # The returned path must never be the blocked file, however it was reached.
    try:
        got = v.validate_input_file(link)
    except EarthAgentSecurityError:
        got = None
    assert got is None or got.suffix.lower() != ".env"


def test_a_symlink_inside_the_allowed_root_is_refused_for_input(jail):
    """This is the case the original test could not reach.

    test_path_validator_blocks_symlink_escape pointed its symlink OUTSIDE the allowed
    root and matched "(symlink|escapes allowed roots)" — an alternation, so it passed on
    the containment branch and said nothing about the symlink check. A link that stays
    inside the root cannot be caught by containment, so only a working symlink check
    refuses it.
    """
    _, allowed, _ = jail
    data = allowed / "data.tif"
    data.write_bytes(b"x")
    link = allowed / "inside.tif"
    link.symlink_to(data)

    with pytest.raises(EarthAgentSecurityError, match="symlinked path component"):
        PathValidator(allowed_base_dirs=[allowed]).validate_input_file(link)


def test_the_escape_case_is_refused_for_the_symlink_reason_not_only_containment(jail):
    """Pins the specific cause, so the alternation blind spot cannot come back."""
    _, allowed, outside = jail
    target = outside / "secret.tif"
    target.write_bytes(b"s")
    link = allowed / "linked.tif"
    link.symlink_to(target)

    with pytest.raises(EarthAgentSecurityError, match="symlinked path component"):
        PathValidator(allowed_base_dirs=[allowed]).validate_input_file(link)


def test_a_symlinked_directory_component_is_refused(jail):
    _, allowed, outside = jail
    real_dir = outside / "real"
    real_dir.mkdir()
    scene = real_dir / "scene.tif"
    scene.write_bytes(b"x")
    linked_dir = allowed / "linked_dir"
    linked_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(EarthAgentSecurityError, match="symlinked path component"):
        PathValidator(allowed_base_dirs=[allowed]).validate_input_file(linked_dir / "scene.tif")


def test_input_validation_is_now_at_least_as_strict_as_directory_validation(jail):
    """The three validators disagreed; validate_input_file was the weak one."""
    _, allowed, _ = jail
    data = allowed / "data.tif"
    data.write_bytes(b"x")
    link = allowed / "inside.tif"
    link.symlink_to(data)
    v = PathValidator(allowed_base_dirs=[allowed])

    with pytest.raises(EarthAgentSecurityError):
        v.validate_directory(link)
    with pytest.raises(EarthAgentSecurityError):
        v.validate_input_file(link)


def test_a_nonexistent_input_still_reports_that_first(jail):
    """mcp_client.py:309 depends on this: a missing file is routed as an output."""
    _, allowed, _ = jail
    with pytest.raises(EarthAgentSecurityError, match="does not exist"):
        PathValidator(allowed_base_dirs=[allowed]).validate_input_file(allowed / "nope.tif")
