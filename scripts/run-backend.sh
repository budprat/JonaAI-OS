#!/bin/bash
# ABOUTME: launchd entrypoint for the JonaAI backend (port 8080).
# ABOUTME: Replicates start-local.sh env without its EXIT-trap supervisor.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
export PYTHONPATH="$PWD:$PWD/openeo-fastapi"
export ALEMBIC_DIR="$PWD/openeo_app/psql"
# free the port if a stale listener survived a previous incarnation
stale=$(lsof -ti :8080 || true); [ -n "$stale" ] && kill -9 $stale && sleep 1
# exec is load-bearing: launchd must track the Python PID, not a bash parent,
# or KeepAlive respawns bash while orphaned Python still holds the port.
exec "$PWD/.venv/bin/python3" -m openeo_ai.web_interface --host 127.0.0.1 --port 8080
