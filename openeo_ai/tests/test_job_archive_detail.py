# ABOUTME: Tests saved job detail assembly from archived result metadata.

import json
from datetime import datetime

from openeo_ai.storage import job_archive
from openeo_ai.storage.models import Project, SavedResult


def test_build_job_detail_includes_metadata_project_and_download_urls(tmp_path):
    result_dir = tmp_path / "archive"
    result_dir.mkdir()

    result_file = result_dir / "result.tif"
    result_file.write_bytes(b"geotiff-bytes")

    aux_file = result_dir / "summary.json"
    aux_file.write_text('{"ok": true}')

    metadata_file = result_dir / "metadata.json"
    metadata_file.write_text(
        json.dumps(
            {
                "format": "GTiff",
                "validation": {"is_valid": True, "warnings": ["Data is 91.2% NaN"]},
                "statistics": {"min": 0.1, "max": 0.9, "mean": 0.5},
            }
        )
    )

    project = Project(
        id="proj-1",
        name="Northern Basin Monitoring",
        description="Seasonal vegetation monitoring",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    row = SavedResult(
        save_id="abcd1234",
        title="NDVI March Composite",
        result_path=str(result_file),
        original_path="/tmp/original/result.tif",
        bounds=[[10.1, 20.2, 10.9, 20.8]],
        colormap="viridis",
        size_bytes=result_file.stat().st_size,
        source_query="Generate NDVI composite",
        job_id="job-1",
        project_id=project.id,
        created_at=datetime.utcnow(),
    )
    row.project = project

    detail = job_archive._build_job_detail(row)

    assert detail["project"]["name"] == "Northern Basin Monitoring"
    assert detail["metadata"]["format"] == "GTiff"
    assert detail["statistics"]["mean"] == 0.5
    assert detail["validation"]["warnings"] == ["Data is 91.2% NaN"]
    assert detail["download_url"] == "/saved-jobs/abcd1234/files/result.tif"
    assert any(
        file_entry["download_url"] == "/saved-jobs/abcd1234/files/result.tif"
        for file_entry in detail["job_files"]
    )
