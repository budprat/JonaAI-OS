#!/usr/bin/env bash
# =============================================================================
# JonaAI — Local Startup Script
# =============================================================================
# Starts: AI web interface + OpenEO backend (8080), Frontend (3000)
# Usage:  bash start-local.sh
# Stop:   Ctrl+C (graceful shutdown of all child processes)
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

# Log files
BACKEND_LOG="/tmp/jonaai-backend.log"
FRONTEND_LOG="/tmp/jonaai-frontend.log"

# Track child PIDs for cleanup
PIDS=()

# ------------------------------------------------------------------
# Graceful shutdown handler
# ------------------------------------------------------------------
cleanup() {
    echo ""
    echo "[shutdown] Stopping all services..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    # Wait briefly for graceful shutdown, then force-kill stragglers
    sleep 2
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "[shutdown] Force-killing PID $pid"
            kill -9 "$pid" 2>/dev/null || true
        fi
    done

    echo "[shutdown] All services stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# ------------------------------------------------------------------
# 0. Kill stale processes from previous runs
# ------------------------------------------------------------------
for port in 8080 3000; do
    stale_pid=$(lsof -ti :$port 2>/dev/null || true)
    if [ -n "$stale_pid" ]; then
        echo "[cleanup] Killing stale process on port $port (PID $stale_pid)"
        kill -9 $stale_pid 2>/dev/null || true
        sleep 0.5
    fi
done

# ------------------------------------------------------------------
# 1. Check .env exists and source it
# ------------------------------------------------------------------
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env file not found."
    echo "Run setup first:  bash setup-local.sh"
    echo "Or copy manually: cp .env.example .env"
    exit 1
fi

echo "=============================================="
echo "  JonaAI — Local Development Server"
echo "=============================================="
echo ""

# Source .env (export all variables)
set -a
source "$PROJECT_DIR/.env"
set +a
echo "[config] Loaded .env"

# ------------------------------------------------------------------
# 2. Activate virtual environment
# ------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: Virtual environment not found at $VENV_DIR"
    echo "Run setup first:  bash setup-local.sh"
    exit 1
fi

source "$VENV_DIR/bin/activate"
echo "[config] Activated venv ($(python3 --version))"

# ------------------------------------------------------------------
# 3. Export PYTHONPATH
# ------------------------------------------------------------------
export PYTHONPATH="$PROJECT_DIR:$PROJECT_DIR/openeo-fastapi${PYTHONPATH:+:$PYTHONPATH}"
export ALEMBIC_DIR="$PROJECT_DIR/openeo_app/psql"
echo "[config] PYTHONPATH=$PYTHONPATH"

# ------------------------------------------------------------------
# 4. Start AI Web Interface + OpenEO Backend (port 8080)
# ------------------------------------------------------------------
# OpenEO backend is mounted inside the AI web interface (same port)
echo ""
echo "[start] AI Web Interface + OpenEO Backend on port 8080..."
echo "[logs]  Backend -> $BACKEND_LOG"
python3 -m openeo_ai.web_interface \
    --host 127.0.0.1 \
    --port 8080 \
    >> "$BACKEND_LOG" 2>&1 &
PIDS+=($!)
echo "  -> PID $!"

# ------------------------------------------------------------------
# 5. Start Frontend dev server (port 3000)
# ------------------------------------------------------------------
FRONTEND_DIR="$PROJECT_DIR/openeo_ai/frontend"
if [ -d "$FRONTEND_DIR" ] && command -v npx &>/dev/null; then
    echo "[start] Frontend (Vite) on port 3000..."
    echo "[logs]  Frontend -> $FRONTEND_LOG"
    cd "$FRONTEND_DIR"
    npx vite --port 3000 >> "$FRONTEND_LOG" 2>&1 &
    PIDS+=($!)
    echo "  -> PID $!"
    cd "$PROJECT_DIR"
else
    echo "[skip] Frontend — directory or npx not found"
fi

# ------------------------------------------------------------------
# 6. Print service URLs
# ------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  Services Running"
echo "=============================================="
echo ""
echo "  AI + OpenEO Backend:  http://localhost:8080"
echo "  OpenEO API:           http://localhost:8080/openeo/1.1.0"
echo "  Frontend (Vite):      http://localhost:3000"
echo ""
echo "  Logs:"
echo "    Backend:   tail -f $BACKEND_LOG"
echo "    Frontend:  tail -f $FRONTEND_LOG"
echo ""
echo "  Press Ctrl+C to stop all services"
echo "=============================================="
echo ""

# ------------------------------------------------------------------
# 7. Wait for all background processes
# ------------------------------------------------------------------
# Wait forever (or until Ctrl+C triggers cleanup)
wait
