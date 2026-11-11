# ABOUTME: Reads the provenance tags a result writer leaves on a raster, so analysis
# ABOUTME: tools can warn when a file holds only part of what the job computed.
from typing import Optional

TIME_STEPS_TAG = "JONA_TIME_STEPS"
KEPT_TIME_TAG = "JONA_KEPT_TIME"
LOAD_NOTES_TAG = "JONA_LOAD_NOTES"


def time_slice_warning(path: str) -> Optional[str]:
    """Return a warning if this raster kept one time step of several."""
    try:
        import rasterio

        with rasterio.open(path) as ds:
            tags = ds.tags()
    except Exception:
        return None
    steps = tags.get(TIME_STEPS_TAG)
    if not steps:
        return None
    return (
        f"WARNING: this file holds only one of {steps} computed time steps "
        f"({tags.get(KEPT_TIME_TAG, 'the first')}). Statistics describe that single "
        "date, not the whole period and not a change between dates. Tell the user, and "
        "rerun with a time reduction if the question spans the period."
    )


def load_notes_warning(path: str) -> Optional[str]:
    """Return the notes recorded when this raster's data was loaded, if any."""
    try:
        import rasterio

        with rasterio.open(path) as ds:
            notes = ds.tags().get(LOAD_NOTES_TAG)
    except Exception:
        return None
    return f"WARNING: {notes}" if notes else None
