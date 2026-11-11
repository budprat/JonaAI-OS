"""Path and workspace mapping between JonaAI outputs and Earth-Agent."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from openeo_app.core.paths import result_root as default_result_root

from .security import PathValidator, TempDirManager


_RESULT_PATH_RE = re.compile(r"Result saved at (?P<path>/\S+)")


@dataclass(frozen=True, slots=True)
class EarthAgentCallContext:
    """Per-call workspace metadata."""

    call_id: str
    temp_dir: Path
    result_dir: Path


class EarthAgentPathResolver:
    """Create per-call workspaces and map outputs back into JonaAI storage."""

    def __init__(
        self,
        path_validator: PathValidator | None = None,
        temp_dir_manager: TempDirManager | None = None,
        result_root: Path | str | None = None,
    ) -> None:
        self.temp_dir_manager = temp_dir_manager or TempDirManager()
        self.result_root = Path(result_root or default_result_root()).expanduser().resolve(strict=False)
        self.path_validator = path_validator or PathValidator(
            allowed_base_dirs=(
                self.temp_dir_manager.root_dir,
                self.result_root,
                Path.home() / ".openeo_jobs",  # Saved results archive
            )
        )

    def resolve_input_files(self, input_files: Iterable[str | Path]) -> list[Path]:
        return [self.path_validator.validate_input_file(path) for path in input_files]

    def create_call_context(self, prefix: str = "earth-agent", job_id: str | None = None) -> EarthAgentCallContext:
        temp_dir = self.temp_dir_manager.create(prefix=prefix)
        call_id = temp_dir.name
        result_dir = (
            self.result_root / job_id / "earth_agent" / call_id
            if job_id
            else temp_dir
        )
        result_dir.mkdir(parents=True, exist_ok=True)
        return EarthAgentCallContext(call_id=call_id, temp_dir=temp_dir, result_dir=result_dir)

    def build_output_argument(self, output_path: str | Path, call_context: EarthAgentCallContext) -> str:
        relative = self.path_validator.validate_relative_output_path(output_path)
        return str(Path(call_context.call_id) / relative)

    def collect_output_files(self, call_context: EarthAgentCallContext) -> list[Path]:
        if not call_context.temp_dir.exists():
            return []
        files: list[Path] = []
        for path in sorted(call_context.temp_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in self.path_validator.allowed_extensions:
                files.append(path)
        return files

    def materialize_outputs(self, call_context: EarthAgentCallContext) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for source in self.collect_output_files(call_context):
            relative = source.relative_to(call_context.temp_dir)
            destination = call_context.result_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != destination.resolve(strict=False):
                shutil.copy2(source, destination)
            mapping[str(source)] = str(destination)
        return mapping

    def rewrite_paths_in_payload(self, payload: Any, path_mapping: Mapping[str, str]) -> Any:
        if isinstance(payload, str):
            updated = payload
            for source, destination in path_mapping.items():
                updated = updated.replace(source, destination)
            return updated
        if isinstance(payload, list):
            return [self.rewrite_paths_in_payload(item, path_mapping) for item in payload]
        if isinstance(payload, dict):
            return {
                key: self.rewrite_paths_in_payload(value, path_mapping)
                for key, value in payload.items()
            }
        return payload

    def extract_reported_paths(self, payload: Any) -> list[str]:
        discovered: list[str] = []
        self._collect_reported_paths(payload, discovered)
        return discovered

    def _collect_reported_paths(self, payload: Any, sink: list[str]) -> None:
        if isinstance(payload, str):
            for match in _RESULT_PATH_RE.finditer(payload):
                sink.append(match.group("path"))
            if payload.startswith("/") and Path(payload).suffix.lower() in self.path_validator.allowed_extensions:
                sink.append(payload)
            return
        if isinstance(payload, list):
            for item in payload:
                self._collect_reported_paths(item, sink)
            return
        if isinstance(payload, dict):
            for value in payload.values():
                self._collect_reported_paths(value, sink)
