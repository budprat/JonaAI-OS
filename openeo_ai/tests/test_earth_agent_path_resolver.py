# ABOUTME: Tests Earth-Agent path staging and output remapping behavior.

from pathlib import Path

from openeo_ai.earth_agent.path_resolver import EarthAgentPathResolver
from openeo_ai.earth_agent.security import PathValidator, TempDirManager


def _make_resolver(tmp_path: Path) -> EarthAgentPathResolver:
    temp_root = tmp_path / "ea_results"
    result_root = tmp_path / "openeo_results"
    validator = PathValidator(allowed_base_dirs=[temp_root, result_root])
    return EarthAgentPathResolver(
        path_validator=validator,
        temp_dir_manager=TempDirManager(root_dir=temp_root, ttl_seconds=3600, quota_bytes=1024 * 1024),
        result_root=result_root,
    )


def test_materialize_outputs_copies_into_job_storage(tmp_path: Path):
    resolver = _make_resolver(tmp_path)
    call = resolver.create_call_context(prefix="ea", job_id="job-123")

    output = call.temp_dir / "derived" / "mask.tif"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"mask")

    mapping = resolver.materialize_outputs(call)
    destination = Path(mapping[str(output)])

    assert destination.exists()
    assert destination.read_bytes() == b"mask"
    assert destination == call.result_dir / "derived" / "mask.tif"


def test_build_output_argument_scopes_to_call_directory(tmp_path: Path):
    resolver = _make_resolver(tmp_path)
    call = resolver.create_call_context(prefix="ea", job_id="job-123")

    assert resolver.build_output_argument("products/output.tif", call) == (
        f"{call.call_id}/products/output.tif"
    )


def test_rewrite_paths_in_payload_updates_nested_strings(tmp_path: Path):
    resolver = _make_resolver(tmp_path)
    payload = {
        "message": "Result saved at /tmp/ea_results/call/result.tif",
        "items": ["/tmp/ea_results/call/result.tif"],
    }
    mapping = {
        "/tmp/ea_results/call/result.tif": str(tmp_path / "mapped" / "result.tif")
    }

    rewritten = resolver.rewrite_paths_in_payload(payload, mapping)

    assert rewritten["message"].endswith("mapped/result.tif")
    assert rewritten["items"][0].endswith("mapped/result.tif")
