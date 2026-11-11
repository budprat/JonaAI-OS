"""Implementation of the load_collection process for OpenEO.

Enhanced with:
- Collection-specific chunking strategies (2048x2048 for Sentinel-2)
- Band name mapping (B04->red, B08->nir)
- Cloud-optimized GeoTIFF configuration
- Common name coordinate for NDVI band selection
"""

import logging
import math
import os
import re
from typing import Dict, Optional, List, Any

import numpy as np
import xarray as xr
import odc.stac
import pystac_client
from odc.geo import resxy_

from ..core.extent import extent_to_lonlat, load_resolution
from ..core.exceptions import UserFacingError
from ..core.job_notes import add_job_note
from ..core.limits import load_element_limit as _load_element_limit, positive_int_setting
from .band_mapper import BandMapper, get_scale_factor, get_offset
from .chunking import ChunkingStrategy, get_chunks_for_collection

logger = logging.getLogger(__name__)
# rasterio logs this on every file open when boto3 isn't installed, which is fine here.
logging.getLogger("rasterio.session").setLevel(logging.WARNING)

# Phase 16: Langfuse @observe for tracing STAC fetches.
try:
    from langfuse import observe as _lf_observe  # type: ignore
except ImportError:  # pragma: no cover
    def _lf_observe(*args, **kwargs):  # type: ignore
        def _decorator(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return _decorator

# Standard OpenEO band name -> AWS Earth Search name translation.
# Users can use either format; this dict translates B## codes to STAC names.
OPENEO_TO_AWS_BANDS = {
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B05": "rededge1",
    "B06": "rededge2",
    "B07": "rededge3",
    "B08": "nir",
    "B8A": "nir08",
    "B09": "nir09",
    "B11": "swir16",
    "B12": "swir22",
    "SCL": "scl",
}


def _translate_band_names(bands: Optional[List[str]]) -> Optional[List[str]]:
    """Translate standard OpenEO band names to AWS Earth Search names.

    Accepts both formats - e.g. 'B04' is translated to 'red', while 'red'
    passes through unchanged.

    Args:
        bands: List of band names (may use B## or common name format)

    Returns:
        List with all names translated to AWS Earth Search format
    """
    if not bands:
        return bands

    translated = []
    for band in bands:
        # Check exact match first, then case-insensitive
        aws_name = OPENEO_TO_AWS_BANDS.get(band)
        if aws_name is None:
            aws_name = OPENEO_TO_AWS_BANDS.get(band.upper())
        if aws_name is not None:
            logger.info(f"Band auto-translated: {band} -> {aws_name}")
            translated.append(aws_name)
        else:
            translated.append(band)

    return translated


# Configure odc.stac for cloud-optimized access (16x fewer HTTP requests)
# The catalog holds public scenes at s3:// paths too. Unsigned access has to be set here:
# configure_s3_access() needs botocore, and without it those scenes fail to read.
try:
    odc.stac.configure_rio(cloud_defaults=True, aws={"aws_unsigned": True})
    logger.info("Configured odc.stac for cloud-optimized access")
except Exception as e:
    logger.warning(f"Could not configure odc.stac cloud defaults: {e}")

# Maximum number of acquisition dates to load (prevents huge queries)
MAX_STAC_ITEMS = positive_int_setting("MAX_STAC_ITEMS", 5)
# Most catalog matches to look through when choosing those items.
_MAX_STAC_MATCHES = 2000
_STAC_PAGE_SIZE = 100
# Beyond this, an area is refused rather than loaded too coarse to be useful.
_MAX_COARSENING = 4.0
BASE_RESOLUTION_METERS = 11
# A day this cloudy is only used if clearer days can't fill the scene limit.
_CLOUDY_DAY_PERCENT = 60.0


# Get STAC API URL from environment
STAC_API_URL = os.environ.get(
    "STAC_API_URL",
    "https://earth-search.aws.element84.com/v1/"
)

PLANETARY_COMPUTER_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/"
# Planetary Computer names Landsat's near infrared and QA assets differently.
_LANDSAT_PC_BANDS = {"nir": "nir08", "SR_B5": "nir08", "SR_B4": "red", "SR_B3": "green", "SR_B2": "blue",
                     "SR_B1": "coastal", "SR_B6": "swir16", "SR_B7": "swir22", "QA_PIXEL": "qa_pixel", "qa": "qa_pixel"}

# Collection ID to STAC URL mapping
# These map OpenEO collection IDs to their STAC collection URLs
COLLECTION_STAC_MAP = {
    # AWS Earth Search collections
    "sentinel-2-l2a": f"{STAC_API_URL}collections/sentinel-2-l2a",
    "sentinel-2-l1c": f"{STAC_API_URL}collections/sentinel-2-l1c",
    # Earth Search lists these from reader-pays buckets. Planetary Computer serves them free.
    # Raw GRD values aren't backscatter and its items have no projection, so this serves the
    # calibrated, terrain-corrected version of the same acquisitions.
    "sentinel-1-grd": f"{PLANETARY_COMPUTER_URL}collections/sentinel-1-rtc",
    "sentinel-1-rtc": f"{PLANETARY_COMPUTER_URL}collections/sentinel-1-rtc",
    "landsat-c2-l2": f"{PLANETARY_COMPUTER_URL}collections/landsat-c2-l2",
    "cop-dem-glo-30": f"{STAC_API_URL}collections/cop-dem-glo-30",
    "cop-dem-glo-90": f"{STAC_API_URL}collections/cop-dem-glo-90",
    "naip": f"{STAC_API_URL}collections/naip",
    "sentinel-2-c1-l2a": f"{STAC_API_URL}collections/sentinel-2-c1-l2a",
    "landsat-c2-l1": f"{STAC_API_URL}collections/landsat-c2-l1",
}


# Classification/QA bands that should NOT have reflectance scaling applied.
# These contain integer class labels, not radiometric data.
CLASSIFICATION_BANDS = {
    # Sentinel-2
    "scl", "SCL",           # Scene Classification Layer (0-11)
    # Landsat
    "qa", "QA_PIXEL",       # Quality Assessment pixel
    "QA_RADSAT",            # Radiometric saturation QA
    "ST_QA",                # Surface temperature QA
    # General
    "qa_pixel", "qa_radsat",
    "cloud", "cloud_mask",
    "snow", "snow_mask",
    "water", "water_mask",
}


def _get_classification_bands(collection_id: str) -> set:
    """Get the set of classification band names for a collection.

    These bands contain integer class labels and must NOT have
    reflectance scale factors applied.

    Args:
        collection_id: Collection identifier

    Returns:
        Set of band names that are classification/QA bands
    """
    return CLASSIFICATION_BANDS


@_lf_observe(name="openeo.load_collection")
def load_collection(
    id: str,
    spatial_extent: Optional[dict] = None,
    temporal_extent: Optional[list] = None,
    bands: Optional[list] = None,
    properties: Optional[dict] = None,
    named_parameters: Optional[dict] = None,  # Accept but ignore this parameter from graph executor
    **kwargs,
) -> xr.DataArray:
    """
    Load a collection by its ID.

    This implementation maps collection IDs to STAC URLs and uses
    the openeo-processes-dask load_stac function for actual data loading.

    Args:
        id: Collection identifier (e.g., "sentinel-2-l2a")
        spatial_extent: Bounding box dict with west, south, east, north, crs
        temporal_extent: List of [start, end] datetime strings
        bands: List of band names to load
        properties: Additional STAC query properties
        **kwargs: Additional arguments passed to load_stac

    Returns:
        xarray.DataArray with requested data (lazy Dask array)
    """
    from openeo_pg_parser_networkx.pg_schema import BoundingBox, TemporalInterval
    import rioxarray  # noqa: F401 — required for .rio accessor downstream

    # The catalog search and the load grid both work in lon/lat, whatever CRS was asked for.
    if spatial_extent:
        _lonlat = extent_to_lonlat(spatial_extent)
        if _lonlat is None:
            raise ValueError(f"Could not read spatial extent: {spatial_extent}")
        spatial_extent = {**_lonlat, "crs": "EPSG:4326"}

    # Validate spatial extent size to prevent massive STAC downloads / OOM
    if spatial_extent:
        try:
            _w, _e = spatial_extent["west"], spatial_extent["east"]
            _s, _n = spatial_extent["south"], spatial_extent["north"]
            _area_deg2 = abs(float(_e) - float(_w)) * abs(float(_n) - float(_s))
            _MAX_EXTENT_DEG2 = 25.0  # ~250km x 250km at equator
            if _area_deg2 > _MAX_EXTENT_DEG2:
                raise ValueError(
                    f"Spatial extent too large: {_area_deg2:.1f} deg^2 "
                    f"(max {_MAX_EXTENT_DEG2} deg^2 = ~250km x 250km). "
                    f"Reduce the area or use a tiled approach."
                )
        except (TypeError, AttributeError):
            pass  # Skip validation if extent format is unexpected

    # Validate collection_id to prevent SSRF via URL path injection
    _COLLECTION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_\-\.]*$')
    if not _COLLECTION_ID_PATTERN.match(id):
        raise ValueError(
            f"Invalid collection ID: '{id}'. "
            "Collection IDs must contain only alphanumeric characters, hyphens, underscores, and dots."
        )

    # Auto-translate standard OpenEO band names (B02, B04, B08, etc.)
    # to AWS Earth Search names (blue, red, nir, etc.)
    bands = _translate_band_names(bands)

    if id == "sentinel-1-grd":
        add_job_note("Sentinel-1 was loaded as calibrated, terrain-corrected backscatter (RTC), not raw GRD values.")
    logger.info(f"Loading collection: {id}")
    logger.info(f"  spatial_extent: {spatial_extent}")
    logger.info(f"  temporal_extent: {temporal_extent}")
    logger.info(f"  bands (after translation): {bands}")
    logger.info(f"  kwargs: {kwargs}")

    # Get STAC URL for collection
    stac_url = COLLECTION_STAC_MAP.get(id)

    if stac_url is None:
        # Try constructing URL from STAC_API_URL
        stac_url = f"{STAC_API_URL}collections/{id}"
        logger.warning(
            f"Collection '{id}' not in predefined map, "
            f"trying: {stac_url}"
        )

    # Convert spatial_extent to BoundingBox
    bbox = None
    if spatial_extent is not None:
        if isinstance(spatial_extent, dict):
            bbox = BoundingBox(
                west=spatial_extent.get("west"),
                south=spatial_extent.get("south"),
                east=spatial_extent.get("east"),
                north=spatial_extent.get("north"),
                crs=spatial_extent.get("crs", "EPSG:4326"),
            )
        elif isinstance(spatial_extent, BoundingBox):
            bbox = spatial_extent

    # Convert temporal_extent to list of timestamps
    # openeo-pg-parser-networkx passes this as a TemporalInterval RootModel object
    # TemporalInterval.root = list of DateTime/Date RootModel objects
    # DateTime.root = actual pendulum.DateTime (a datetime.datetime subclass)
    temporal = None
    if temporal_extent is not None:
        import pandas as pd

        def _unwrap_datetime(val):
            """Unwrap openeo-pg-parser-networkx DateTime/Date RootModel to real datetime."""
            if val is None:
                return None
            # Unwrap Pydantic v2 RootModel (.root holds the actual value)
            if hasattr(val, 'root') and not isinstance(val.root, (list, tuple)):
                val = val.root
            # At this point val should be a real datetime/pendulum object
            if hasattr(val, 'isoformat'):
                return pd.Timestamp(val)
            # Fallback: try parsing as string
            return pd.to_datetime(str(val))

        try:
            # Extract the raw list from TemporalInterval RootModel
            raw_extent = temporal_extent
            if hasattr(temporal_extent, 'root') and isinstance(temporal_extent.root, (list, tuple)):
                raw_extent = temporal_extent.root

            if isinstance(raw_extent, (list, tuple)) and len(raw_extent) >= 2:
                start = _unwrap_datetime(raw_extent[0])
                end = _unwrap_datetime(raw_extent[1])
                if start is not None and end is not None:
                    temporal = [start, end]
                    logger.info(f"Parsed temporal extent: {temporal}")
            else:
                logger.warning(f"Unknown temporal_extent format: {type(temporal_extent)}")

        except Exception as e:
            logger.error(f"Failed to parse temporal_extent: {e}")

    # Load data via STAC with item limit
    try:
        # Use custom loading with item limit for better control
        data = _load_stac_limited(
            collection_url=stac_url,
            spatial_extent=bbox,
            temporal_extent=temporal,
            bands=bands,
            properties=properties,
            max_items=MAX_STAC_ITEMS,
        )

        logger.info(f"Successfully loaded collection {id}, shape: {data.shape}")
        return data

    except Exception as e:
        logger.error(f"Failed to load collection {id}: {e}")
        raise


def _build_stac_query(properties: Optional[dict]) -> Optional[dict]:
    if not properties:
        return None
    query: dict[str, Any] = {}
    for key, value in properties.items():
        if isinstance(value, dict):
            query[str(key)] = {str(operator): operand for operator, operand in value.items()}
        else:
            query[str(key)] = {"eq": value}
    return query or None


def _sort_stac_items_by_cloud_cover(items: list) -> list:
    def _cloud_key(item):
        props = getattr(item, "properties", {}) or {}
        value = props.get("eo:cloud_cover")
        return float(value) if value is not None else float("inf")

    return sorted(items, key=_cloud_key)


def _coarsening_factor(sizes: Any, max_days: int, limit: int) -> float:
    """How much coarser than the base grid a load must be to fit the element limit.

    Counts the most dates a load can have, not this search's count. Otherwise the
    before and after cubes of one comparison could land on different grids.
    """
    elements = 1
    for name, size in sizes.items():
        elements *= max(int(size), max_days) if name == "time" else int(size)
    return max(1.0, math.sqrt(elements / limit) * 1.01) if elements > limit else 1.0


def _item_day(item) -> Optional[str]:
    """The acquisition date of a STAC item as YYYY-MM-DD, or None if it has none."""
    props = getattr(item, "properties", {}) or {}
    value = props.get("datetime") or getattr(item, "datetime", None) or props.get("start_datetime")
    return str(value)[:10] if value else None


def _item_cloud(item) -> float:
    value = (getattr(item, "properties", {}) or {}).get("eo:cloud_cover")
    return float(value) if value is not None else 100.0


def _is_cog_item(item) -> bool:
    assets = getattr(item, "assets", {}) or {}
    return any(str(getattr(asset, "href", "")).startswith("http") for asset in assets.values())


def _drop_duplicate_scenes(items: list) -> list:
    """Keep one catalog entry per scene.

    Earth Search can list a scene as both ..._0_L2A and ..._1_L2A, and counting both
    would use up the scene limit on one acquisition. Prefer the cloud-optimized copy.
    """
    best: Dict[str, Any] = {}
    for item in items:
        key = re.sub(r"_\d+_(L\w+)$", r"_\1", str(getattr(item, "id", id(item))))
        current = best.get(key)
        if current is None or (_is_cog_item(item) and not _is_cog_item(current)):
            best[key] = item
    return list(best.values())


def _scenes_by_day(items: list) -> Optional[Dict[str, list]]:
    """Group items by acquisition date, or None if any item has no date."""
    by_day: Dict[str, list] = {}
    for item in items:
        day = _item_day(item)
        if day is None:
            return None
        by_day.setdefault(day, []).append(item)
    return by_day


def _select_stac_items(items: list, max_days: int) -> list:
    """Choose scenes from up to max_days dates that span the requested period.

    Taking the clearest scenes overall can leave a composite or a time series resting
    on a few neighboring dates. Split the period into equal windows and take the
    clearest date from each. The limit counts dates because they set the size of the
    load: a date's tiles only mosaic into one time step, so they all stay together.
    """
    by_day = _scenes_by_day(items)
    if by_day is None:
        return _sort_stac_items_by_cloud_cover(items)[:max_days]
    if len(by_day) <= max_days:
        return _sort_stac_items_by_cloud_cover(items)

    def day_cloud(day: str) -> float:
        return sum(_item_cloud(item) for item in by_day[day]) / len(by_day[day])

    days = sorted(by_day)
    clear = [day for day in days if day_cloud(day) <= _CLOUDY_DAY_PERCENT]
    candidates = clear if len(clear) >= max_days else days

    windows: Dict[int, list] = {}
    for position, day in enumerate(candidates):
        windows.setdefault(position * max_days // len(candidates), []).append(day)
    chosen_days = [min(group, key=day_cloud) for group in windows.values()]
    return [item for day in chosen_days for item in by_day[day]]


def _scene_limit_note(selected: list, available: list, capped: bool, max_days: int) -> str:
    days = sorted({day for day in (_item_day(item) for item in selected) if day})
    found = len({_item_day(item) for item in available})
    count = f"{found}+" if capped else str(found)
    return (
        f"Used {len(days)} of {count} available dates ({', '.join(days)}): this service loads "
        f"at most {max_days} per request. The result rests on those dates only, so tell the "
        "user. Narrow the date range or split it into separate jobs to use more of them."
    )


def _load_stac_limited(
    collection_url: str,
    spatial_extent=None,
    temporal_extent=None,
    bands: Optional[List[str]] = None,
    properties: Optional[dict] = None,
    max_items: int = 5,
) -> xr.DataArray:
    """
    Load STAC data with optimized chunking and band mapping.

    Features:
    - Collection-specific chunk sizes (2048x2048 for Sentinel-2)
    - Band name translation (OpenEO names -> STAC names)
    - Common name coordinate for NDVI band selection
    - Cloud-optimized S3 access configuration
    """
    # Parse collection URL to get catalog and collection ID
    # URL format: https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a
    parts = collection_url.rsplit("/collections/", 1)
    catalog_url = parts[0]
    collection_id = parts[1] if len(parts) > 1 else None

    logger.info(f"Searching STAC catalog: {catalog_url}")
    logger.info(f"Collection: {collection_id}, max_items: {max_items}")

    # Initialize band mapper for this collection
    mapper = BandMapper(collection_id) if collection_id else None

    # Open catalog with timeout protection
    stac_timeout = int(os.environ.get("STAC_TIMEOUT_SECONDS", "30"))
    try:
        # pystac_client expects int/float timeout, not httpx.Timeout
        catalog = pystac_client.Client.open(
            catalog_url,
            timeout=stac_timeout,
        )
    except Exception as e:
        from ..core.exceptions import STACQueryError
        raise STACQueryError(
            f"Failed to connect to STAC catalog: {e}",
            catalog_url=catalog_url,
            collection_id=collection_id,
        )

    # Build search parameters
    # "limit" is the page size, not a total: a small one means many catalog requests.
    search_params = {"limit": _STAC_PAGE_SIZE, "max_items": _MAX_STAC_MATCHES}

    if collection_id:
        search_params["collections"] = [collection_id]

    if spatial_extent is not None:
        bbox = [
            spatial_extent.west,
            spatial_extent.south,
            spatial_extent.east,
            spatial_extent.north,
        ]
        search_params["bbox"] = bbox
        logger.info(f"Bbox: {bbox}")

    logger.info(f"DEBUG: temporal_extent={temporal_extent}")
    if temporal_extent is not None:
        # Convert to ISO 8601 format for STAC API (must have T separator, not space)
        def to_iso(dt):
            if dt is None:
                return None
            if hasattr(dt, "isoformat"):
                return dt.isoformat()
            return str(dt).replace(" ", "T")

        start = to_iso(temporal_extent[0])
        end = to_iso(temporal_extent[1])
        if start and end:
            search_params["datetime"] = f"{start}/{end}"
            logger.info(f"Temporal: {start} to {end}")

    stac_query = _build_stac_query(properties)
    if stac_query:
        search_params["query"] = stac_query
        logger.info(f"STAC property query: {stac_query}")

    # Search for items with error handling
    logger.info(f"Starting STAC search with params: {search_params}")
    try:
        search = catalog.search(**search_params)
        logger.info("STAC search initiated, fetching items...")
        all_items = list(search.items())
        logger.info(f"STAC search complete, got {len(all_items)} items")
    except Exception as e:
        from ..core.exceptions import STACQueryError
        raise STACQueryError(
            f"STAC search failed: {e}",
            catalog_url=catalog_url,
            collection_id=collection_id,
        )

    capped = len(all_items) >= _MAX_STAC_MATCHES
    all_items = _drop_duplicate_scenes(all_items)
    items = _select_stac_items(all_items, max_items)

    if len(items) < len(all_items):
        note = _scene_limit_note(items, all_items, capped, max_items)
        logger.warning(note)
        add_job_note(note)

    logger.info(f"Found {len(all_items)} STAC items, using {len(items)}")

    if bands and any(band.startswith("SR_B") for band in bands) and any(
        (getattr(item, "properties", {}) or {}).get("platform") in ("landsat-4", "landsat-5", "landsat-7")
        for item in items
    ):
        raise UserFacingError(
            "SR_B band numbers differ on Landsat 4, 5, and 7. Ask for bands by name, such as red and nir."
        )

    if "planetarycomputer" in catalog_url:
        # Its files are read with a short-lived signature on each item.
        import planetary_computer

        items = [planetary_computer.sign(item) for item in items]

    if len(items) == 0:
        from ..core.exceptions import DataSourceUnavailableError
        raise DataSourceUnavailableError(
            f"No STAC items found for query: {search_params}"
        )

    # Get optimized chunk configuration for this collection
    chunks = ChunkingStrategy.for_collection(collection_id or "default", len(items))

    # Build load parameters with optimized chunking
    # float32 halves memory against the float64 that scaling integers gives, and reads
    # missing pixels as NaN rather than a reflectance of zero.
    load_params = {
        "chunks": chunks.to_dict(),
        "crs": "EPSG:4326",
        "dtype": "float32",
        "groupby": "solar_day",  # Group by acquisition date
    }

    if spatial_extent is not None:
        load_params["bbox"] = bbox
        step_x, step_y = load_resolution(spatial_extent.south, spatial_extent.north)
    else:
        step_x, step_y = load_resolution()
    load_params["resolution"] = resxy_(step_x, -step_y)

    def _open(params: dict):
        loaded = odc.stac.load(items, bands=bands, **params) if bands else odc.stac.load(items, **params)
        return loaded.rename(requested_names) if requested_names else loaded

    # Map band names to STAC names if we have a mapper
    original_bands = bands
    if bands and mapper:
        bands = mapper.to_stac_names(bands)
        logger.info(f"Band mapping: {original_bands} -> {bands}")
    # Graphs keep using the name they asked for, whatever the catalog calls the asset.
    requested_names = {}
    bands_as_requested = None
    if bands and "planetarycomputer" in catalog_url and "landsat" in (collection_id or ""):
        asked = bands_as_requested = bands
        bands = [_LANDSAT_PC_BANDS.get(band, band) for band in asked]
        requested_names = {actual: name for actual, name in zip(bands, original_bands or asked) if actual != name}

    # Load data with odc.stac
    try:
        ds = _open(load_params)
    except ValueError as e:
        if "No such band/alias" in str(e):
            from ..core.exceptions import BandSelectionError
            raise BandSelectionError(
                f"Band mapping failed for {original_bands}: {e}"
            )
        raise

    # Convert to DataArray. Band math reads bands together along this dimension, and
    # dask refuses that across chunks, which breaks any graph using array_element.
    data = ds.to_dataarray(dim="bands").chunk({"bands": -1})

    # Guard against catastrophic in-memory materialization. A huge element count
    # OOM-SIGKILLs the whole backend at .compute() (macOS jetsam, exit 9). Raise a
    # clear, actionable error instead so the model can narrow the request. The
    # threshold is generous — a normal AOI (e.g. 2000x2000 x 3 bands x a few
    # timesteps) is ~24M elements; this only blocks near-continental full-res loads.
    _max_load_elements = _load_element_limit()
    _scale = _coarsening_factor(data.sizes, max_items, _max_load_elements)
    if _scale > _MAX_COARSENING:
        _n_elements = int(np.prod([int(size) for size in data.sizes.values()]))
        raise UserFacingError(
            f"Requested load is too large to compute in memory: {_n_elements:,} "
            f"elements {dict(data.sizes)} (limit {_max_load_elements:,}), even at "
            f"{_MAX_COARSENING:g} times coarser resolution. Reduce the area, shorten the "
            f"date range, or select fewer bands."
        )
    if _scale > 1:
        ds = _open({**load_params, "resolution": resxy_(step_x * _scale, -step_y * _scale)})
        data = ds.to_dataarray(dim="bands").chunk({"bands": -1})
        note = (
            f"Loaded at about {round(BASE_RESOLUTION_METERS * _scale)} m per pixel instead of "
            f"{BASE_RESOLUTION_METERS} m: this area at full resolution is too large for this "
            "service. Fine detail isn't resolved, so tell the user."
        )
        logger.warning(note)
        add_job_note(note)

    # CRITICAL: Apply scale factor to convert DN to reflectance (0-1)
    # Sentinel-2 L2A uses 0-10000 -> multiply by 0.0001 to get 0-1
    # This is essential for NDVI calculations to produce valid [-1, 1] results
    #
    # IMPORTANT: Classification bands (SCL, QA_PIXEL, etc.) must NOT be scaled.
    # They contain integer class labels (e.g., SCL: 0=no data, 4=vegetation,
    # 8=cloud medium, 9=cloud high). Scaling turns 9 into 0.0009, breaking
    # cloud masking comparisons like eq(scl, 9).
    scale_factor = get_scale_factor(collection_id or "")
    offset = get_offset(collection_id or "")

    if scale_factor != 1.0:
        # Identify classification bands that should NOT be scaled
        classification_bands = _get_classification_bands(collection_id or "")
        band_labels = list(data.coords["bands"].values) if "bands" in data.dims else []
        bands_to_skip = set(b for b in band_labels if b in classification_bands)

        if bands_to_skip:
            # Apply scale factor only to non-classification bands.
            # Use xr.where (not .loc assignment) to work with Dask lazy arrays.
            logger.info(
                f"Applying scale factor {scale_factor} for {collection_id} "
                f"(skipping classification bands: {bands_to_skip})"
            )
            scale_mask = xr.DataArray(
                [band not in bands_to_skip for band in band_labels],
                dims=["bands"],
                coords={"bands": band_labels},
            )
            scaled_data = data * scale_factor
            if offset != 0.0:
                scaled_data = scaled_data + offset
            data = xr.where(scale_mask, scaled_data, data)
            # Preserve original coords/attrs that xr.where may drop
            data = data.assign_coords(data.coords)
        else:
            # No classification bands present — scale everything
            logger.info(f"Applying scale factor {scale_factor} for {collection_id}")
            data = data * scale_factor
            if offset != 0.0:
                data = data + offset

    # CRITICAL: Add common_name coordinate for NDVI band selection
    # This enables data.sel(common_name='nir') for vegetation indices
    if mapper and bands:
        common_names = mapper.get_common_names(bands_as_requested or bands)
        data = data.assign_coords(common_name=("bands", common_names))
        logger.info(f"Added common_name coordinate: {common_names}")

    logger.info(f"Loaded data shape: {data.shape}, dims: {data.dims}")
    logger.info(f"Chunking: {chunks.to_dict()}")

    return data


def register_collection(collection_id: str, stac_url: str):
    """
    Register a new collection mapping.

    Args:
        collection_id: The OpenEO collection ID
        stac_url: The STAC collection URL
    """
    COLLECTION_STAC_MAP[collection_id] = stac_url
    logger.info(f"Registered collection {collection_id} -> {stac_url}")


def get_available_collections() -> list:
    """
    Get list of available collection IDs.

    Returns:
        List of collection ID strings
    """
    return list(COLLECTION_STAC_MAP.keys())


def get_collection_stac_url(collection_id: str) -> Optional[str]:
    """
    Get the STAC URL for a collection.

    Args:
        collection_id: The collection ID

    Returns:
        The STAC URL or None if not found
    """
    return COLLECTION_STAC_MAP.get(collection_id)
