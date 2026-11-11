#!/usr/bin/env bash
# =============================================================================
# JonaAI — One-Time Local Setup Script
# =============================================================================
# Run once after cloning: bash setup-local.sh
# Creates venv, installs deps, applies patches, sets up frontend.
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

# ------------------------------------------------------------------
# Detect uv for faster installs (falls back to pip if unavailable)
# ------------------------------------------------------------------
if command -v uv &>/dev/null; then
    PIP="uv pip"
    VENV_CMD="uv venv"
    echo "[config] Using uv $(uv --version 2>/dev/null | head -1) for fast installs"
else
    PIP="pip"
    VENV_CMD="python3 -m venv"
    echo "[config] uv not found — using pip (install uv for 10-100x faster setup)"
fi

echo "=============================================="
echo "  JonaAI — Local Setup"
echo "=============================================="
echo "Project: $PROJECT_DIR"
echo ""

# ------------------------------------------------------------------
# 1. Create Python virtual environment if it doesn't exist
# ------------------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/7] Creating Python virtual environment..."
    $VENV_CMD "$VENV_DIR"
    echo "  -> Created $VENV_DIR"
else
    echo "[1/7] Virtual environment already exists at $VENV_DIR"
fi

# Activate venv
source "$VENV_DIR/bin/activate"
echo "  -> Activated venv (Python: $(python3 --version))"

# ------------------------------------------------------------------
# 2. Upgrade pip and install core Python dependencies
# ------------------------------------------------------------------
echo ""
echo "[2/7] Installing Python dependencies..."
$PIP install --upgrade pip setuptools wheel 2>&1 | tail -1

# Install main requirements
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    $PIP install -r "$PROJECT_DIR/requirements.txt" 2>&1 | tail -5
    echo "  -> Installed requirements.txt"
fi

# Install the scientific/geo stack
echo "  -> Installing scientific/geo stack..."
$PIP install \
    'pydantic>=2.12' \
    'pydantic-core>=2.41' \
    'fastapi>=0.128' \
    'starlette>=0.40' \
    'uvicorn>=0.30' \
    'geojson-pydantic>=1.0' \
    'numpy>=2.0' \
    'shapely>=2.0' \
    'xvec' \
    'odc-stac' \
    'geopandas' \
    'dask-geopandas' \
    'pystac-client' \
    'planetary_computer' \
    'stackstac' \
    'stac_validator' \
    'Pillow' \
    2>&1 | tail -5
echo "  -> Scientific/geo stack installed"

# ------------------------------------------------------------------
# 3. Install openeo-fastapi with --no-deps to avoid pydantic conflicts
# ------------------------------------------------------------------
echo ""
echo "[3/7] Installing openeo-fastapi (--no-deps)..."
$PIP install --no-deps 'openeo-fastapi>=2025.5.1' 2>&1 | tail -3
echo "  -> openeo-fastapi installed (no-deps)"

# ------------------------------------------------------------------
# 4. Apply pydantic patches to openeo_pg_parser_networkx
# ------------------------------------------------------------------
echo ""
echo "[4/7] Applying pydantic compatibility patches..."

SITE_PACKAGES="$(python3 -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null || python3 -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
echo "  -> Site-packages: $SITE_PACKAGES"

# Patch pg_schema.py: regex -> pattern (pydantic v2)
PG_SCHEMA="$SITE_PACKAGES/openeo_pg_parser_networkx/pg_schema.py"
if [ -f "$PG_SCHEMA" ]; then
    sed -i.bak 's/constr(regex=/constr(pattern=/g' "$PG_SCHEMA" 2>/dev/null || \
    sed -i '' 's/constr(regex=/constr(pattern=/g' "$PG_SCHEMA"
    rm -f "${PG_SCHEMA}.bak"
    echo "  -> Patched pg_schema.py (regex -> pattern)"
fi

# Patch utils.py: pydantic.error_wrappers -> pydantic (pydantic v2)
PG_UTILS="$SITE_PACKAGES/openeo_pg_parser_networkx/utils.py"
if [ -f "$PG_UTILS" ]; then
    sed -i.bak 's/pydantic.error_wrappers.ValidationError/pydantic.ValidationError/g' "$PG_UTILS" 2>/dev/null || \
    sed -i '' 's/pydantic.error_wrappers.ValidationError/pydantic.ValidationError/g' "$PG_UTILS"
    rm -f "${PG_UTILS}.bak"
    echo "  -> Patched utils.py (ValidationError import)"
fi

# Run the comprehensive pydantic compat fixer if it exists
if [ -f "$PROJECT_DIR/fix_pydantic_compat.py" ]; then
    python3 "$PROJECT_DIR/fix_pydantic_compat.py"
    echo "  -> Ran fix_pydantic_compat.py"
fi

# ------------------------------------------------------------------
# 5. Apply import patches to openeo_processes_dask
# ------------------------------------------------------------------
echo ""
echo "[5/7] Applying openeo_processes_dask import patches..."

python3 -c "
import os, sys

site_pkg = '$SITE_PACKAGES'
base = os.path.join(site_pkg, 'openeo_processes_dask', 'process_implementations')

if not os.path.isdir(base):
    print('  -> openeo_processes_dask not found, skipping')
    sys.exit(0)

# Patch cubes/__init__.py - wrap 'from .load import *' in try/except
cubes_init = os.path.join(base, 'cubes', '__init__.py')
if os.path.isfile(cubes_init):
    content = open(cubes_init).read()
    if 'from .load import *' in content and 'try:' not in content:
        content = content.replace(
            'from .load import *',
            'try:\n    from .load import *\nexcept ImportError:\n    pass'
        )
        open(cubes_init, 'w').write(content)
        print('  -> Patched cubes/__init__.py')
    else:
        print('  -> cubes/__init__.py already patched or not needed')

# Patch top-level __init__.py with proper try/except blocks
top_init = os.path.join(base, '__init__.py')
if os.path.isfile(top_init):
    content = open(top_init).read()
    if 'import rioxarray' in content and content.count('try:') < 3:
        fixed = '''import logging

logger = logging.getLogger(__name__)

from .arrays import *
from .comparison import *
from .cubes import *
from .inspect import *
from .logic import *
from .math import *

try:
    from .ml import *
except ImportError as e:
    logger.warning('Did not load ML processes due to missing deps')

try:
    import rioxarray as rio
except ImportError:
    pass

try:
    import openeo_processes_dask.process_implementations.cubes._xr_interop
except ImportError:
    pass
'''
        open(top_init, 'w').write(fixed)
        print('  -> Patched process_implementations/__init__.py')
    else:
        print('  -> process_implementations/__init__.py already patched or not needed')

print('  -> Import patches complete')
"

# ------------------------------------------------------------------
# 6. Install frontend dependencies
# ------------------------------------------------------------------
echo ""
echo "[6/7] Installing frontend dependencies..."
FRONTEND_DIR="$PROJECT_DIR/openeo_ai/frontend"
if [ -d "$FRONTEND_DIR" ]; then
    cd "$FRONTEND_DIR"
    if command -v npm &>/dev/null; then
        npm install 2>&1 | tail -5
        echo "  -> Frontend npm packages installed"
    else
        echo "  !! npm not found — install Node.js to run the frontend"
    fi
    cd "$PROJECT_DIR"
else
    echo "  !! Frontend directory not found at $FRONTEND_DIR"
fi

# ------------------------------------------------------------------
# 7. Create .env from .env.example if .env doesn't exist
# ------------------------------------------------------------------
echo ""
echo "[7/7] Checking .env configuration..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        echo "  -> Created .env from .env.example"
        echo "  !! IMPORTANT: Edit .env and set your ANTHROPIC_API_KEY and Supabase settings"
    else
        echo "  !! No .env.example found — create .env manually"
    fi
else
    echo "  -> .env already exists"
fi

# ------------------------------------------------------------------
# Done
# ------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  Setup complete!"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys:  vim .env"
echo "  2. Start all services:            bash start-local.sh"
echo ""
