# ABOUTME: Host-sized limits on what a job may load, read from the environment.
# ABOUTME: Shared by the load path and by the estimates shown before a job runs.
import os


def positive_int_setting(name: str, default: int) -> int:
    """A whole-number limit from the environment. A value that doesn't parse, or isn't above
    zero, keeps the default rather than stopping startup or disabling the limit."""
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def load_element_limit() -> int:
    """Largest cube a job may load. Set OPENEO_MAX_LOAD_ELEMENTS to fit the host.

    Peak memory is several times the raw size (float32 plus masks and intermediate
    index arrays), so the default of 250M (~1 GB raw) needs a host with far more than
    1 GB available. A value that doesn't parse falls back to the default rather than
    disabling the limit.
    """
    try:
        limit = int(os.environ.get("OPENEO_MAX_LOAD_ELEMENTS", ""))
    except ValueError:
        return 250_000_000
    return limit if limit > 0 else 250_000_000
