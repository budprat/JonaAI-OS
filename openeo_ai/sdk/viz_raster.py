# ABOUTME: Raster-side visualization helpers: colormap auto-detection from
# ABOUTME: titles and best-effort archiving of produced rasters to saved jobs.

import os
from typing import Any, Optional


# Reason: Auto-detect the best colormap from job title/description so
# visualizations use semantically correct colors even when the AI agent
# doesn't explicitly pass a colormap.
INDEX_COLORMAP_MAP = {
    "ndvi": "ndvi",
    "evi": "ndvi",
    "savi": "ndvi",
    "msavi": "ndvi",
    "msavi2": "ndvi",
    "gndvi": "ndvi",
    "lai": "ndvi",
    "arvi": "ndvi",
    "ndre": "ndvi",
    "chl_re": "ndvi",
    "vci": "ndvi",
    "vegetation index": "ndvi",
    "ndwi": "coolwarm",
    "mndwi": "coolwarm",
    "awei": "coolwarm",
    "wiw": "coolwarm",
    "water index": "coolwarm",
    "wetland": "coolwarm",
    "ndbi": "coolwarm",
    "ui": "coolwarm",
    "built-up": "coolwarm",
    "built up": "coolwarm",
    "urban index": "coolwarm",
    "ndsi": "coolwarm",
    "snow index": "coolwarm",
    "ndmi": "coolwarm",
    "tci": "coolwarm",
    "moisture index": "coolwarm",
    "nbr": "inferno",
    "dnbr": "inferno",
    "bai": "inferno",
    "burn severity": "inferno",
    "burn ratio": "inferno",
    "bsi": "plasma",
    "ndti": "plasma",
    "bare soil": "plasma",
    "mining": "plasma",
    "quarry": "plasma",
    "excavation": "plasma",
    "ndci": "viridis",
    "algal bloom": "viridis",
    "chlorophyll-a": "viridis",
    "eutrophic": "viridis",
    "dem": "terrain",
    "elevation": "terrain",
    "altitude": "terrain",
    "height map": "terrain",
    "change": "coolwarm",
    "difference": "coolwarm",
}


def _detect_colormap_from_title(title: str) -> str:
    """Detect the best colormap from a job/result title string.

    Scans for index names in the title and returns the matching colormap.
    Returns 'viridis' if no index is detected.
    """
    if not title:
        return "viridis"
    title_lower = title.lower()
    # Check longer names first to avoid substring collisions (same pattern as _detect_analysis_type)
    for index_name in sorted(INDEX_COLORMAP_MAP.keys(), key=len, reverse=True):
        if index_name in title_lower:
            return INDEX_COLORMAP_MAP[index_name]
    return "viridis"


def _is_unarchived_result(path: str) -> bool:
    """True for a raster in a working location rather than the saved-jobs store."""
    from openeo_app.core.paths import result_root

    working_root = str(result_root()).rstrip("/") + "/"
    return path.startswith(("/tmp/", working_root))


_archived_results: dict = {}
_ARCHIVED_RESULTS_MAX = 500


def _archive_result(source_path: str, title: str, *, bounds: Any = None,
                    colormap: str = "viridis", vmin: Any = None, vmax: Any = None,
                    source_query: str = "", project_id: Optional[str] = None,
                    user_id: Optional[str] = None, context: str = "result") -> Optional[str]:
    """Best-effort archive of a produced raster into the saved-jobs store.

    The lazy import keeps the circular-import protection between
    viz_extractor and storage.job_archive intact.
    """
    try:
        from ..storage.job_archive import save_job
        # One run reaches this from several tools. The same file is one saved result.
        key = (user_id, os.path.realpath(source_path), os.path.getmtime(source_path))
        if key in _archived_results:
            return _archived_results[key]
        save_id = save_job(source_path=source_path, title=title, bounds=bounds,
                           colormap=colormap, vmin=vmin, vmax=vmax,
                           source_query=source_query, project_id=project_id,
                           user_id=user_id)
        if save_id:
            _archived_results[key] = save_id
            while len(_archived_results) > _ARCHIVED_RESULTS_MAX:
                _archived_results.pop(next(iter(_archived_results)))
        return save_id
    except Exception as _e:
        print(f"[auto-save] Failed to archive {context}: {_e}")
        return None
