# ABOUTME: HTTP route handlers for the OpenEO AI web interface.
# ABOUTME: Includes render-raster, saved-jobs CRUD, projects CRUD, and project members.

import logging
import os
import re
import time
import uuid
import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse

from .visualization.maps import MapComponent
# Supabase Auth replaces legacy single-admin web_auth.py.
# require_auth / require_auth_or_query_token return the JWT payload (dict),
# not the raw token string. Extract user_id with payload["sub"].
from .supabase_auth import require_auth, require_auth_or_query_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Frontend dist directory
_FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

# Allowed directories for file access (resolved once at import time)
_ALLOWED_DIRS = [
    Path("/tmp").resolve(),
    Path.home().resolve() / ".openeo_jobs",
    Path.cwd().resolve(),
]


def _validate_path(raw_path: str, allowed_dirs: list[Path] | None = None) -> Path:
    """Validate a file path against symlink attacks and directory traversal.

    1. Checks if the raw path (before resolve) is a symlink — blocks it.
    2. Resolves the path to its canonical form.
    3. Checks that no component of the resolved path is itself a symlink.
    4. Checks the resolved path is under one of the allowed directories.

    Args:
        raw_path: The untrusted path string from user input.
        allowed_dirs: Override for allowed base directories.

    Returns:
        The resolved, validated Path.

    Raises:
        ValueError: If the path fails any security check.
    """
    dirs = allowed_dirs or _ALLOWED_DIRS
    p = Path(raw_path)

    # Step 1: Reject if the raw path itself is a symlink (before resolve)
    if p.is_symlink():
        raise ValueError(f"Symlink not allowed: {raw_path}")

    # Step 2: Resolve to canonical path
    resolved = p.resolve()

    # Step 3: Walk each component of the resolved path for hidden symlinks
    # Check from root down to the full resolved path
    check = Path(resolved.anchor)
    for part in resolved.relative_to(resolved.anchor).parts:
        check = check / part
        if check.is_symlink():
            raise ValueError(f"Symlink in path component not allowed: {check}")

    # Step 4: Ensure resolved path is under an allowed directory
    def _is_under(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    if not any(_is_under(resolved, d) for d in dirs):
        raise ValueError(f"Path not in allowed directories: {raw_path}")

    return resolved


async def _validate_user_path(raw_path: str, payload: dict) -> Path:
    """Validate a path and confirm the signed-in user may read that file."""
    from .storage.result_access import can_read_path

    resolved = _validate_path(raw_path)
    if not await asyncio.to_thread(can_read_path, str(resolved), payload["sub"]):
        raise ValueError(f"Not readable by this user: {raw_path}")
    return resolved


# ── Tile Proxy (bypass browser proxy for external tile servers) ──

_TILE_PROVIDERS = {
    "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "terrain": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
}

# Reason: In-memory rate limiter for tile proxy — prevents abuse of upstream
# tile providers. Stores a list of request timestamps per provider, pruned on
# each request. No external dependencies needed.
_TILE_RATE_LIMIT_MAX = 100       # max requests per window
_TILE_RATE_LIMIT_WINDOW = 10.0   # window in seconds
_tile_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _tile_rate_limited(provider: str) -> bool:
    """Return True if the provider has exceeded the rate limit."""
    now = time.monotonic()
    bucket = _tile_rate_buckets[provider]

    # Prune timestamps older than the window
    cutoff = now - _TILE_RATE_LIMIT_WINDOW
    _tile_rate_buckets[provider] = [t for t in bucket if t > cutoff]
    bucket = _tile_rate_buckets[provider]

    if len(bucket) >= _TILE_RATE_LIMIT_MAX:
        return True

    bucket.append(now)
    return False


# Per-client-IP limiter: the route is intentionally unauthenticated (Leaflet's
# raw <img> tile pipeline cannot carry auth headers, and a query-token would leak
# the JWT into browser cache/history across hundreds of tile URLs). This bounds a
# single client's abuse of the proxy; the per-provider bucket above protects the
# upstream provider in aggregate.
_TILE_IP_RATE_LIMIT_MAX = 200      # max requests per window per client IP
_TILE_IP_BUCKET_CAP = 20_000       # max distinct IPs tracked before a global sweep
# Plain dict (not defaultdict): a mere membership check must not create an entry,
# and empty buckets are evicted so a stream of one-shot IPs cannot grow memory.
_tile_ip_rate_buckets: dict[str, list[float]] = {}


def _prune_ip_buckets(cutoff: float) -> None:
    """Drop IP buckets whose newest timestamp is outside the current window."""
    stale = [ip for ip, ts in _tile_ip_rate_buckets.items() if not ts or ts[-1] <= cutoff]
    for ip in stale:
        del _tile_ip_rate_buckets[ip]


def _tile_ip_rate_limited(client_ip: str) -> bool:
    """Return True if the client IP has exceeded its per-window request budget.

    Memory is bounded: empty buckets are evicted and a global sweep caps the
    tracked-IP set, so spoofed one-shot IPs (e.g. an IPv6 /64) cannot leak.

    NOTE: client_ip is the TCP peer (request.client.host). Behind a reverse
    proxy every client shares the proxy's IP — run uvicorn with --proxy-headers
    and a trusted forwarded-allow-ips list for true per-client limiting.
    """
    now = time.monotonic()
    cutoff = now - _TILE_RATE_LIMIT_WINDOW
    if len(_tile_ip_rate_buckets) > _TILE_IP_BUCKET_CAP:
        _prune_ip_buckets(cutoff)

    bucket = [t for t in _tile_ip_rate_buckets.get(client_ip, ()) if t > cutoff]
    over = len(bucket) >= _TILE_IP_RATE_LIMIT_MAX
    if not over:
        bucket.append(now)
    if bucket:
        _tile_ip_rate_buckets[client_ip] = bucket
    else:
        _tile_ip_rate_buckets.pop(client_ip, None)
    return over


@router.get("/tile-proxy/{provider}/{z}/{y}/{x}")
async def tile_proxy(provider: str, z: int, y: int, x: int, request: Request):
    """Proxy tile requests to bypass browser proxy authentication issues.

    Intentionally public (see _tile_ip_rate_limited): Leaflet loads tiles via a
    raw <img> pipeline that cannot send Authorization headers, so auth is
    enforced by a per-IP rate limit and a provider whitelist, not a token.
    """
    import httpx
    from fastapi.responses import Response

    # Input validation: reject invalid tile coordinates before any upstream
    # fetch. z is capped at 19 (real basemap providers stop there); x and y must
    # fall inside the 2^z grid, so out-of-range indices can't burn the provider
    # budget on requests that could only ever 404.
    if z < 0 or z > 19 or x < 0 or y < 0:
        return JSONResponse({"error": "Invalid tile coordinates"}, status_code=400)
    max_index = 1 << z
    if x >= max_index or y >= max_index:
        return JSONResponse({"error": "Invalid tile coordinates"}, status_code=400)

    url_template = _TILE_PROVIDERS.get(provider)
    if not url_template:
        return JSONResponse({"error": f"Unknown provider: {provider}"}, status_code=400)

    # Per-IP rate limiting (checked before any upstream fetch)
    client_ip = request.client.host if request.client else "unknown"
    if _tile_ip_rate_limited(client_ip):
        return JSONResponse(
            {"error": "Rate limit exceeded. Try again shortly."},
            status_code=429,
            headers={"Retry-After": "10"},
        )

    # Rate limiting: max 100 requests per 10s per provider
    if _tile_rate_limited(provider):
        return JSONResponse(
            {"error": "Rate limit exceeded. Try again shortly."},
            status_code=429,
            headers={"Retry-After": "10"},
        )

    url = url_template.format(z=z, y=y, x=x)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": "JonaAI/1.0"})
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "image/png")
                return Response(
                    content=resp.content,
                    media_type=content_type,
                    headers={"Cache-Control": "public, max-age=86400"},
                )
            return Response(content=b"", status_code=resp.status_code)
    except Exception:
        return Response(content=b"", status_code=502)


def _downsample_for_preview(resolved) -> str:
    """Memory guard: downsample very large rasters to a temp GeoTIFF before
    rendering. Returns the path to render — the original when it is small
    enough or rasterio is unavailable."""
    MAX_RENDER_PIXELS = 16_000_000  # 4000x4000 = 16M pixels max
    render_path = str(resolved)
    try:
        import rasterio
        from rasterio.enums import Resampling
        with rasterio.open(render_path) as _check:
            _total_px = _check.width * _check.height
            if _total_px > MAX_RENDER_PIXELS:
                # Downsample to fit within limit
                scale = (MAX_RENDER_PIXELS / _total_px) ** 0.5
                new_w = max(1, int(_check.width * scale))
                new_h = max(1, int(_check.height * scale))
                logger.info(
                    f"[render-raster] Downsampling {_check.width}x{_check.height} -> {new_w}x{new_h} for preview"
                )
                data = _check.read(
                    out_shape=(_check.count, new_h, new_w),
                    resampling=Resampling.bilinear,
                )
                profile = _check.profile.copy()
                profile.update(width=new_w, height=new_h, transform=_check.transform * _check.transform.scale(
                    _check.width / new_w, _check.height / new_h
                ))
                # Write downsampled to temp file
                import tempfile
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tif")
                os.close(tmp_fd)
                with rasterio.open(tmp_path, "w", **profile) as dst:
                    dst.write(data)
                render_path = tmp_path
    except ImportError:
        pass  # rasterio not available — skip check
    return render_path


@router.head("/render-raster")
async def render_raster_head(
    path: str, colormap: str = "viridis", payload: dict = Depends(require_auth_or_query_token)
):
    """Handle HEAD request for render-raster (used by Leaflet to probe images)."""
    from fastapi.responses import Response
    try:
        await _validate_user_path(path, payload)
    except ValueError:
        return Response(content=b"", status_code=403)
    return Response(content=b"", media_type="image/png", headers={"Content-Length": "0"})


@router.get("/render-raster")
async def render_raster(
    path: str, colormap: str = "viridis", payload: dict = Depends(require_auth_or_query_token)
):
    """Re-render a raster with a different colormap and return the actual image."""
    import base64
    from fastapi.responses import Response

    print(f"[render-raster] GET request: path={path}, colormap={colormap}")

    try:
        resolved = await _validate_user_path(path, payload)
    except ValueError as e:
        print(f"[render-raster] Blocked path: {e}")
        return JSONResponse({"error": "Path not allowed"}, status_code=403)

    try:
        render_path = _downsample_for_preview(resolved)

        map_component = MapComponent()
        try:
            result = await map_component.create_raster_map(geotiff_path=render_path, colormap=colormap)

            if result and "spec" in result:
                layers = result["spec"].get("layers", [])
                if layers and "url" in layers[0]:
                    data_url = layers[0]["url"]
                    if data_url.startswith("data:image/png;base64,"):
                        base64_data = data_url.split(",")[1]
                        image_bytes = base64.b64decode(base64_data)
                        logger.info(f"[render-raster] Returning PNG image: {len(image_bytes)} bytes")
                        return Response(content=image_bytes, media_type="image/png")
                    return JSONResponse({"url": data_url})

            logger.warning("[render-raster] Failed to render: no layers found")
            return JSONResponse({"error": "Failed to render"}, status_code=500)
        finally:
            # Clean up temp downsampled file
            if render_path != str(resolved):
                try:
                    os.unlink(render_path)
                except OSError:
                    pass

    except Exception as e:
        ref = str(uuid.uuid4())[:8]
        logger.exception(f"[render-raster] ref={ref} Error")
        return JSONResponse({"error": "Internal server error", "ref": ref}, status_code=500)


# ── Raster Data Endpoints ──

@router.get("/raster-stats")
async def raster_stats(path: str, payload: dict = Depends(require_auth)):
    """Return statistical summary of a GeoTIFF raster."""
    import rasterio
    import numpy as np

    try:
        resolved = await _validate_user_path(path, payload)
    except ValueError as e:
        print(f"[raster-stats] Blocked path: {e}")
        return JSONResponse({"error": "Path not allowed"}, status_code=403)

    try:
        with rasterio.open(str(resolved)) as src:
            data = src.read(1)
            nodata = src.nodata

            # Get valid data (exclude nodata and NaN)
            if np.issubdtype(data.dtype, np.floating):
                valid = data[~np.isnan(data)]
            else:
                valid = data.ravel()
            if nodata is not None:
                valid = valid[~np.isclose(valid.astype(float), float(nodata))]

            total = data.size
            valid_count = len(valid)

            return {
                "min": float(valid.min()) if valid_count > 0 else None,
                "max": float(valid.max()) if valid_count > 0 else None,
                "mean": float(valid.mean()) if valid_count > 0 else None,
                "std": float(valid.std()) if valid_count > 0 else None,
                "median": float(np.median(valid)) if valid_count > 0 else None,
                "nodata_percent": round((1 - valid_count / total) * 100, 1) if total > 0 else 0,
                "valid_pixels": int(valid_count),
                "total_pixels": int(total),
                "crs": str(src.crs) if src.crs else None,
                "resolution": [abs(float(src.res[0])), abs(float(src.res[1]))],
                "width": src.width,
                "height": src.height,
                "bands": src.count,
                "dtype": str(data.dtype),
                "bounds": list(src.bounds),
                "file_size_bytes": os.path.getsize(str(resolved)),
            }
    except Exception as e:
        ref = str(uuid.uuid4())[:8]
        logger.exception(f"[raster-stats] ref={ref} Error")
        return JSONResponse({"error": "Internal server error", "ref": ref}, status_code=500)


@router.get("/pixel-value")
async def pixel_value(path: str, lat: float, lng: float, payload: dict = Depends(require_auth)):
    """Return the raw raster value at a given lat/lng coordinate."""
    import rasterio
    from rasterio.crs import CRS

    try:
        resolved = await _validate_user_path(path, payload)
    except ValueError as e:
        print(f"[pixel-value] Blocked path: {e}")
        return JSONResponse({"error": "Path not allowed"}, status_code=403)

    try:
        with rasterio.open(str(resolved)) as src:
            # Transform coordinates if raster CRS is not EPSG:4326
            coord_x, coord_y = lng, lat
            if src.crs and not src.crs.to_epsg() == 4326:
                from rasterio.warp import transform
                xs, ys = transform(CRS.from_epsg(4326), src.crs, [lng], [lat])
                coord_x, coord_y = xs[0], ys[0]

            # Sample the raster at the coordinate
            values = list(src.sample([(coord_x, coord_y)]))
            if not values or len(values[0]) == 0:
                return JSONResponse({"error": "No data at location"}, status_code=404)

            val = float(values[0][0])

            # Check for nodata
            import numpy as np
            if src.nodata is not None and np.isclose(val, float(src.nodata)):
                return {"value": None, "lat": lat, "lng": lng, "band": 1, "nodata": True}
            if np.isnan(val):
                return {"value": None, "lat": lat, "lng": lng, "band": 1, "nodata": True}

            return {"value": val, "lat": lat, "lng": lng, "band": 1}
    except Exception as e:
        ref = str(uuid.uuid4())[:8]
        logger.exception(f"[pixel-value] ref={ref} Error")
        return JSONResponse({"error": "Internal server error", "ref": ref}, status_code=500)


# ── Saved Jobs API ──

async def _supabase_auth_proxy(path: str, body: Dict[str, Any], query: str = "") -> JSONResponse:
    """Proxy Supabase Auth calls for browser environments that block direct fetches."""
    supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY") or ""
    if not supabase_url or not anon_key:
        return JSONResponse({"error": "Supabase auth is not configured"}, status_code=503)

    import httpx

    url = f"{supabase_url}/auth/v1/{path}{query}"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        for attempt in range(1, 4):
            try:
                response = await client.post(url, json=body, headers=headers)
                break
            except httpx.HTTPError as exc:
                logger.warning(
                    "Supabase auth proxy transport failed on attempt %s/3: %s",
                    attempt,
                    exc,
                )
                if attempt < 3:
                    await asyncio.sleep(0.25 * attempt)
        else:
            return JSONResponse(
                {
                    "error": "Supabase auth request failed",
                    "error_code": "supabase_auth_unreachable",
                    "msg": "Could not reach Supabase Auth. Check network connectivity and try again.",
                },
                status_code=502,
            )

    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text or "Supabase auth returned a non-JSON response"}
    return JSONResponse(payload, status_code=response.status_code)


@router.post("/api/supabase-auth/signin")
async def proxy_supabase_signin(request: Request):
    body = await request.json()
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    if not email or not password:
        return JSONResponse({"error": "Email and password are required"}, status_code=400)
    return await _supabase_auth_proxy(
        "token",
        {"email": email, "password": password},
        query="?grant_type=password",
    )


@router.post("/api/supabase-auth/signup")
async def proxy_supabase_signup(request: Request):
    body = await request.json()
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    if not email or not password:
        return JSONResponse({"error": "Email and password are required"}, status_code=400)
    from .supabase_auth import email_allowed
    if not email_allowed(email):
        return JSONResponse({"error": "New accounts aren't open on this service."}, status_code=403)
    return await _supabase_auth_proxy("signup", {"email": email, "password": password})


def _require_project_owner(project_id: str, user_id: str, action: str):
    """Return an error JSONResponse unless user_id owns project_id (None = OK)."""
    from .storage.job_archive import get_project
    project = get_project(project_id)
    if not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)
    if project.get("user_id") != user_id:
        return JSONResponse(
            {"error": f"Only the project owner can {action}"}, status_code=403
        )
    return None


def _project_with_role(project_id: str, user_id: str) -> tuple[Optional[dict], Optional[str]]:
    """Return a project and caller role when the user owns or belongs to it."""
    from .storage.job_archive import get_project

    project = get_project(project_id)
    if not project:
        return None, None
    if project.get("user_id") == user_id:
        return project, "owner"

    from openeo_ai import _pg_compat

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?::uuid",
            (project_id, user_id),
        )
        row = cursor.fetchone()
        if row:
            return project, row["role"]
    except Exception as exc:
        logger.warning("Failed to resolve project membership for %s: %s", project_id, exc)
    finally:
        conn.close()
    return project, None


def _saved_job_owner_for_read(save_id: str, user_id: str) -> Optional[str]:
    """Resolve the saved-result owner when the caller may read the analysis."""
    from .storage.job_archive import get_job

    # A lost result with an offsite copy still has an owner; it is restored after this check.
    job = get_job(save_id, require_file=False)
    if not job:
        return None

    owner_user_id = job.get("user_id")
    if owner_user_id == user_id:
        return owner_user_id

    project_id = job.get("project_id")
    if project_id:
        _, role = _project_with_role(project_id, user_id)
        if role and owner_user_id:
            return owner_user_id
    return None

@router.get("/saved-jobs")
async def list_saved_jobs(
    limit: int = 50,
    project_id: Optional[str] = None,
    payload: dict = Depends(require_auth),
):
    from .storage.job_archive import list_jobs
    user_id = payload["sub"]
    owner_user_id = user_id
    if project_id:
        project, role = _project_with_role(project_id, user_id)
        if not project or not role:
            return JSONResponse({"error": "Not found"}, status_code=404)
        owner_user_id = project.get("user_id") or user_id
    jobs = list_jobs(limit=limit, project_id=project_id, user_id=owner_user_id, include_restorable=True)
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/saved-jobs/{save_id}")
async def get_saved_job(save_id: str, payload: dict = Depends(require_auth)):
    from .storage.job_archive import get_job_detail, restore_saved_result

    owner_user_id = _saved_job_owner_for_read(save_id, payload["sub"])
    if owner_user_id:
        # Opening a lost result restores it from its offsite copy, off the event loop.
        await asyncio.to_thread(restore_saved_result, save_id)
    job = get_job_detail(save_id, user_id=owner_user_id) if owner_user_id else None
    if job:
        return job
    return JSONResponse({"error": "Not found"}, status_code=404)


@router.put("/saved-jobs/{save_id}/visualizations")
async def put_saved_job_visualizations(
    save_id: str, request: Request, payload: dict = Depends(require_auth)
):
    """Persist the run's non-map visualizations + report sections for a saved result.

    Owner-only (passes the requesting user_id to save_visualizations, which gates
    on get_job ownership). Replayed by load_saved_job so Charts/Table/Report/Process
    tabs repopulate on "Load on Map".
    """
    if not re.match(r"^[a-f0-9]{8}$", save_id):
        return JSONResponse({"error": "Invalid save_id format"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid body"}, status_code=400)

    visualizations = body.get("visualizations") or []
    report_sections = body.get("reportSections") or []
    if not isinstance(visualizations, list) or not isinstance(report_sections, list):
        return JSONResponse({"error": "Invalid body"}, status_code=400)

    from .storage.job_archive import save_visualizations
    ok = save_visualizations(
        save_id,
        {"visualizations": visualizations, "reportSections": report_sections},
        user_id=payload["sub"],
    )
    if ok:
        return {"ok": True, "save_id": save_id}
    return JSONResponse({"error": "Not found"}, status_code=404)


@router.delete("/saved-jobs/{save_id}")
async def delete_saved_job(save_id: str, payload: dict = Depends(require_auth)):
    from .storage.job_archive import delete_job
    success = delete_job(save_id, user_id=payload["sub"])
    if success:
        return {"deleted": True, "save_id": save_id}
    return JSONResponse({"error": "Not found"}, status_code=404)


@router.patch("/saved-jobs/{save_id}/project")
async def assign_saved_job_project(save_id: str, request: Request, payload: dict = Depends(require_auth)):
    body = await request.json()
    project_id = body.get("project_id")
    from .storage.job_archive import assign_job_to_project
    ok = assign_job_to_project(save_id, project_id, user_id=payload["sub"])
    if ok:
        return {"ok": True}
    return JSONResponse({"error": "Not found"}, status_code=404)


@router.get("/saved-jobs/{save_id}/files/{filename:path}")
async def serve_saved_job_file(save_id: str, filename: str, payload: dict = Depends(require_auth)):
    """Serve a file from a saved job's result directory."""
    import mimetypes
    if not re.match(r'^[a-f0-9]{8}$', save_id):
        raise HTTPException(status_code=400, detail="Invalid save_id format")
    from .storage.job_archive import get_job
    owner_user_id = _saved_job_owner_for_read(save_id, payload["sub"])
    if not owner_user_id or not get_job(save_id, user_id=owner_user_id):
        return JSONResponse({"error": "File not found"}, status_code=404)
    results_dir = Path.home() / ".openeo_jobs" / "results" / save_id
    raw_file_path = str(results_dir / filename)
    try:
        validated = _validate_path(raw_file_path, allowed_dirs=[results_dir.resolve()])
    except ValueError as e:
        print(f"[serve-saved-job-file] Blocked path: {e}")
        return JSONResponse({"error": "Path not allowed"}, status_code=403)
    if not validated.exists() or not validated.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    media_type = mimetypes.guess_type(str(validated))[0] or "application/octet-stream"
    return FileResponse(str(validated), media_type=media_type, filename=filename)


# ── Projects API ──

@router.get("/api/projects")
async def api_list_projects(payload: dict = Depends(require_auth)):
    """List projects the current user owns or is a member of.

    Phase 15: scope projects by user_id and project_members membership.
    Users see projects where:
      - projects.user_id == JWT sub (they own it), OR
      - there's a project_members row linking them to the project
    """
    from .storage.job_archive import list_projects, list_unassigned_files
    from openeo_ai import _pg_compat

    user_id = payload["sub"]
    # list_projects() returns ALL projects; filter here for backward compat.
    all_projects = list_projects()

    # Pull member project IDs for the current user
    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "SELECT project_id, role FROM project_members WHERE user_id = ?::uuid",
            (user_id,),
        )
        member_map = {row["project_id"]: row["role"] for row in cursor.fetchall()}
    finally:
        conn.close()

    # Keep projects where user_id matches OR user is a member
    filtered = [
        {**p, "role": member_map.get(p["id"], "owner" if p.get("user_id") == user_id else None)}
        for p in all_projects
        if p.get("user_id") == user_id or p["id"] in member_map
    ]
    unassigned = list_unassigned_files(user_id=user_id)
    return {"projects": filtered, "unassigned_files": unassigned}


@router.get("/api/projects/{project_id}/members")
async def api_list_project_members(project_id: str, payload: dict = Depends(require_auth)):
    """List members of a project."""
    from openeo_ai import _pg_compat

    project, role = _project_with_role(project_id, payload["sub"])
    if not project or not role:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            """
            SELECT user_id, role, invited_at, invited_by
            FROM project_members
            WHERE project_id = ?
            ORDER BY invited_at
            """,
            (project_id,),
        )
        members = [
            {
                "user_id": str(row["user_id"]),
                "role": row["role"],
                "invited_at": row["invited_at"].isoformat() if row["invited_at"] else None,
                "invited_by": str(row["invited_by"]) if row["invited_by"] else None,
            }
            for row in cursor.fetchall()
        ]
        return {"members": members}
    finally:
        conn.close()


@router.post("/api/projects/{project_id}/members")
async def api_add_project_member(
    project_id: str,
    request: Request,
    payload: dict = Depends(require_auth),
):
    """Add a member to a project. Only the project owner can add members."""
    from openeo_ai import _pg_compat

    user_id = payload["sub"]
    err = _require_project_owner(project_id, user_id, "add members")
    if err:
        return err

    body = await request.json()
    new_user = body.get("user_id")
    role = body.get("role", "viewer")
    if not new_user:
        return JSONResponse({"error": "user_id required"}, status_code=400)
    if role not in ("owner", "editor", "viewer"):
        return JSONResponse({"error": "Invalid role"}, status_code=400)

    conn = _pg_compat.connect()
    try:
        conn.execute(
            """
            INSERT INTO project_members (project_id, user_id, role, invited_by)
            VALUES (?, ?::uuid, ?, ?::uuid)
            ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role
            """,
            (project_id, new_user, role, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "project_id": project_id, "user_id": new_user, "role": role}


@router.delete("/api/projects/{project_id}/members/{member_user_id}")
async def api_remove_project_member(
    project_id: str,
    member_user_id: str,
    payload: dict = Depends(require_auth),
):
    """Remove a member from a project. Only the owner can remove members."""
    from openeo_ai import _pg_compat

    user_id = payload["sub"]
    err = _require_project_owner(project_id, user_id, "remove members")
    if err:
        return err

    conn = _pg_compat.connect()
    try:
        cursor = conn.execute(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?::uuid",
            (project_id, member_user_id),
        )
        conn.commit()
        return {"removed": cursor.rowcount > 0}
    finally:
        conn.close()


@router.post("/api/projects")
async def api_create_project(request: Request, payload: dict = Depends(require_auth)):
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "Name is required"}, status_code=400)
    project_id = body.get("id") or str(uuid.uuid4())[:36]
    description = body.get("description", "")
    # Reason: Supabase JWT 'sub' claim is the user UUID — used for ownership
    user_id = payload["sub"]
    from .storage.job_archive import create_project
    project = create_project(project_id, name, description, user_id=user_id)
    if project:
        return project
    return JSONResponse({"error": "Failed to create project"}, status_code=500)


@router.get("/api/projects/{project_id}")
async def api_get_project(project_id: str, payload: dict = Depends(require_auth)):
    project, role = _project_with_role(project_id, payload["sub"])
    if project and role:
        return {**project, "role": role}
    return JSONResponse({"error": "Not found"}, status_code=404)


@router.get("/api/projects/{project_id}/detail")
async def api_get_project_detail(project_id: str, payload: dict = Depends(require_auth)):
    """Get project with fully enriched analyses (metadata, statistics, job_files)."""
    from .storage.job_archive import get_job_detail
    user_id = payload["sub"]
    project, role = _project_with_role(project_id, user_id)
    if not project or not role:
        return JSONResponse({"error": "Not found"}, status_code=404)
    owner_user_id = project.get("user_id") or user_id

    enriched_files = []
    for f in project.get("files", []):
        detail = get_job_detail(f["save_id"], user_id=owner_user_id)
        if detail:
            enriched_files.append(detail)
        else:
            enriched_files.append(f)

    project["files"] = enriched_files
    project["role"] = role
    return project


@router.patch("/api/projects/{project_id}")
async def api_update_project(project_id: str, request: Request, payload: dict = Depends(require_auth)):
    body = await request.json()
    from .storage.job_archive import update_project
    project = update_project(
        project_id,
        name=body.get("name"),
        description=body.get("description"),
        user_id=payload["sub"],
    )
    if project:
        return project
    return JSONResponse({"error": "Not found"}, status_code=404)


@router.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str, payload: dict = Depends(require_auth)):
    from .storage.job_archive import delete_project
    if delete_project(project_id, user_id=payload["sub"]):
        return {"deleted": True}
    return JSONResponse({"error": "Not found"}, status_code=404)
