# ABOUTME: Tests Earth-Agent runner output remapping and MCP argument sanitization.

import json
from pathlib import Path

import pytest

from openeo_ai.earth_agent.agent_runner import EarthAgentRunner
from openeo_ai.earth_agent.mcp_client import EarthAgentMCPClient
from openeo_ai.earth_agent.path_resolver import EarthAgentPathResolver
from openeo_ai.earth_agent.security import EarthAgentSecurityError, PathValidator, TempDirManager


def _make_resolver(tmp_path: Path) -> EarthAgentPathResolver:
    temp_root = tmp_path / "ea_results"
    result_root = tmp_path / "openeo_results"
    validator = PathValidator(allowed_base_dirs=[temp_root, result_root])
    return EarthAgentPathResolver(
        path_validator=validator,
        temp_dir_manager=TempDirManager(root_dir=temp_root, ttl_seconds=3600, quota_bytes=1024 * 1024),
        result_root=result_root,
    )


@pytest.mark.asyncio
async def test_runner_materializes_outputs_and_rewrites_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    resolver = _make_resolver(tmp_path)
    input_file = resolver.result_root / "job-9" / "input.tif"
    input_file.parent.mkdir(parents=True, exist_ok=True)
    input_file.write_bytes(b"input")

    runner = EarthAgentRunner(path_resolver=resolver)

    async def _fake_run(command):
        temp_dir = Path(command[command.index("--temp-dir") + 1])
        output_path = temp_dir / "outputs" / "analysis.tif"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"analysis")
        payload = {
            "answer": "done",
            "trajectory": [],
            "raw_response": {},
            "message": f"Result saved at {output_path}",
        }
        return json.dumps(payload), "", 0

    monkeypatch.setattr(runner, "_run_command", _fake_run)

    result = await runner.analyze(
        query="Analyze this raster",
        input_files=[input_file],
        job_id="job-9",
    )

    assert result["answer"] == "done"
    assert len(result["output_files"]) == 1
    assert Path(result["output_files"][0]).exists()
    assert "earth_agent" in result["output_files"][0]
    assert result["message"].endswith("analysis.tif")


def test_mcp_client_prepares_safe_arguments(tmp_path: Path):
    resolver = _make_resolver(tmp_path)
    input_file = resolver.result_root / "job-9" / "input.tif"
    input_file.parent.mkdir(parents=True, exist_ok=True)
    input_file.write_bytes(b"input")

    client = EarthAgentMCPClient(path_resolver=resolver, path_validator=resolver.path_validator)
    call = resolver.create_call_context(prefix="ea", job_id="job-9")

    prepared = client._prepare_arguments(
        {
            "input_path": str(input_file),
            "output_path": "outputs/result.tif",
            "threshold": 0.3,
        },
        call,
    )

    assert prepared["input_path"] == str(input_file.resolve())
    assert prepared["output_path"] == f"{call.call_id}/outputs/result.tif"
    assert prepared["threshold"] == 0.3


def test_mcp_client_rejects_absolute_output_paths(tmp_path: Path):
    resolver = _make_resolver(tmp_path)
    client = EarthAgentMCPClient(path_resolver=resolver, path_validator=resolver.path_validator)
    call = resolver.create_call_context(prefix="ea", job_id="job-9")

    with pytest.raises(EarthAgentSecurityError):
        client._prepare_arguments({"output_path": "/etc/passwd"}, call)
