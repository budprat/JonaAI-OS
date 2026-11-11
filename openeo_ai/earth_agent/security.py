"""Security and runtime guardrails for Earth-Agent integration."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, MutableMapping, Sequence

from openeo_app.core.paths import LEGACY_RESULT_ROOT, result_root


class EarthAgentSecurityError(ValueError):
    """Raised when an Earth-Agent integration request violates policy."""


def _normalize_base_dirs(base_dirs: Iterable[Path | str]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for base_dir in base_dirs:
        candidate = Path(base_dir).expanduser().resolve(strict=False)
        if candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def _default_allowed_base_dirs() -> tuple[Path, ...]:
    configured = os.getenv("EARTH_AGENT_ALLOWED_BASE_DIRS")
    if configured:
        return _normalize_base_dirs(
            part for part in configured.split(os.pathsep) if part.strip()
        )
    return _normalize_base_dirs(
        (
            result_root(),
            LEGACY_RESULT_ROOT,
            Path("/tmp/ea_results"),
            Path.home() / ".openeo_jobs",
        )
    )


def _walk_existing_components(path: Path) -> Iterator[Path]:
    parts = path.parts[1:] if path.is_absolute() else path.parts
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    for part in parts:
        current = current / part
        if current.exists():
            yield current


class PathValidator:
    """Validate filesystem paths used by Earth-Agent subprocesses."""

    ALLOWED_EXTENSIONS = {".tif", ".tiff", ".json", ".csv", ".nc", ".geojson", ".png"}

    def __init__(
        self,
        allowed_base_dirs: Sequence[Path | str] | None = None,
        allowed_extensions: Sequence[str] | None = None,
    ) -> None:
        self.allowed_base_dirs = _normalize_base_dirs(
            allowed_base_dirs or _default_allowed_base_dirs()
        )
        self.allowed_extensions = {
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (allowed_extensions or self.ALLOWED_EXTENSIONS)
        }

    def validate_input_file(self, path: str | Path) -> Path:
        candidate = self._coerce_path(path)
        if not candidate.exists():
            raise EarthAgentSecurityError(f"Input file does not exist: {candidate}")
        self._check_extension(candidate)
        # The symlink policy has to be applied to the path AS GIVEN. resolve()
        # eliminates every symlink component, so calling this on the resolved path
        # could never fire — it was a check that looked like a defence and was not.
        # validate_directory already enforces this on an unresolved path; inputs here
        # arrive as model-controlled strings (mcp_client.py:303-305), so they get at
        # least the same treatment.
        self._check_existing_symlinks(candidate)
        # Containment and type are checked AFTER resolution. That ordering is the
        # TOCTOU defence the original comment described, and it is kept: resolve()
        # decides which file is actually opened, so a swap between check and resolve
        # cannot widen either of these two.
        resolved = candidate.resolve(strict=True)
        self._ensure_allowed_base(resolved)
        # No second _check_extension(resolved) here on purpose. It would be dead: the
        # only way the resolved suffix can differ from the candidate's is a symlinked
        # final component, which the check above now refuses, and the _OS_SYMLINKS
        # exemptions (/tmp, /var) are directory components that cannot change a suffix.
        # Adding it would repeat the mistake this fix exists to correct. The extension
        # bypass stays closed BY the symlink refusal — test_a_tif_symlink_cannot_smuggle
        # _a_blocked_file_type pins that, so relaxing the symlink policy without
        # re-validating the resolved type will fail loudly.
        if not resolved.is_file():
            raise EarthAgentSecurityError(f"Input path is not a file: {resolved}")
        return resolved

    def validate_output_file(self, path: str | Path) -> Path:
        candidate = self._coerce_path(path)
        self._check_extension(candidate)
        self._check_existing_symlinks(candidate.parent)
        resolved = candidate.resolve(strict=False)
        self._ensure_allowed_base(resolved)
        return resolved

    def validate_directory(self, path: str | Path) -> Path:
        candidate = self._coerce_path(path)
        self._check_existing_symlinks(candidate)
        resolved = candidate.resolve(strict=False)
        self._ensure_allowed_base(resolved)
        return resolved

    def validate_relative_output_path(self, path: str | Path) -> Path:
        candidate = Path(path)
        raw = str(candidate)
        if "\x00" in raw:
            raise EarthAgentSecurityError("Embedded NUL byte in path")
        if candidate.is_absolute():
            raise EarthAgentSecurityError("Output paths must be relative to temp_dir")
        if candidate.anchor or ".." in candidate.parts:
            raise EarthAgentSecurityError(f"Blocked path traversal: {candidate}")
        self._check_extension(candidate)
        return candidate

    def _coerce_path(self, path: str | Path) -> Path:
        raw = str(path)
        if "\x00" in raw:
            raise EarthAgentSecurityError("Embedded NUL byte in path")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate

    def _check_extension(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix not in self.allowed_extensions:
            raise EarthAgentSecurityError(f"Blocked file type: {path.suffix or '<none>'}")

    # Well-known OS-level symlinks that are safe (e.g., macOS /tmp -> /private/tmp)
    # Well-known OS-level symlinks (macOS /tmp -> /private/tmp, /var -> /private/var)
    # /etc removed: no EA workflow should traverse /etc
    _OS_SYMLINKS = frozenset({Path("/tmp"), Path("/var")})

    def _check_existing_symlinks(self, path: Path) -> None:
        for component in _walk_existing_components(path):
            if component.is_symlink() and component not in self._OS_SYMLINKS:
                raise EarthAgentSecurityError(f"Blocked symlinked path component: {component}")

    def _ensure_allowed_base(self, resolved_path: Path) -> None:
        for base_dir in self.allowed_base_dirs:
            try:
                resolved_path.relative_to(base_dir)
                return
            except ValueError:
                continue
        raise EarthAgentSecurityError(f"Path escapes allowed roots: {resolved_path}")


class EnvironmentScrubber:
    """Remove unrelated and secret environment variables before subprocess launch."""

    SAFE_VARS = {
        "CONDA_PREFIX",
        "GDAL_DATA",
        "GTIFF_SRS_SOURCE",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PROJ_LIB",
        "PYTHONUNBUFFERED",
        "SYSTEMROOT",
        "TMPDIR",
    }

    @classmethod
    def clean_env(
        cls,
        source: Mapping[str, str] | None = None,
        extra_vars: Iterable[str] = (),
        overrides: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        allowed = set(cls.SAFE_VARS)
        allowed.update(extra_vars)
        source_env = dict(source or os.environ)
        cleaned = {key: value for key, value in source_env.items() if key in allowed}
        if overrides:
            cleaned.update({key: value for key, value in overrides.items() if value is not None})
        cleaned.setdefault("PYTHONUNBUFFERED", "1")
        return cleaned


class EarthAgentRateLimiter:
    """In-memory sliding-window rate limiter."""

    def __init__(self, max_calls: int = 10, window_seconds: int = 60) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._history: MutableMapping[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str = "default", now: float | None = None) -> bool:
        timestamp = now if now is not None else time.time()
        history = self._history[key]
        cutoff = timestamp - self.window_seconds
        while history and history[0] <= cutoff:
            history.popleft()
        if len(history) >= self.max_calls:
            return False
        history.append(timestamp)
        return True

    def check(self, key: str = "default", now: float | None = None) -> None:
        if not self.allow(key=key, now=now):
            raise EarthAgentSecurityError(
                f"Rate limit exceeded for {key}: max {self.max_calls}/{self.window_seconds}s"
            )


class SubprocessGate:
    """Concurrency limiter shared by Earth-Agent subprocess consumers."""

    def __init__(self, max_concurrency: int = 5) -> None:
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

    @asynccontextmanager
    async def acquire(self):
        async with self._semaphore:
            yield


@dataclass(slots=True)
class TempDirManager:
    """Manage per-call temp workspaces with TTL and quota enforcement."""

    root_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("EARTH_AGENT_TEMP_ROOT", "/tmp/ea_results")
        ).resolve()
    )
    ttl_seconds: int = 3600
    quota_bytes: int = 10 * 1024 * 1024 * 1024

    def create(self, prefix: str = "call") -> Path:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup()
        path = self.root_dir / f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup(self, now: float | None = None) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        current_time = now if now is not None else time.time()

        expired: list[Path] = []
        candidates: list[tuple[Path, float]] = []
        for child in self.root_dir.iterdir():
            try:
                stat = child.stat()
            except FileNotFoundError:
                continue
            mtime = stat.st_mtime
            candidates.append((child, mtime))
            if current_time - mtime > self.ttl_seconds:
                expired.append(child)

        for child in expired:
            self._remove(child)

        total_size = self.total_size_bytes()
        if total_size <= self.quota_bytes:
            return

        for child, _ in sorted(candidates, key=lambda item: item[1]):
            if not child.exists():
                continue
            self._remove(child)
            total_size = self.total_size_bytes()
            if total_size <= self.quota_bytes:
                break

    def total_size_bytes(self) -> int:
        total = 0
        if not self.root_dir.exists():
            return 0
        for path in self.root_dir.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    def _remove(self, path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
