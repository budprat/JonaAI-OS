# ABOUTME: Where job results and tool outputs are written, read from RESULT_STORAGE_PATH.
# ABOUTME: The default is durable; /tmp is wiped on reboot and is private to a service with PrivateTmp.
import os
from pathlib import Path

# Older installs wrote here. Files under it stay readable, but nothing new is written there.
LEGACY_RESULT_ROOT = Path("/tmp/openeo_results")


def result_root() -> Path:
    """Folder for OpenEO job results and agent tool outputs.

    RESULT_STORAGE_PATH overrides it. The default is under the service's home, so it
    survives restarts and reboots and is writable on a development machine too.
    Job results in it are still removed after a day; saved results are copied to the
    saved-jobs archive first.
    """
    configured = os.environ.get("RESULT_STORAGE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".openeo_results"


def log_root() -> Path:
    """Folder for the OpenEO backend's rotating log files.

    LOG_DIR overrides it. The default is under the service's home, like result_root(),
    because /tmp is wiped on reboot and, under PrivateTmp, on every restart: the logs
    from before a crash would be gone exactly when they're needed. Rotation caps it at
    about 330 MB (three logs, each 10 MB plus 10 backups).
    """
    configured = os.environ.get("LOG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".openeo_logs"


def user_outputs_root() -> Path:
    """Folder holding each user's tool outputs, one subfolder per user."""
    return result_root() / "users"
