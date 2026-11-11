"""Result storage management for OpenEO job outputs."""

import json
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import numpy as np
import xarray as xr

from openeo_app.core.paths import result_root

logger = logging.getLogger(__name__)

# Default TTL for result files: 24 hours in seconds
RESULT_TTL_SECONDS = 24 * 60 * 60
# Cleanup interval: every hour
CLEANUP_INTERVAL_SECONDS = 60 * 60


def validate_result_data(data: Union[xr.DataArray, xr.Dataset]) -> dict:
    """Validate result data and return statistics.

    Args:
        data: The xarray data to validate

    Returns:
        Dict with validation info: is_valid, warnings, stats

    Raises:
        ValueError: If data is completely invalid (empty)
    """
    warnings = []
    stats = {}

    if isinstance(data, xr.Dataset):
        # For datasets, check each variable
        for var in data.data_vars:
            var_data = data[var]
            if var_data.size == 0:
                raise ValueError(f"Data variable '{var}' is empty")

            arr = var_data.values
            nan_pct = np.isnan(arr).sum() / arr.size * 100 if arr.size > 0 else 0
            if nan_pct > 90:
                warnings.append(f"Variable '{var}' is {nan_pct:.1f}% NaN")

            stats[var] = {
                "min": float(np.nanmin(arr)) if arr.size > 0 else None,
                "max": float(np.nanmax(arr)) if arr.size > 0 else None,
                "nan_percent": round(nan_pct, 2),
            }
    else:
        # DataArray
        if data.size == 0:
            raise ValueError("DataArray is empty")

        arr = data.values
        nan_pct = np.isnan(arr).sum() / arr.size * 100 if arr.size > 0 else 0
        if nan_pct > 90:
            warnings.append(f"Data is {nan_pct:.1f}% NaN")

        if nan_pct == 100:
            warnings.append("Data contains only NaN values - result may be invalid")

        stats = {
            "min": float(np.nanmin(arr)) if arr.size > 0 and nan_pct < 100 else None,
            "max": float(np.nanmax(arr)) if arr.size > 0 and nan_pct < 100 else None,
            "nan_percent": round(nan_pct, 2),
            "shape": list(data.shape),
        }

    return {
        "is_valid": len(warnings) == 0 or all("NaN" in w for w in warnings),
        "warnings": warnings,
        "stats": stats,
    }


class ResultStorage:
    """
    Manage storage of job results and execution logs.

    Handles saving computation results to disk in various formats
    and managing job execution logs.
    """

    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize the ResultStorage.

        Args:
            base_path: Base directory for storing results; defaults to result_root()
        """
        self.base_path = Path(base_path) if base_path else result_root()
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        self.results_dir = self.base_path / "results"
        self.logs_dir = self.base_path / "logs"
        self.results_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)

        # Start background cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
        )
        self._cleanup_thread.start()
        logger.info(
            f"ResultStorage initialized with TTL={RESULT_TTL_SECONDS}s, "
            f"cleanup every {CLEANUP_INTERVAL_SECONDS}s"
        )

    def _get_job_dir(self, job_id: uuid.UUID) -> Path:
        """Get the directory for a specific job."""
        job_dir = self.results_dir / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def save_result(
        self,
        job_id: uuid.UUID,
        data: Union[xr.DataArray, xr.Dataset, dict, list],
        format: str = "GTiff",
        filename: Optional[str] = None,
        notes: Optional[list] = None,
    ) -> Path:
        """
        Save computation result to storage.

        Args:
            job_id: The job identifier
            data: The data to save (xarray DataArray/Dataset, dict, or list)
            format: Output format (GTiff, netCDF, JSON)
            filename: Optional custom filename
            notes: Facts about how the data was loaded, kept with the result

        Returns:
            Path to the saved result file
        """
        job_dir = self._get_job_dir(job_id)

        # Determine filename and extension
        ext_map = {
            "GTiff": ".tif",
            "GeoTiff": ".tif",
            "netCDF": ".nc",
            "JSON": ".json",
            "PNG": ".png",
        }
        ext = ext_map.get(format, ".tif")

        if filename is None:
            filename = f"result{ext}"

        result_path = job_dir / filename

        logger.info(f"Saving result for job {job_id} to {result_path}")

        try:
            # Validate data before saving
            validation = None
            if isinstance(data, (xr.DataArray, xr.Dataset)):
                try:
                    validation = validate_result_data(data)
                    for warning in validation.get("warnings", []):
                        logger.warning(f"Job {job_id} result warning: {warning}")
                except ValueError as ve:
                    logger.error(f"Job {job_id} result validation failed: {ve}")
                    raise

            dropped_time_note = None
            if format in ["GTiff", "GeoTiff"]:
                dropped_time_note = self._save_geotiff(data, result_path)
            elif format == "netCDF":
                self._save_netcdf(data, result_path)
            elif format == "JSON":
                self._save_json(data, result_path)
            elif format == "PNG":
                self._save_png(data, result_path)
            else:
                # Default to GeoTIFF
                dropped_time_note = self._save_geotiff(data, result_path)

            if dropped_time_note:
                validation = validation or {"is_valid": True, "warnings": []}
                validation["warnings"].append(dropped_time_note)

            if notes:
                validation = validation or {"is_valid": True, "warnings": []}
                validation["warnings"].extend(notes)
                self._tag_load_notes(result_path, notes)

            # Save metadata with validation info
            self._save_metadata(job_id, result_path, format, validation)

            return result_path

        except Exception as e:
            logger.error(f"Failed to save result for job {job_id}: {e}")
            raise

    def _save_geotiff(self, data: xr.DataArray, path: Path) -> Optional[str]:
        """Save data as GeoTIFF.

        Handles multi-dimensional arrays by:
        1. Converting Dataset to DataArray
        2. Selecting first time slice if time dimension exists
        3. Squeezing singleton dimensions
        4. Ensuring proper 2D or 3D (bands, y, x) shape for rasterio
        """
        import rioxarray  # noqa: F401
        import numpy as np

        if isinstance(data, xr.Dataset):
            # Convert dataset to dataarray if needed
            data = data.to_array(dim="bands")

        # Ensure CRS is set
        if data.rio.crs is None:
            data = data.rio.write_crs("EPSG:4326")

        time_tags = None
        # Handle time dimension — GeoTIFF only supports 2D/3D (bands, y, x)
        if "time" in data.dims or "t" in data.dims:
            time_dim = "time" if "time" in data.dims else "t"
            n_times = data[time_dim].size
            if n_times > 1:
                time_tags = {
                    "JONA_TIME_STEPS": str(n_times),
                    "JONA_KEPT_TIME": str(data[time_dim].values[0])[:10],
                }
                logger.warning(
                    f"GeoTIFF does not support time dimension: {n_times} time steps found, "
                    f"saving FIRST time slice only. Use 'netCDF' format to preserve all time steps, "
                    f"or add reduce_dimension(dimension='time', reducer=median) to your process graph."
                )
            data = data.isel({time_dim: 0})

        # Handle 4D+ arrays by squeezing singleton dimensions
        if data.ndim > 3:
            # Squeeze any singleton dimensions
            data = data.squeeze()
            logger.debug(f"Squeezed data to shape: {data.shape}")

        # If still >3D, select first element of extra dimensions
        while data.ndim > 3:
            # Find a non-spatial dimension to reduce
            for dim in data.dims:
                if dim not in ("x", "y", "latitude", "longitude", "bands"):
                    logger.warning(f"Reducing dimension '{dim}' to first element for GeoTIFF")
                    data = data.isel({dim: 0})
                    break
            else:
                # If all dimensions look spatial, just take first
                first_dim = data.dims[0]
                logger.warning(f"Taking first element of '{first_dim}' for GeoTIFF")
                data = data.isel({first_dim: 0})

        # Validate data before saving
        if data.size == 0:
            raise ValueError("Cannot save empty DataArray as GeoTIFF")

        # Check for all-NaN data
        if hasattr(data, "values"):
            arr = data.values
            if np.all(np.isnan(arr)):
                logger.warning("Data contains only NaN values")

        data.rio.to_raster(str(path), driver="GTiff")

        if time_tags:
            # Readers of the file can't otherwise tell that time steps were dropped.
            import rasterio

            with rasterio.open(path, "r+") as ds:
                ds.update_tags(**time_tags)
            return (
                f"Saved 1 of {time_tags['JONA_TIME_STEPS']} time steps "
                f"({time_tags['JONA_KEPT_TIME']}); GeoTIFF keeps a single date."
            )
        return None

    def _tag_load_notes(self, path: Path, notes: list) -> None:
        """Keep load notes on the file, so copies of a result still carry them."""
        if path.suffix.lower() != ".tif":
            return
        import rasterio

        try:
            with rasterio.open(path, "r+") as ds:
                ds.update_tags(JONA_LOAD_NOTES=" ".join(notes))
        except Exception as e:
            logger.warning(f"Could not tag load notes on {path}: {e}")

    def _save_netcdf(self, data: Union[xr.DataArray, xr.Dataset], path: Path):
        """Save data as NetCDF."""
        if isinstance(data, xr.DataArray):
            data = data.to_dataset(name="data")
        data.to_netcdf(str(path))

    def _save_json(self, data, path: Path):
        """Save data as JSON."""
        if isinstance(data, (xr.DataArray, xr.Dataset)):
            # Convert xarray to dict
            result = data.to_dict()
        elif hasattr(data, "tolist"):
            result = data.tolist()
        elif isinstance(data, (dict, list)):
            result = data
        else:
            result = str(data)

        with open(path, "w") as f:
            json.dump(result, f, indent=2, default=str)

    def _save_png(self, data: xr.DataArray, path: Path):
        """Save data as PNG image."""
        import numpy as np
        from PIL import Image

        if isinstance(data, xr.Dataset):
            data = data.to_array(dim="bands")

        # Handle time dimension first — PNG only supports 2D/3D image-like arrays.
        if "time" in data.dims or "t" in data.dims:
            time_dim = "time" if "time" in data.dims else "t"
            n_times = data[time_dim].size
            if n_times > 1:
                logger.warning(
                    f"PNG does not support time dimension: {n_times} time steps found, "
                    f"saving FIRST time slice only. Use 'netCDF' format to preserve all time steps."
                )
            data = data.isel({time_dim: 0})

        # Squeeze singleton dimensions before converting to numpy.
        if data.ndim > 3:
            data = data.squeeze()
            logger.debug(f"Squeezed PNG data to shape: {data.shape}")

        # If still >3D, reduce extra non-spatial dimensions to the first element.
        while data.ndim > 3:
            for dim in data.dims:
                if dim not in ("x", "y", "latitude", "longitude", "bands"):
                    logger.warning(f"Reducing dimension '{dim}' to first element for PNG")
                    data = data.isel({dim: 0})
                    break
            else:
                first_dim = data.dims[0]
                logger.warning(f"Taking first element of '{first_dim}' for PNG")
                data = data.isel({first_dim: 0})

        # Get numpy array
        arr = data.values

        # Normalize to 0-255.
        # For RGB stacks, stretch each channel independently using robust percentiles
        # so one bright cloud band does not flatten the whole image into gray mush.
        def _stretch_channel(channel):
            finite = np.isfinite(channel)
            if not finite.any():
                return np.zeros_like(channel, dtype=np.uint8)
            low, high = np.nanpercentile(channel[finite], [2, 98])
            if high <= low:
                low = np.nanmin(channel[finite])
                high = np.nanmax(channel[finite])
            if high <= low:
                return np.zeros_like(channel, dtype=np.uint8)
            scaled = np.clip((channel - low) / (high - low), 0.0, 1.0)
            scaled = np.nan_to_num(scaled, nan=0.0, posinf=1.0, neginf=0.0)
            return (scaled * 255).astype(np.uint8)

        # Handle different dimensions
        if arr.ndim == 2:
            arr = _stretch_channel(arr)
            img = Image.fromarray(arr, mode="L")
        elif arr.ndim == 3:
            if arr.shape[0] in [1, 3, 4]:
                arr = np.moveaxis(arr, 0, -1)
            if arr.shape[-1] == 1:
                arr = _stretch_channel(arr[:, :, 0])
                img = Image.fromarray(arr, mode="L")
            elif arr.shape[-1] in (3, 4):
                channels = [_stretch_channel(arr[:, :, idx]) for idx in range(arr.shape[-1])]
                arr = np.stack(channels, axis=-1)
                img = Image.fromarray(arr, mode="RGB" if arr.shape[-1] == 3 else "RGBA")
            else:
                arr = _stretch_channel(arr[:, :, 0])
                img = Image.fromarray(arr, mode="L")
        else:
            raise ValueError(f"Cannot save array with {arr.ndim} dimensions as PNG")

        img.save(str(path))

    def _save_metadata(
        self,
        job_id: uuid.UUID,
        result_path: Path,
        format: str,
        validation: Optional[dict] = None,
    ):
        """Save result metadata with validation info."""
        job_dir = self._get_job_dir(job_id)
        metadata_path = job_dir / "metadata.json"

        metadata = {
            "job_id": str(job_id),
            "result_file": result_path.name,
            "format": format,
            "created": datetime.utcnow().isoformat(),
            "size_bytes": result_path.stat().st_size if result_path.exists() else 0,
        }

        # Add validation info if available
        if validation:
            metadata["validation"] = {
                "is_valid": validation.get("is_valid", True),
                "warnings": validation.get("warnings", []),
            }
            if "stats" in validation:
                metadata["statistics"] = validation["stats"]

        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

    def get_result(self, job_id: uuid.UUID) -> Optional[Path]:
        """
        Get the path to a job's result file.

        Args:
            job_id: The job identifier

        Returns:
            Path to the result file, or None if not found
        """
        job_dir = self.results_dir / str(job_id)

        if not job_dir.exists():
            return None

        # Look for result files
        for ext in [".tif", ".nc", ".json", ".png"]:
            result_path = job_dir / f"result{ext}"
            if result_path.exists():
                return result_path

        # Check metadata for custom filename
        metadata_path = job_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path) as f:
                metadata = json.load(f)
                result_file = metadata.get("result_file")
                if result_file:
                    result_path = job_dir / result_file
                    if result_path.exists():
                        return result_path

        return None

    def delete_result(self, job_id: uuid.UUID) -> bool:
        """
        Delete all result files for a job.

        Args:
            job_id: The job identifier

        Returns:
            True if deletion was successful
        """
        import shutil

        job_dir = self.results_dir / str(job_id)

        if not job_dir.exists():
            return False

        try:
            shutil.rmtree(job_dir)
            return True
        except Exception as e:
            logger.error(f"Failed to delete results for job {job_id}: {e}")
            return False

    def save_log(
        self,
        job_id: uuid.UUID,
        level: str,
        message: str,
        data: Optional[dict] = None,
    ):
        """
        Save a log entry for a job.

        Args:
            job_id: The job identifier
            level: Log level (debug, info, warning, error)
            message: Log message
            data: Optional additional data
        """
        log_file = self.logs_dir / f"{job_id}.jsonl"

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": level,
            "message": message,
        }

        if data:
            log_entry["data"] = data

        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    def get_logs(self, job_id: uuid.UUID, limit: int = 100) -> list[dict]:
        """
        Get logs for a job.

        Args:
            job_id: The job identifier
            limit: Maximum number of log entries to return

        Returns:
            List of log entries
        """
        log_file = self.logs_dir / f"{job_id}.jsonl"

        if not log_file.exists():
            return []

        logs = []
        with open(log_file) as f:
            for line in f:
                if line.strip():
                    logs.append(json.loads(line))

        # Return most recent logs up to limit
        return logs[-limit:]

    def clear_logs(self, job_id: uuid.UUID) -> bool:
        """Clear all logs for a job."""
        log_file = self.logs_dir / f"{job_id}.jsonl"

        if log_file.exists():
            log_file.unlink()
            return True
        return False

    def cleanup_old_results(self, ttl_seconds: int = RESULT_TTL_SECONDS) -> int:
        """
        Delete result directories older than the TTL.

        Uses the modification time of the result directory to determine age.

        Args:
            ttl_seconds: Maximum age in seconds before deletion (default 24h)

        Returns:
            Number of job result directories deleted
        """
        now = time.time()
        deleted_count = 0

        if not self.results_dir.exists():
            return 0

        for job_dir in self.results_dir.iterdir():
            if not job_dir.is_dir():
                continue

            try:
                dir_mtime = os.path.getmtime(job_dir)
                age = now - dir_mtime

                if age > ttl_seconds:
                    shutil.rmtree(job_dir)
                    deleted_count += 1
                    logger.info(
                        f"Cleaned up old result: {job_dir.name} "
                        f"(age: {age / 3600:.1f}h)"
                    )
            except Exception as e:
                logger.warning(f"Failed to clean up {job_dir}: {e}")

        # Tool outputs are working files too; a user who wants one kept saves it.
        outputs_dir = self.base_path / "users"
        if outputs_dir.exists():
            for output in outputs_dir.glob("*/*"):
                try:
                    if output.is_file() and now - output.stat().st_mtime > ttl_seconds:
                        output.unlink()
                except Exception as e:
                    logger.warning(f"Failed to clean up output {output}: {e}")

        # Also clean up old log files
        if self.logs_dir.exists():
            for log_file in self.logs_dir.iterdir():
                if not log_file.is_file():
                    continue
                try:
                    file_mtime = os.path.getmtime(log_file)
                    if now - file_mtime > ttl_seconds:
                        log_file.unlink()
                        logger.debug(f"Cleaned up old log: {log_file.name}")
                except Exception as e:
                    logger.warning(f"Failed to clean up log {log_file}: {e}")

        if deleted_count > 0:
            logger.info(f"Cleanup complete: removed {deleted_count} old result(s)")

        return deleted_count

    def _cleanup_loop(self):
        """Background loop that periodically cleans up old results."""
        while True:
            time.sleep(CLEANUP_INTERVAL_SECONDS)
            try:
                self.cleanup_old_results()
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
