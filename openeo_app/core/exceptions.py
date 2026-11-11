"""Custom exception hierarchy for OpenEO processing.

Provides specific exception types for better error handling and
user-friendly error messages.
"""


class ProcessingError(Exception):
    """Base class for all OpenEO processing errors.

    All custom exceptions inherit from this class, making it easy
    to catch any processing-related error.
    """

    def __init__(self, message: str, details: dict = None):
        """Initialize with message and optional details.

        Args:
            message: Human-readable error message
            details: Optional dictionary with additional error context
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON response."""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
        }


class DataSourceUnavailableError(ProcessingError):
    """Raised when STAC query returns no items.

    This typically happens when:
    - The spatial/temporal extent doesn't match any data
    - The collection doesn't have data for the requested area
    - Network issues prevent accessing the STAC catalog
    """

    def __init__(self, message: str, query: dict = None):
        super().__init__(message, details={"query": query} if query else None)
        self.query = query


class STACQueryError(ProcessingError):
    """Raised when STAC API query fails.

    This happens when:
    - The STAC catalog is unreachable
    - The collection ID doesn't exist
    - Invalid query parameters are provided
    """

    def __init__(self, message: str, catalog_url: str = None, collection_id: str = None):
        details = {}
        if catalog_url:
            details["catalog_url"] = catalog_url
        if collection_id:
            details["collection_id"] = collection_id
        super().__init__(message, details=details)
        self.catalog_url = catalog_url
        self.collection_id = collection_id


class BandSelectionError(ProcessingError):
    """Raised when requested bands are not found in the collection.

    This happens when:
    - Band names don't match the collection's band names
    - Band mapping fails (e.g., wrong band name format)
    - The collection doesn't have the requested bands
    """

    def __init__(self, message: str, requested_bands: list = None, available_bands: list = None):
        details = {}
        if requested_bands:
            details["requested_bands"] = requested_bands
        if available_bands:
            details["available_bands"] = available_bands
        super().__init__(message, details=details)
        self.requested_bands = requested_bands
        self.available_bands = available_bands


class ExtentValidationError(ProcessingError):
    """Raised when spatial or temporal extent is invalid.

    This happens when:
    - Spatial extent is too large (global queries)
    - Temporal extent is too long (>1 year)
    - Invalid coordinate values
    """

    def __init__(self, message: str, extent_type: str = None, provided_extent: dict = None):
        details = {}
        if extent_type:
            details["extent_type"] = extent_type
        if provided_extent:
            details["provided_extent"] = provided_extent
        super().__init__(message, details=details)
        self.extent_type = extent_type
        self.provided_extent = provided_extent


class JobExecutionError(ProcessingError):
    """Raised when job execution fails.

    This is a general error for job processing failures that
    don't fit into more specific categories.
    """

    def __init__(self, message: str, job_id: str = None, process_id: str = None):
        details = {}
        if job_id:
            details["job_id"] = job_id
        if process_id:
            details["process_id"] = process_id
        super().__init__(message, details=details)
        self.job_id = job_id
        self.process_id = process_id


class DimensionError(ProcessingError):
    """Raised when dimension operations fail.

    This happens when:
    - Trying to reduce a dimension that doesn't exist
    - Dimension mismatch in merge operations
    - Invalid dimension labels
    """

    def __init__(self, message: str, dimension: str = None, available_dims: list = None):
        details = {}
        if dimension:
            details["dimension"] = dimension
        if available_dims:
            details["available_dimensions"] = available_dims
        super().__init__(message, details=details)
        self.dimension = dimension
        self.available_dims = available_dims


class OutputFormatError(ProcessingError):
    """Raised when result cannot be saved in the requested format.

    This happens when:
    - Unsupported output format requested
    - Data incompatible with format (e.g., 4D data to GeoTIFF)
    - Missing CRS for geospatial formats
    """

    def __init__(self, message: str, format: str = None, reason: str = None):
        details = {}
        if format:
            details["format"] = format
        if reason:
            details["reason"] = reason
        super().__init__(message, details=details)
        self.format = format
        self.reason = reason


# Error classification for user-friendly messages with self-healing suggestions
ERROR_CLASSIFICATIONS = {
    DataSourceUnavailableError: {
        "user_message": "No data found for the specified area and time range.",
        "http_status": 404,
        "auto_fix": {
            "strategy": "widen_search",
            "actions": [
                "Expand temporal extent by 30 days in each direction",
                "Verify spatial extent is within collection coverage",
                "Try a different collection (e.g., landsat-c2-l2 instead of sentinel-2-l2a)",
            ],
        },
    },
    STACQueryError: {
        "user_message": "Failed to query the data catalog.",
        "http_status": 502,
        "auto_fix": {
            "strategy": "retry_or_fallback",
            "actions": [
                "Retry the same request (transient network issue)",
                "Verify collection ID is valid (use openeo_list_collections)",
            ],
        },
    },
    BandSelectionError: {
        "user_message": "The requested bands are not available in this collection.",
        "http_status": 400,
        "auto_fix": {
            "strategy": "fix_bands",
            "actions": [
                "Use explain_collection_bands to get correct band names",
                "For Landsat, use 'nir08' instead of 'nir'",
                "Remove unavailable bands and retry with valid subset",
            ],
        },
    },
    ExtentValidationError: {
        "user_message": "The spatial or temporal extent is invalid or too large.",
        "http_status": 400,
        "auto_fix": {
            "strategy": "reduce_extent",
            "actions": [
                "Halve the spatial extent (reduce by 50%)",
                "Limit to fewer bands (only bands needed for the analysis)",
                "Reduce temporal range to 1 month",
            ],
        },
    },
    JobExecutionError: {
        "user_message": "Job execution failed.",
        "http_status": 500,
        "auto_fix": {
            "strategy": "diagnose_and_fix",
            "actions": [
                "Check error message for missing process or dimension errors",
                "Validate process graph with openeo_validate_graph before resubmitting",
                "Reduce spatial extent if memory-related error",
            ],
        },
    },
    DimensionError: {
        "user_message": "A dimension operation failed — the requested dimension does not exist or is incompatible.",
        "http_status": 400,
        "auto_fix": {
            "strategy": "fix_dimension_name",
            "actions": [
                "Use 'time' instead of 't' for temporal dimension",
                "Use 'latitude'/'longitude' instead of 'y'/'x' for spatial dims",
                "Check available dimensions in the loaded data",
            ],
        },
    },
    OutputFormatError: {
        "user_message": "The result could not be saved in the requested format.",
        "http_status": 400,
        "auto_fix": {
            "strategy": "fix_output_format",
            "actions": [
                "Use 'GTiff' for 2D/3D raster data",
                "Use 'netCDF' for multi-temporal or 4D data",
                "Reduce temporal dimension before saving as GeoTIFF",
            ],
        },
    },
}


class UserFacingError(ValueError):
    """An error whose message was written for the user, such as why a load is too large."""


_PAID_BUCKET_MARKERS = ("Requester Pays", "sentinel-s1-l1c", "usgs-landsat")


def classify_error(error: Exception) -> dict:
    """Classify an error and return user-friendly information with self-healing suggestions.

    Args:
        error: The exception to classify

    Returns:
        Dictionary with user_message, http_status, auto_fix suggestions, and error details
    """
    error_class = type(error)
    error_str = str(error)

    if error_class in ERROR_CLASSIFICATIONS:
        classification = ERROR_CLASSIFICATIONS[error_class]
        result = {
            "user_message": classification["user_message"],
            "http_status": classification["http_status"],
            "error_type": error_class.__name__,
            "technical_message": error_str,
            "details": error.details if hasattr(error, "details") else {},
            "auto_fix": classification.get("auto_fix", {}),
        }
        return result

    # Pattern-match common runtime errors for self-healing suggestions
    auto_fix = _detect_auto_fix(error_str)

    if any(marker in error_str for marker in _PAID_BUCKET_MARKERS):
        return {
            "user_message": (
                "This collection's files are in a bucket that charges the reader, which this "
                "service can't access. Use sentinel-2-l2a or sentinel-2-c1-l2a instead."
            ),
            "http_status": 502,
            "error_type": error_class.__name__,
            "technical_message": error_str,
            "details": {},
            "auto_fix": {},
        }

    return {
        "user_message": error_str[:500] if isinstance(error, UserFacingError) else "An unexpected error occurred.",
        "http_status": 500,
        "error_type": error_class.__name__,
        "technical_message": error_str,
        "details": {},
        "auto_fix": auto_fix,
    }


def _detect_auto_fix(error_message: str) -> dict:
    """Pattern-match error messages to suggest automatic fixes.

    Args:
        error_message: The error string to analyze

    Returns:
        Dict with strategy and actions for self-healing
    """
    msg = error_message.lower()

    if "dimension" in msg and "not found" in msg:
        return {
            "strategy": "fix_dimension_name",
            "actions": [
                "Use 'time' instead of 't' for temporal dimension",
                "Use 'latitude'/'longitude' instead of 'y'/'x' for spatial dims",
                "Check available dimensions in the loaded data",
            ],
        }

    if "band" in msg and ("not found" in msg or "no such" in msg):
        return {
            "strategy": "fix_bands",
            "actions": [
                "Use explain_collection_bands to verify band names",
                "For Landsat use 'nir08' not 'nir'",
                "Check band names are lowercase",
            ],
        }

    if "missing process" in msg or "process" in msg and "not found" in msg:
        return {
            "strategy": "fix_process",
            "actions": [
                "Check process name spelling",
                "Use openeo_list_processes to find available alternatives",
                "Some processes have underscore prefixes (_and, _or, _not)",
            ],
        }

    if "memory" in msg or "killed" in msg or "oom" in msg:
        return {
            "strategy": "reduce_extent",
            "actions": [
                "Halve the spatial extent",
                "Reduce temporal range",
                "Load fewer bands",
            ],
        }

    if "timeout" in msg or "timed out" in msg:
        return {
            "strategy": "reduce_extent",
            "actions": [
                "Reduce spatial extent (smaller area)",
                "Reduce temporal extent (fewer dates)",
                "Limit bands to only those needed",
            ],
        }

    if "nan" in msg or "empty" in msg or "no valid" in msg:
        return {
            "strategy": "check_masking",
            "actions": [
                "Verify cloud mask semantics — mask() replaces True with NaN",
                "Check if between() range is correct (8-11 for clouds, not 0-7)",
                "Ensure cloud masking is applied BEFORE temporal reduction",
                "Try a different date range with less cloud cover",
            ],
        }

    return {
        "strategy": "generic_retry",
        "actions": [
            "Validate process graph with openeo_validate_graph",
            "Reduce spatial/temporal extent and retry",
            "Check server logs for detailed error information",
        ],
    }
