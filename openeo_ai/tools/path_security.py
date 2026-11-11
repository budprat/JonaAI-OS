# ABOUTME: Centralized path validation for all AI tool handlers.
# Reuses _validate_path() logic from web_routes.py with extension and size checks.

"""
Centralized path security for tool handlers.

All tool functions that accept file_path arguments must validate
paths through these functions before any file I/O. This prevents
path traversal, symlink attacks, and unauthorized file access.
"""

import os
import logging
from pathlib import Path
from typing import Optional, Sequence

from openeo_app.core.paths import result_root

logger = logging.getLogger(__name__)

# Allowed directories for tool file access (resolved once at import time)
_ALLOWED_INPUT_DIRS = [
    result_root().resolve(),
    Path("/tmp").resolve(),
    Path.home().resolve() / ".openeo_jobs",
    Path.cwd().resolve(),
]

# Output files are restricted to this single directory
_ALLOWED_OUTPUT_DIR = result_root().resolve()

# Allowed file extensions for input files
_ALLOWED_INPUT_EXTENSIONS = {
    ".tif", ".tiff", ".json", ".csv", ".nc", ".geojson", ".png",
}

# Allowed file extensions for output files
_ALLOWED_OUTPUT_EXTENSIONS = {
    ".tif", ".tiff", ".json", ".csv", ".png", ".html", ".geojson",
}

# Maximum file size for input files (1 GB — our own job outputs can be large)
_MAX_INPUT_FILE_SIZE = 1024 * 1024 * 1024


class PathSecurityError(ValueError):
    """Raised when a file path fails security validation."""
    pass


def current_user_output_dir() -> str:
    """The current user's output folder, so exports stay readable only by them."""
    from ..sdk.user_context import get_current_user_id
    from ..storage.result_access import user_output_dir

    return str(user_output_dir(get_current_user_id()))


def _is_under(path: Path, base: Path) -> bool:
    """Check if *path* is equal to or a child of *base* using Path.relative_to."""
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _check_symlinks(path: Path) -> None:
    """Check that no component of the path is a symlink."""
    if path.is_symlink():
        raise PathSecurityError(f"Symlink not allowed: {path}")

    # Walk each component of the resolved path for hidden symlinks
    check = Path(path.anchor)
    for part in path.relative_to(path.anchor).parts:
        check = check / part
        if check.is_symlink():
            raise PathSecurityError(f"Symlink in path component: {check}")


def validate_tool_input_path(
    raw_path: str,
    allowed_dirs: Optional[Sequence[Path]] = None,
    allowed_extensions: Optional[set] = None,
    check_exists: bool = True,
    max_size: Optional[int] = None,
) -> str:
    """Validate a file path for reading by tool handlers.

    Checks:
    1. Path is not empty
    2. No symlinks in any path component
    3. Resolved path is under an allowed directory
    4. File extension is in the whitelist
    5. File exists and is not too large

    Args:
        raw_path: The untrusted path string from tool arguments.
        allowed_dirs: Override for allowed base directories.
        allowed_extensions: Override for allowed file extensions.
        check_exists: Whether to verify the file exists.
        max_size: Maximum file size in bytes.

    Returns:
        The validated, resolved path as a string.

    Raises:
        PathSecurityError: If the path fails any security check.
    """
    if not raw_path or not raw_path.strip():
        raise PathSecurityError("file_path parameter is required and cannot be empty")

    dirs = allowed_dirs or _ALLOWED_INPUT_DIRS
    extensions = allowed_extensions or _ALLOWED_INPUT_EXTENSIONS
    size_limit = max_size or _MAX_INPUT_FILE_SIZE

    p = Path(raw_path)

    # Check for null bytes
    if "\x00" in raw_path:
        raise PathSecurityError("Null byte in path")

    # Step 1: Reject if the raw path itself is a symlink (before resolve)
    if p.exists() and p.is_symlink():
        raise PathSecurityError(f"Symlink not allowed: {raw_path}")

    # Step 2: Resolve to canonical path
    resolved = p.resolve()

    # Step 3: Walk each component for hidden symlinks
    _check_symlinks(resolved)

    # Step 4: Ensure resolved path is under an allowed directory
    if not any(_is_under(resolved, d) for d in dirs):
        raise PathSecurityError(f"Path not in allowed directories: {raw_path}")

    # Step 5: Check file extension
    suffix = resolved.suffix.lower()
    if suffix not in extensions:
        raise PathSecurityError(
            f"File type not allowed: {suffix or '<none>'}. "
            f"Allowed: {', '.join(sorted(extensions))}"
        )

    # Step 6: Check existence and size
    if check_exists:
        if not resolved.exists():
            raise PathSecurityError(f"File not found: {raw_path}")
        if not resolved.is_file():
            raise PathSecurityError(f"Not a file: {raw_path}")
        file_size = resolved.stat().st_size
        if file_size > size_limit:
            raise PathSecurityError(
                f"File too large: {file_size} bytes (max: {size_limit})"
            )

    logger.debug(f"Path validated: {raw_path} -> {resolved}")
    return str(resolved)


def validate_tool_output_path(
    raw_path: str,
    allowed_dir: Optional[Path] = None,
    allowed_extensions: Optional[set] = None,
) -> str:
    """Validate and constrain an output file path for tool handlers.

    Forces output paths under the result root (or override).
    Creates the output directory if needed.

    Args:
        raw_path: The untrusted output path string.
        allowed_dir: Override for the allowed output directory.
        allowed_extensions: Override for allowed output extensions.

    Returns:
        The validated output path as a string.

    Raises:
        PathSecurityError: If the path fails validation.
    """
    if not raw_path or not raw_path.strip():
        raise PathSecurityError("output_path cannot be empty")

    out_dir = allowed_dir or _ALLOWED_OUTPUT_DIR
    extensions = allowed_extensions or _ALLOWED_OUTPUT_EXTENSIONS

    # Check for null bytes
    if "\x00" in raw_path:
        raise PathSecurityError("Null byte in path")

    p = Path(raw_path)
    resolved = p.resolve()

    # Ensure output is under allowed directory
    try:
        resolved.relative_to(out_dir)
    except ValueError:
        raise PathSecurityError(
            f"Output path must be under {out_dir}, got: {raw_path}"
        )

    # Check extension
    suffix = resolved.suffix.lower()
    if suffix not in extensions:
        raise PathSecurityError(
            f"Output file type not allowed: {suffix or '<none>'}. "
            f"Allowed: {', '.join(sorted(extensions))}"
        )

    # Check for symlinks in existing parent components
    parent = resolved.parent
    if parent.exists():
        _check_symlinks(parent)

    return str(resolved)
