# ABOUTME: Persistent job archive — saves completed job results permanently.
# Uses PostgreSQL (saved_results table) for metadata and
# copies result files to ~/.openeo_jobs/results/{save_id}/.

import os
import uuid
import shutil
import tempfile
import threading
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import quote

import logging
logger = logging.getLogger(__name__)

from sqlalchemy import create_engine, select, delete as sa_delete, desc, update
from sqlalchemy.orm import sessionmaker

from .models import BatchRun, EarthAsset, EarthObservation, EarthWatch, EvidencePackage, PlanTrajectory, SavedResult, Project

_RESULTS_DIR = Path.home() / ".openeo_jobs" / "results"
_lock = threading.Lock()

_engine = None
_SessionLocal = None
_tables_ready = False

# Full index list from interpretation.py's _INDEX_DOMAIN_MAP,
# sorted by length descending to avoid substring collision (e.g., "GNDVI" before "NDVI").
_AUTO_INTERPRET_INDICES = sorted(
    [
        "NDVI", "EVI", "SAVI", "MSAVI", "MSAVI2", "GNDVI", "LAI", "ARVI",
        "NDRE", "CHL_RE", "NDWI", "MNDWI", "AWEI", "WIW", "dNBR", "NBR",
        "BAI", "NDBI", "UI", "BSI", "NDTI", "NDSI", "NDMI", "NDCI", "VCI",
        "TCI",
    ],
    key=len,
    reverse=True,
)


def _get_session():
    global _engine, _SessionLocal, _tables_ready
    if _engine is None:
        try:
            from openeo_fastapi.client.psql.engine import get_engine
            _engine = get_engine()
        except Exception:
            import os
            url = (
                f"postgresql://{os.environ.get('POSTGRES_USER', 'postgres')}"
                f":{os.environ.get('POSTGRES_PASSWORD', '')}"
                f"@{os.environ.get('POSTGRESQL_HOST', 'localhost')}"
                f":{os.environ.get('POSTGRESQL_PORT', '5432')}"
                f"/{os.environ.get('POSTGRES_DB', 'openeo')}"
            )
            # pre_ping recycles dead pooled connections; keepalives + connect_timeout
            # bound a half-open socket so a query can't hang the event loop forever
            # (mirrors the primary openeo_fastapi engine).
            _engine = create_engine(
                url,
                pool_pre_ping=True,
                pool_recycle=3600,
                connect_args={
                    "connect_timeout": 10,
                    "keepalives": 1,
                    "keepalives_idle": 30,
                    "keepalives_interval": 10,
                    "keepalives_count": 5,
                },
            )
        _SessionLocal = sessionmaker(bind=_engine)

    if not _tables_ready:
        Project.__table__.create(bind=_engine, checkfirst=True)
        SavedResult.__table__.create(bind=_engine, checkfirst=True)
        BatchRun.__table__.create(bind=_engine, checkfirst=True)
        PlanTrajectory.__table__.create(bind=_engine, checkfirst=True)
        try:
            from sqlalchemy import text, inspect as sa_inspect
            insp = sa_inspect(_engine)
            cols = {c["name"] for c in insp.get_columns("saved_results")}
            if "project_id" not in cols:
                with _engine.begin() as conn:
                    conn.execute(text("ALTER TABLE saved_results ADD COLUMN project_id VARCHAR(36) REFERENCES projects(id) ON DELETE SET NULL"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_saved_results_project ON saved_results(project_id)"))
                logger.info("Added project_id column to saved_results")
            if "user_id" not in cols:
                with _engine.begin() as conn:
                    conn.execute(text("ALTER TABLE saved_results ADD COLUMN user_id VARCHAR(255)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_saved_results_user_id ON saved_results(user_id)"))
                logger.info("Added user_id column to saved_results")
        except Exception as e:
            logger.warning(f"saved_results migration check: {e}")
        try:
            from sqlalchemy import text, inspect as sa_inspect
            insp = sa_inspect(_engine)
            cols_projects = {c["name"] for c in insp.get_columns("projects")}
            if "user_id" not in cols_projects:
                with _engine.begin() as conn:
                    conn.execute(text("ALTER TABLE projects ADD COLUMN user_id VARCHAR(255)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_user ON projects(user_id)"))
                logger.info("Added user_id column to projects")
        except Exception as e:
            logger.warning(f"projects user_id migration check: {e}")
        _tables_ready = True

    return _SessionLocal()


def _ensure_dirs():
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _generate_basic_metadata(geotiff_path: Path, metadata_path: Path) -> None:
    """Generate a basic metadata.json from a GeoTIFF when one doesn't exist."""
    try:
        import numpy as np
        import rasterio

        with rasterio.open(str(geotiff_path)) as src:
            data = src.read(1, masked=True)
            valid = data.compressed() if hasattr(data, 'compressed') else data[~np.isnan(data)]
            nan_count = int(np.isnan(data).sum()) if not hasattr(data, 'compressed') else int(data.mask.sum())
            total = data.size
            stats = {
                "min": float(np.nanmin(valid)) if len(valid) > 0 else 0.0,
                "max": float(np.nanmax(valid)) if len(valid) > 0 else 0.0,
                "mean": float(np.nanmean(valid)) if len(valid) > 0 else 0.0,
                "std": float(np.nanstd(valid)) if len(valid) > 0 else 0.0,
                "nan_percent": round(nan_count / total * 100, 2) if total > 0 else 0.0,
                "shape": list(data.shape),
            }
            metadata = {
                "format": "GTiff",
                "size_bytes": geotiff_path.stat().st_size,
                "created": datetime.utcnow().isoformat(),
                "statistics": stats,
                "validation": {
                    "is_valid": True,
                    "warnings": [] if stats["nan_percent"] < 50 else ["High NaN percentage"],
                },
            }
            metadata_path.write_text(json.dumps(metadata, indent=2))
            logger.info(f"Generated metadata.json for {geotiff_path.name}")
    except ImportError:
        # rasterio not available — write minimal metadata
        metadata = {
            "format": "GTiff",
            "size_bytes": geotiff_path.stat().st_size,
            "created": datetime.utcnow().isoformat(),
            "statistics": None,
            "validation": {"is_valid": True, "warnings": ["Statistics unavailable — rasterio not installed"]},
        }
        metadata_path.write_text(json.dumps(metadata, indent=2))
    except Exception as e:
        logger.warning(f"Failed to generate metadata for {geotiff_path}: {e}")


def _auto_interpret(result_path: str, metadata_path: str, title: str) -> None:
    """Auto-run interpretation engine on a saved result (push mode).

    Reads the GeoTIFF, detects the spectral index from the title/path,
    runs the interpretation engine, and merges the result into metadata.json.
    Runs in a background thread — never blocks the save operation.
    Uses atomic write (write-to-tmp + rename) to avoid race conditions with
    concurrent readers of metadata.json.
    """
    try:
        import numpy as np
        import rasterio
        from openeo_ai.tools.interpretation import (
            interpret_index_value,
            assess_quality,
            _detect_index_from_path,
        )

        p = Path(result_path)
        if not p.exists() or p.suffix.lower() not in (".tif", ".tiff"):
            return

        # Detect index from title or path
        index_name = _detect_index_from_path(str(p))
        if not index_name:
            # Try from title — sorted by length descending to avoid substring collision
            title_lower = title.lower()
            for idx in _AUTO_INTERPRET_INDICES:
                if idx.lower() in title_lower:
                    index_name = idx
                    break

        if not index_name:
            logger.debug(f"[auto-interpret] No index detected for {title}, skipping")
            return

        # Compute stats from raster
        with rasterio.open(result_path) as ds:
            data = ds.read(1).astype(float)
            nodata = ds.nodata
            if nodata is not None:
                data[data == nodata] = np.nan
            valid = data[~np.isnan(data)].ravel()
            if valid.size == 0:
                return
            stats = {
                "min": float(np.min(valid)),
                "max": float(np.max(valid)),
                "mean": float(np.mean(valid)),
                "median": float(np.median(valid)),
                "std": float(np.std(valid)),
                "p5": float(np.percentile(valid, 5)),
                "p25": float(np.percentile(valid, 25)),
                "p75": float(np.percentile(valid, 75)),
                "p95": float(np.percentile(valid, 95)),
                "coverage_pct": round(100.0 * valid.size / data.size, 1),
                "valid_pixels": int(valid.size),
                "total_pixels": data.size,
            }

        # Run interpretation
        interp = interpret_index_value(index_name, stats)
        quality = assess_quality(stats)

        # Merge into metadata.json using atomic write (tmp + rename)
        meta_path = Path(metadata_path)
        existing = {}
        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text())
            except Exception:
                pass

        existing["interpretation"] = interp
        existing["quality_assessment"] = quality
        existing["interpreted_index"] = index_name

        # Atomic write: write to a .tmp file then rename to avoid partial reads
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(meta_path.parent), suffix='.tmp')
        try:
            os.write(tmp_fd, json.dumps(existing, indent=2).encode())
            os.close(tmp_fd)
            os.rename(tmp_path, str(meta_path))
        except Exception:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        logger.info(f"[auto-interpret] {title}: {index_name} grade={interp.get('grade', '?')} score={interp.get('score', 0)}")
        from . import result_copies

        result_copies.queue(meta_path.parent.name, None)

    except ImportError:
        logger.debug("[auto-interpret] rasterio not available, skipping")
    except Exception as e:
        logger.warning(f"[auto-interpret] Failed for {title}: {e}")


def _extract_location_from_title(title: str) -> Optional[str]:
    """Extract the location/subject from an analysis title by stripping index keywords, dates, and analysis terms.

    Examples:
        "Ob River Floodplain NDVI Monitoring July 2025" → "Ob River Floodplain"
        "Nazca Geoglyphs - BSI Bare Soil Detection" → "Nazca Geoglyphs"
        "Houston NDBI - Industrial Zone Detection (Jul 2025)" → "Houston"
        "Bangladesh Flood Extent - MNDWI August 2024" → "Bangladesh"
        "Caral Archaeological Site - NDWI Soil Moisture Analysis" → "Caral Archaeological Site"
    """
    import re

    # Remove parenthetical suffixes like "(Jul 2025)" or "(Dry Season 2025)"
    text = re.sub(r'\([^)]*\)', '', title).strip()

    # Split on " - " separator (common in titles)
    parts = text.split(" - ")
    location = parts[0].strip()

    # Strip index keywords from the end of the location part
    # Use the full index list, sorted longest first
    for idx in _AUTO_INTERPRET_INDICES:
        # Remove index keyword and anything after it
        pattern = re.compile(rf'\b{re.escape(idx)}\b.*$', re.IGNORECASE)
        location = pattern.sub('', location).strip()

    # Strip common analysis terms
    _ANALYSIS_TERMS = [
        "Analysis", "Detection", "Monitoring", "Mapping", "Assessment",
        "Change", "Baseline", "Current", "Extent", "Health", "Stress",
        "Vegetation", "Water", "Flood", "Urban", "Built-up", "Stone",
        "Snow", "Ice", "Fire", "Burn", "Soil", "Moisture",
    ]
    for term in _ANALYSIS_TERMS:
        location = re.sub(rf'\b{term}\b', '', location, flags=re.IGNORECASE).strip()

    # Strip trailing date patterns (months, years)
    location = re.sub(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b', '', location, flags=re.IGNORECASE).strip()
    location = re.sub(r'\b20\d{2}\b', '', location).strip()

    # Clean up multiple spaces and trailing separators
    location = re.sub(r'\s+', ' ', location).strip().rstrip('-').rstrip(',').strip()

    return location if len(location) >= 3 else None


def _auto_assign_project(save_id: str, title: str, user_id: Optional[str] = None) -> Optional[str]:
    """Auto-create or find a project based on the title's location and assign the result.

    Returns the project_id if assigned, None otherwise.
    """
    try:
        location = _extract_location_from_title(title)
        if not location:
            return None

        session = _get_session()
        try:
            # Search for existing project with matching name (case-insensitive prefix match)
            from sqlalchemy import func
            existing = session.execute(
                select(Project).where(
                    func.lower(Project.name).contains(location.lower()),
                    Project.user_id == user_id if user_id else Project.user_id == None,
                )
            ).scalars().first()

            if existing:
                project_id = existing.id
            else:
                # Create new project
                # Unique per project: the same place name from two users used to give both the
                # same id, so the second insert failed and that result got no project.
                # projects.id is String(36): 5 + 21 + 1 + 8.
                project_id = f"proj-{location.lower().replace(' ', '-')[:21]}-{uuid.uuid4().hex[:8]}"
                new_project = Project(
                    id=project_id,
                    name=location,
                    description=f"Auto-created project for analyses related to {location}.",
                    user_id=user_id,
                )
                session.add(new_project)
                session.commit()
                logger.info(f"[auto-project] Created project '{location}' (id={project_id})")

            # Assign the saved result to this project
            result = session.execute(
                select(SavedResult).where(SavedResult.save_id == save_id)
            ).scalar_one_or_none()
            if result and not result.project_id:
                result.project_id = project_id
                session.commit()
                logger.info(f"[auto-project] Assigned {save_id} to project '{location}'")

            return project_id
        except Exception as e:
            session.rollback()
            logger.warning(f"[auto-project] Failed: {e}")
            return None
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"[auto-project] Error: {e}")
        return None


def save_job(
    source_path: str,
    title: str,
    bounds: Optional[list] = None,
    colormap: str = "viridis",
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
    source_query: str = "",
    job_id: str = "",
    project_id: Optional[str] = None,
    user_id: Optional[str] = None,
    observation_id: Optional[str] = None,
    process_graph: Optional[Dict[str, Any]] = None,
    scf: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    source_items: Optional[List[Dict[str, Any]]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Copy a result file to permanent storage and save metadata to PostgreSQL."""
    source = Path(source_path)
    if not source.exists():
        logger.error(f"Source file not found: {source_path}")
        return None

    save_id = str(uuid.uuid4())[:8]
    dest_dir = _RESULTS_DIR / save_id
    created_dir = False

    with _lock:
        try:
            _ensure_dirs()
            # Ids are 8 hex characters, so they can collide. Never reuse a folder: a collision
            # used to copy into another result's folder, fail the insert, then delete that folder.
            for _attempt in range(5):
                try:
                    dest_dir.mkdir()
                    created_dir = True
                    break
                except FileExistsError:
                    save_id = str(uuid.uuid4())[:8]
                    dest_dir = _RESULTS_DIR / save_id
            else:
                raise RuntimeError("couldn't find a free save id")

            # Copy the result file
            dest_file = dest_dir / source.name
            shutil.copy2(str(source), str(dest_file))

            # Copy metadata.json if it exists alongside the source, or generate basic one
            source_meta = source.parent / "metadata.json"
            dest_meta = dest_dir / "metadata.json"
            if source_meta.exists():
                shutil.copy2(str(source_meta), str(dest_meta))
            elif not dest_meta.exists():
                # Generate basic metadata from the GeoTIFF
                _generate_basic_metadata(dest_file, dest_meta)

            size_bytes = dest_file.stat().st_size

            # Save to PostgreSQL
            saved_result_db_id = None
            session = _get_session()
            try:
                row = SavedResult(
                    save_id=save_id,
                    user_id=user_id,
                    title=title,
                    result_path=str(dest_file),
                    original_path=source_path,
                    bounds=bounds,
                    colormap=colormap,
                    vmin=vmin,
                    vmax=vmax,
                    size_bytes=size_bytes,
                    source_query=source_query,
                    job_id=job_id,
                    project_id=project_id,
                )
                session.add(row)
                session.commit()
                saved_result_db_id = row.id
            except Exception as db_err:
                session.rollback()
                raise db_err
            finally:
                session.close()

            # Create a first-class evidence package when the new platform
            # tables are present. This is intentionally best-effort so older
            # deployments without the Phase 17 migration keep archiving jobs.
            if user_id and saved_result_db_id:
                try:
                    metadata = {}
                    if dest_meta.exists():
                        try:
                            metadata = json.loads(dest_meta.read_text())
                        except Exception:
                            metadata = {}
                    evidence_session = _get_session()
                    try:
                        artifacts = {
                            "result": {
                                "href": str(dest_file),
                                "name": dest_file.name,
                                "size_bytes": size_bytes,
                                "media_type": "image/tiff" if dest_file.suffix.lower() in (".tif", ".tiff") else "application/octet-stream",
                            },
                            "metadata": {
                                "href": str(dest_meta),
                                "media_type": "application/json",
                            },
                        }
                        stac_item = {
                            "type": "Feature",
                            "stac_version": "1.0.0",
                            "id": save_id,
                            "geometry": None,
                            "bbox": bounds,
                            "properties": {
                                "title": title,
                                "datetime": datetime.utcnow().isoformat() + "Z",
                                "jonaai:job_id": job_id or None,
                                "jonaai:save_id": save_id,
                            },
                            "assets": {
                                "result": {
                                    "href": str(dest_file),
                                    "type": artifacts["result"]["media_type"],
                                    "title": title,
                                }
                            },
                        }
                        evidence_statistics = metadata.get("statistics", {}) if isinstance(metadata, dict) else {}
                        evidence_provenance = provenance or (
                            metadata.get("provenance", {}) if isinstance(metadata, dict) else {}
                        )
                        evidence_session.add(
                            EvidencePackage(
                                user_id=user_id,
                                observation_id=observation_id,
                                saved_result_id=saved_result_db_id,
                                saved_result_save_id=save_id,
                                stac_item=stac_item,
                                source_items=source_items or [],
                                artifacts=artifacts,
                                statistics=evidence_statistics,
                                provenance=evidence_provenance,
                                process_graph=process_graph or {},
                                scf=scf,
                                trace_id=trace_id,
                            )
                        )
                        evidence_session.commit()
                    except Exception:
                        evidence_session.rollback()
                        raise
                    finally:
                        evidence_session.close()
                except Exception as evidence_err:
                    logger.warning(f"Evidence package creation skipped for {save_id}: {evidence_err}")

            logger.info(f"Saved job {save_id}: {title} -> {dest_file}")

            # Auto-assign to project if no explicit project_id was provided
            if not project_id:
                _auto_assign_project(save_id, title, user_id=user_id)

            from . import result_copies

            result_copies.queue(save_id, user_id)

            # Auto-interpret in background (push mode) — non-blocking
            threading.Thread(
                target=_auto_interpret,
                args=(str(dest_file), str(dest_dir / "metadata.json"), title),
                daemon=True,
            ).start()

            return save_id

        except Exception as e:
            logger.error(f"Failed to save job: {e}")
            if created_dir:  # only a folder this call made, never an existing result's
                shutil.rmtree(dest_dir, ignore_errors=True)
            return None


def list_jobs(limit: int = 50, project_id: Optional[str] = None, user_id: Optional[str] = None,
              include_restorable: bool = False) -> List[Dict[str, Any]]:
    """List saved jobs from PostgreSQL, newest first. Validates files still exist.

    With include_restorable, each job also says whether its files are here (`available`)
    and whether an offsite copy of it exists (`restorable`), and lost jobs that have a
    copy are listed too, so they can be opened and restored. Lost jobs with no copy stay
    hidden.
    """
    try:
        session = _get_session()
        try:
            stmt = select(SavedResult)
            if user_id is not None:
                stmt = stmt.where(SavedResult.user_id == user_id)
            if project_id is not None:
                stmt = stmt.where(SavedResult.project_id == project_id)
            stmt = stmt.order_by(desc(SavedResult.created_at)).limit(limit)
            results = session.execute(stmt).scalars().all()
            copied = set()
            if include_restorable:
                from . import result_copies

                copied = result_copies.copied([row.save_id for row in results])
            valid = []
            for row in results:
                available = Path(row.result_path).exists()
                if not include_restorable:
                    if available:
                        valid.append(row.to_dict())
                    continue
                restorable = row.save_id in copied
                if available or restorable:
                    valid.append({**row.to_dict(), "available": available, "restorable": restorable})
            return valid
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to list jobs from DB: {e}")
        return []


def _read_metadata(result_path: str) -> Optional[Dict[str, Any]]:
    """Load a saved result's metadata.json if present."""
    metadata_path = Path(result_path).parent / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        return json.loads(metadata_path.read_text())
    except Exception as e:
        logger.warning(f"Failed to read metadata for {result_path}: {e}")
        return None


def read_visualizations(result_path: str) -> Optional[Dict[str, Any]]:
    """Load a saved result's visualization bundle (charts/tables/process-graph/report).

    Mirrors _read_metadata: the bundle lives next to the result file as
    visualizations.json. Returns None when absent or unreadable.
    """
    bundle_path = Path(result_path).parent / "visualizations.json"
    if not bundle_path.exists():
        return None
    try:
        return json.loads(bundle_path.read_text())
    except Exception as e:
        logger.warning(f"Failed to read visualizations for {result_path}: {e}")
        return None


def write_visualizations(result_path: str, bundle: Dict[str, Any]) -> None:
    """Persist a visualization bundle next to the result file (atomic tmp+rename)."""
    bundle_path = Path(result_path).parent / "visualizations.json"
    tmp_path = bundle_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(bundle))
    tmp_path.replace(bundle_path)


def save_visualizations(save_id: str, bundle: Dict[str, Any], user_id: Optional[str] = None) -> bool:
    """Save a visualization bundle for a saved result, gated on ownership.

    get_job enforces user_id ownership; returns False when the caller has no
    access to save_id (or it does not exist).
    """
    job = get_job(save_id, user_id=user_id)
    if not job or not job.get("result_path"):
        return False
    try:
        write_visualizations(job["result_path"], bundle)
        from . import result_copies

        result_copies.queue(save_id, job.get("user_id"))
        return True
    except Exception as e:
        logger.warning(f"Failed to save visualizations for {save_id}: {e}")
        return False


def get_visualizations(save_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read the visualization bundle for a saved result, gated on ownership."""
    job = get_job(save_id, user_id=user_id)
    if not job or not job.get("result_path"):
        return None
    return read_visualizations(job["result_path"])


def _build_job_detail(row: SavedResult, session: Optional[Any] = None) -> Dict[str, Any]:
    detail = row.to_dict(include_job_files=True)
    metadata = _read_metadata(row.result_path)
    result_name = Path(row.result_path).name
    result_ext = Path(row.result_path).suffix.lower().lstrip(".")
    download_url = f"/saved-jobs/{row.save_id}/files/{quote(result_name)}"
    evidence_rows: List[EvidencePackage] = []
    earth_observation = None
    earth_watch = None
    earth_asset = None

    if session is not None:
        try:
            evidence_stmt = (
                select(EvidencePackage)
                .where(EvidencePackage.saved_result_save_id == row.save_id)
                .order_by(desc(EvidencePackage.created_at))
            )
            if row.user_id:
                evidence_stmt = evidence_stmt.where(EvidencePackage.user_id == row.user_id)
            evidence_rows = list(session.execute(evidence_stmt).scalars().all())

            observation_id = next(
                (evidence.observation_id for evidence in evidence_rows if evidence.observation_id),
                None,
            )
            if observation_id:
                observation_stmt = select(EarthObservation).where(EarthObservation.id == observation_id)
                if row.user_id:
                    observation_stmt = observation_stmt.where(EarthObservation.user_id == row.user_id)
                earth_observation = session.execute(observation_stmt).scalar_one_or_none()

                if earth_observation and earth_observation.watch_id:
                    watch_stmt = select(EarthWatch).where(EarthWatch.id == earth_observation.watch_id)
                    if row.user_id:
                        watch_stmt = watch_stmt.where(EarthWatch.user_id == row.user_id)
                    earth_watch = session.execute(watch_stmt).scalar_one_or_none()

                asset_id = earth_observation.asset_id if earth_observation else None
                if asset_id:
                    asset_stmt = select(EarthAsset).where(EarthAsset.id == asset_id)
                    if row.user_id:
                        asset_stmt = asset_stmt.where(EarthAsset.user_id == row.user_id)
                    earth_asset = session.execute(asset_stmt).scalar_one_or_none()
        except Exception as e:
            logger.warning(f"Failed to load Earth Intelligence links for {row.save_id}: {e}")

    detail["metadata"] = metadata
    detail["statistics"] = metadata.get("statistics") if isinstance(metadata, dict) else None
    detail["validation"] = metadata.get("validation") if isinstance(metadata, dict) else None
    detail["interpretation"] = metadata.get("interpretation") if isinstance(metadata, dict) else None
    detail["quality_assessment"] = metadata.get("quality_assessment") if isinstance(metadata, dict) else None
    detail["interpreted_index"] = metadata.get("interpreted_index") if isinstance(metadata, dict) else None
    detail["project"] = row.project.to_dict(include_files=False) if row.project else None
    detail["result_filename"] = result_name
    detail["result_ext"] = result_ext
    detail["download_url"] = download_url
    detail["job_files"] = [
        {
            **job_file,
            "download_url": f"/saved-jobs/{row.save_id}/files/{quote(job_file['name'])}",
        }
        for job_file in detail.get("job_files", [])
    ]
    detail["evidence_packages"] = [evidence.to_dict() for evidence in evidence_rows]
    detail["earth_intelligence"] = {
        "evidence_count": len(evidence_rows),
        "evidence_package_ids": [evidence.id for evidence in evidence_rows],
        "observation": earth_observation.to_dict() if earth_observation else None,
        "watch": earth_watch.to_dict() if earth_watch else None,
        "asset": earth_asset.to_dict() if earth_asset else None,
    }
    from . import result_copies

    # None when offsite copies are off; otherwise says whether this result is backed up.
    detail["backup"] = result_copies.status_of(row.save_id)
    return detail


def create_batch(batch: Dict[str, Any]) -> None:
    """Persist a public-API batch run. Explicit field list — never **batch —
    so no live asyncio objects or stray keys can leak into the row."""
    session = _get_session()
    try:
        session.add(
            BatchRun(
                id=batch["batch_id"],
                client_id=batch["client_id"],
                user_id=batch.get("user_id"),
                webhook_url=batch.get("webhook_url"),
                location_jobs=batch.get("location_jobs") or [],
                webhook_fired=bool(batch.get("webhook_fired", False)),
                created_at=batch.get("created_at") or datetime.utcnow(),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_batch(batch_id: str, client_id: str) -> Optional[Dict[str, Any]]:
    """Return a batch scoped to its owning client, or None.

    Client scoping is in the query (not a post-fetch Python check) to avoid the
    IDOR class flagged for saved jobs. created_at is returned as a real
    datetime so the /batch response can call .isoformat() on it.
    """
    session = _get_session()
    try:
        row = session.execute(
            select(BatchRun).where(
                BatchRun.id == batch_id,
                BatchRun.client_id == client_id,
            )
        ).scalar_one_or_none()
        if not row:
            return None
        return {
            "batch_id": row.id,
            "client_id": row.client_id,
            "user_id": row.user_id,
            "webhook_url": row.webhook_url,
            "location_jobs": list(row.location_jobs or []),
            "webhook_fired": row.webhook_fired,
            "created_at": row.created_at,
        }
    finally:
        session.close()


def restore_saved_result(save_id: str) -> bool:
    """Bring a saved result's files back from its offsite copy if they are missing here.

    True when the files are here afterwards. It may download, so call it off the event
    loop, and only after the caller's access check: get_job never restores.
    """
    try:
        session = _get_session()
        try:
            row = session.execute(select(SavedResult).where(SavedResult.save_id == save_id)).scalar_one_or_none()
            if row is not None:
                session.expunge(row)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to look up {save_id} for restore: {e}")
        return False
    if row is None:
        return False
    if Path(row.result_path).exists():
        return True
    from . import result_copies

    if not result_copies.restore(row):
        return False
    _record_restored_size(save_id, Path(row.result_path))
    return True


def _record_restored_size(save_id: str, result: Path) -> None:
    """Make size_bytes match the restored file, which may be a coarser copy."""
    try:
        size = result.stat().st_size
        session = _get_session()
        try:
            session.execute(
                update(SavedResult).where(SavedResult.save_id == save_id).values(size_bytes=size)
            )
            session.commit()
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Couldn't record the restored size of {save_id}: {e}")


def get_job(save_id: str, user_id: Optional[str] = None, require_file: bool = True) -> Optional[Dict[str, Any]]:
    """Get a single saved job by save_id.

    By default a job whose result file is missing counts as not found. Access checks pass
    require_file=False, so a lost result with an offsite copy can still be authorized and
    then restored.
    """
    try:
        session = _get_session()
        try:
            stmt = select(SavedResult).where(SavedResult.save_id == save_id)
            if user_id is not None:
                stmt = stmt.where(SavedResult.user_id == user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row and (not require_file or Path(row.result_path).exists()):
                return row.to_dict()
            return None
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get job {save_id}: {e}")
        return None


def get_job_detail(save_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a single saved job with project association and archived metadata."""
    try:
        session = _get_session()
        try:
            from sqlalchemy.orm import joinedload

            stmt = (
                select(SavedResult)
                .options(joinedload(SavedResult.project))
                .where(SavedResult.save_id == save_id)
            )
            if user_id is not None:
                stmt = stmt.where(SavedResult.user_id == user_id)
            row = session.execute(stmt).unique().scalar_one_or_none()
            if row and Path(row.result_path).exists():
                return _build_job_detail(row, session=session)
            return None
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get detailed job {save_id}: {e}")
        return None


def delete_job(save_id: str, user_id: Optional[str] = None) -> bool:
    """Delete a saved job: remove from DB and delete files."""
    try:
        session = _get_session()
        try:
            stmt = select(SavedResult).where(SavedResult.save_id == save_id)
            if user_id is not None:
                stmt = stmt.where(SavedResult.user_id == user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if not row:
                return False
            owner = row.user_id

            session.execute(
                sa_delete(SavedResult).where(SavedResult.save_id == save_id)
            )
            session.commit()
        except Exception as db_err:
            session.rollback()
            raise db_err
        finally:
            session.close()

        # Remove files
        dest_dir = _RESULTS_DIR / save_id
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        from . import result_copies

        result_copies.forget(save_id, owner)
        return True

    except Exception as e:
        logger.error(f"Failed to delete job {save_id}: {e}")
        return False


# ── Project CRUD ──────────────────────────────────────────────────────


def create_project(project_id: str, name: str, description: str = "", user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        session = _get_session()
        try:
            p = Project(id=project_id, name=name, description=description, user_id=user_id)
            session.add(p)
            session.commit()
            session.refresh(p)
            return p.to_dict(include_files=True)
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to create project: {e}")
            return None
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to create project: {e}")
        return None


def list_unassigned_files(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        session = _get_session()
        try:
            stmt = select(SavedResult).where(SavedResult.project_id == None)
            if user_id is not None:
                stmt = stmt.where(SavedResult.user_id == user_id)
            stmt = stmt.order_by(desc(SavedResult.created_at))
            results = session.execute(stmt).scalars().all()
            return [r.to_dict(include_job_files=True) for r in results]
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to list unassigned files: {e}")
        return []


def list_projects(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        session = _get_session()
        try:
            from sqlalchemy.orm import joinedload
            stmt = select(Project).options(joinedload(Project.saved_results))
            if user_id is not None:
                stmt = stmt.where(Project.user_id == user_id)
            stmt = stmt.order_by(desc(Project.updated_at))
            results = session.execute(stmt).unique().scalars().all()
            return [p.to_dict(include_files=True) for p in results]
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to list projects: {e}")
        return []


def get_project(project_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        session = _get_session()
        try:
            from sqlalchemy.orm import joinedload
            stmt = select(Project).options(joinedload(Project.saved_results)).where(Project.id == project_id)
            if user_id is not None:
                stmt = stmt.where(Project.user_id == user_id)
            p = session.execute(stmt).unique().scalar_one_or_none()
            return p.to_dict(include_files=True) if p else None
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get project {project_id}: {e}")
        return None


def update_project(project_id: str, name: Optional[str] = None, description: Optional[str] = None, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        session = _get_session()
        try:
            from sqlalchemy.orm import joinedload
            stmt = select(Project).options(joinedload(Project.saved_results)).where(Project.id == project_id)
            if user_id is not None:
                stmt = stmt.where(Project.user_id == user_id)
            p = session.execute(stmt).unique().scalar_one_or_none()
            if not p:
                return None
            if name is not None:
                p.name = name
            if description is not None:
                p.description = description
            p.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(p)
            return p.to_dict(include_files=True)
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update project: {e}")
            return None
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to update project {project_id}: {e}")
        return None


def delete_project(project_id: str, user_id: Optional[str] = None) -> bool:
    """Delete a project, all its associated saved results, and their files from disk."""
    try:
        session = _get_session()
        try:
            from sqlalchemy.orm import joinedload
            p = session.execute(
                select(Project).options(joinedload(Project.saved_results)).where(Project.id == project_id)
            ).unique().scalar_one_or_none()
            if p and user_id is not None and p.user_id != user_id:
                return False
            if not p:
                return False

            saved = [(r.save_id, r.user_id) for r in (p.saved_results or [])]

            session.delete(p)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to delete project: {e}")
            return False
        finally:
            session.close()

        from . import result_copies

        for sid, owner in saved:
            dest_dir = _RESULTS_DIR / sid
            if dest_dir.exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
                logger.info(f"Deleted result files for save_id={sid}")
            result_copies.forget(sid, owner)

        return True
    except Exception as e:
        logger.error(f"Failed to delete project {project_id}: {e}")
        return False


def assign_job_to_project(save_id: str, project_id: Optional[str], user_id: Optional[str] = None) -> bool:
    try:
        session = _get_session()
        try:
            stmt = select(SavedResult).where(SavedResult.save_id == save_id)
            if user_id is not None:
                stmt = stmt.where(SavedResult.user_id == user_id)
            row = session.execute(stmt).scalar_one_or_none()
            if not row:
                return False
            if project_id is not None and user_id is not None:
                project = session.execute(
                    select(Project).where(Project.id == project_id, Project.user_id == user_id)
                ).scalar_one_or_none()
                if not project:
                    return False
            row.project_id = project_id
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to assign job to project: {e}")
            return False
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to assign job {save_id}: {e}")
        return False
