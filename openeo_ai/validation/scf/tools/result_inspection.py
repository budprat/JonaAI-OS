"""Result inspection and sanity-check tool."""
from __future__ import annotations

from typing import Any, Dict


def inspect_result(path: str) -> Dict[str, Any]:
    try:
        import xarray as xr
    except Exception as e:
        return {"error": f"xarray not available: {e}"}

    data = None
    try:
        import rioxarray as rxr
        data = rxr.open_rasterio(path)
    except Exception:
        try:
            data = xr.open_dataset(path)
        except Exception as e:
            return {"error": f"Unable to open result: {e}"}

    try:
        arr = data
        if hasattr(data, "to_array"):
            arr = data.to_array()
        values = arr.values
        import numpy as np
        finite = np.isfinite(values)
        nan_ratio = 1.0 - (finite.sum() / values.size if values.size else 0)
        stats = {
            "min": float(np.nanmin(values)) if values.size else None,
            "max": float(np.nanmax(values)) if values.size else None,
            "mean": float(np.nanmean(values)) if values.size else None,
            "std": float(np.nanstd(values)) if values.size else None,
            "nan_ratio": float(nan_ratio),
            "shape": list(values.shape),
            "dtype": str(values.dtype),
        }
        return stats
    except Exception as e:
        return {"error": f"Failed to compute stats: {e}"}
