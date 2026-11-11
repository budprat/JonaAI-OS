# JonaAI Architecture

Last updated: 2026-11-11

JonaAI is an AI-powered Earth Observation and persistent Earth Intelligence platform. It combines a conversational AI analysis interface, an OpenEO-compatible processing backend, STAC catalog discovery, saved analysis archives, and durable monitoring entities for assets, watches, observations, evidence packages, baselines, signals, and deliveries.

The active product path is:

- `openeo_ai/`: AI web interface, SDK bridge, tools, storage, APIs, tests, and backend routes.
- `openeo_app/`: OpenEO-compatible API backend, execution engine, and database migrations.
- `openeo_ai/frontend/`: active React, Vite, and TypeScript frontend.

## Runtime Topology

Two local services serve the active app.

Backend on port `8080`:

- ASGI dispatcher: `openeo_ai.web_interface:asgi_app`.
- Routes `/openeo/*` and `/health*` to `openeo_app/`.
- Routes everything else to the AI FastAPI app in `openeo_ai/`.
- Optional Earth Intelligence worker can run inside the web process.

Frontend on port `3000`:

- Vite dev server from `openeo_ai/frontend/`.
- Proxies API, WebSocket, saved-job, raster, and OpenEO requests to the backend.

Always start uvicorn with `openeo_ai.web_interface:asgi_app`. Starting `openeo_ai.web_interface:app` only serves the AI FastAPI instance and bypasses the OpenEO dispatcher.

## High-Level Flow

```text
React frontend
  -> FastAPI AI app and WebSocket
  -> SDK bridge and OpenEO AI tools
  -> OpenEO API dispatcher
  -> process graph execution, STAC loading, raster/vector processing
  -> saved analysis archive
  -> visualization extraction
  -> frontend map/chart/table/report views
```

Persistent monitoring uses a separate durable spine:

```text
EarthAsset -> EarthWatch -> CatalogItem -> EarthObservation
  -> EvidencePackage -> EarthBaseline -> EarthSignal -> Delivery
```

## Main Modules

| Module | Responsibility |
| --- | --- |
| `openeo_ai/web_interface.py` | FastAPI app, WebSocket handler, router wiring, ASGI dispatcher, optional worker startup |
| `openeo_ai/web_routes.py` | Saved analyses, projects, file serving, raster rendering, SPA catch-all |
| `openeo_ai/api/auth.py` | Supabase/API-key auth helpers and scope checks for public API routes |
| `openeo_ai/api/router.py` | Public API v1 analyze/job/batch endpoints |
| `openeo_ai/sdk/client.py` | OpenEO AI client, tool definitions, system prompt path |
| `openeo_ai/sdk/claude_sdk_bridge.py` | Claude Agent SDK bridge and WebSocket streaming; per-query and daily spend limits, idle session reaping |
| `openeo_ai/sdk/method_gate.py` | Checks a query's method before running it (for example, a burn-severity comparison needs pre- and post-event periods) |
| `openeo_ai/access_guard.py` | Validates the Cloudflare Access token on proxied requests |
| `openeo_ai/storage/result_access.py` | Per-user read checks for jobs, saved results, result files and evidence artifacts |
| `openeo_ai/sdk/viz_extractor.py` | Tool-result to visualization extraction |
| `openeo_ai/tools/` | OpenEO, spectral index, temporal, export, QA, GeoAI, and saved-analysis tools |
| `openeo_ai/storage/models.py` | SQLAlchemy models, including saved results and Earth Intelligence tables |
| `openeo_ai/storage/job_archive.py` | Saved analysis archive, projects, project files, saved-job detail serialization, visualization bundle persistence |
| `openeo_ai/intelligence/repository.py` | Durable Earth Intelligence repository and serialization layer |
| `openeo_ai/intelligence/catalog.py` | STAC normalization and observation materialization |
| `openeo_ai/intelligence/worker.py` | Due-watch enqueueing, observation claiming, OpenEO execution/resume, evidence, signals, deliveries |
| `openeo_ai/intelligence/routes.py` | Authenticated app-facing Earth Intelligence API |
| `openeo_ai/intelligence/api_v1.py` | Public `/api/v1` Earth Intelligence controls |
| `openeo_app/app.py` | OpenEO FastAPI backend |
| `openeo_app/execution/executor.py` | Process graph executor and NumPy/xarray compatibility shims |
| `openeo_app/processes/load_collection.py` | STAC loading through odc-stac: catalog routing, scene selection, size limit |
| `openeo_app/core/extent.py`, `limits.py`, `job_notes.py` | Extent normalization and load grid, configured limits, notes that travel with a job's result |
| `openeo_app/processes/resample_fix.py`, `aggregate_fix.py` | Corrections to upstream resampling and spatial aggregation |
| `openeo_ai/frontend/src/hooks/usePlatformData.ts` | REST/Supabase frontend data hooks for projects, saved analyses, and Earth Intelligence |

## Saved Analyses, Assets, Observations, Evidence

The archive and monitoring layers are related but distinct:

- `SavedResult` is a saved analysis artifact produced by an AI/OpenEO run.
- `EarthAsset` is a durable monitored AOI or real-world asset.
- `EarthWatch` describes a recurring or manual intelligence contract attached to an asset.
- `EarthObservation` is one concrete watch run or run opportunity.
- `EvidencePackage` links an observation to saved artifacts, provenance, STAC inputs, statistics, and process graph evidence.

Dashboard detail pages should treat archived outputs as saved analyses. Canonical frontend routes are:

```text
/dashboard/analysis/:analysisId
/dashboard/analysis/:analysisId/evidence
```

Legacy aliases remain for compatibility:

```text
/dashboard/asset/:analysisId
/dashboard/asset/:analysisId/evidence
/dashboard/evidence/:analysisId
```

The saved-job detail API returns `evidence_packages` and an `earth_intelligence` summary so the UI can show whether an analysis is standalone or evidence-backed.

Loading a saved analysis onto the map replays its full result tab set, not just the raster. On turn completion the frontend persists that run's non-map visualizations (charts, tables, process graph) and report sections via `PUT /saved-jobs/{save_id}/visualizations`, stored as `visualizations.json` beside the GeoTIFF. `load_saved_job` emits the map from the saved GeoTIFF, then replays the bundle as `visualization` events plus one `report_sections` WebSocket event. Maps are intentionally excluded from the bundle because their `/render-raster` URLs point at working files that expire after a day; the map always reloads from the persistent GeoTIFF, and saves without a bundle fall back to map-only.

## Authentication And Access

The active auth model is Supabase bearer tokens plus scoped API keys.

- Frontend auth comes from `openeo_ai/frontend/src/components/auth/useAuth.ts`.
- Avoid new direct `localStorage.getItem("jonaai-token")` usage.
- Backend route auth uses Supabase JWT payloads and extracts `payload["sub"]` as the user id.
- API keys support exact scopes, `admin`, and `*`.
- Public `/api/v1` routes must require Supabase auth or scoped API keys as appropriate.

Project and saved-analysis access rules:

- Project owners can create, update, delete, and manage members.
- Project members can read project detail, list members, and read/download saved analyses attached to accessible projects.
- Saved analyses without a project remain owner-scoped.
- Saved-result file serving validates the save id, checks read access, and validates the resolved path before serving a file.
- Jobs, batches, evidence, result files and chat sessions are all checked against the requesting user (`storage/result_access.py`). The agent's tools run with the signed-in user's identity; `JONA_REQUIRE_TOOL_USER` refuses tool calls without one.
- API keys carry scopes; a key without a scope can't reach routes that need it. Signed-in users get `SIGNED_IN_SCOPES`.
- `JONA_ALLOWED_EMAILS` restricts which accounts a deployment accepts, applied wherever a Supabase token is verified.
- `openeo_ai/access_guard.py` validates the Cloudflare Access token (`JONA_ACCESS_MODE=observe|enforce`) on requests that came through the proxy. Loopback calls skip it.
- Bearer tokens are kept out of logs and are only sent to the app's own origin.
- Outbound requests to user-supplied URLs (webhooks, Slack) connect only to the address that passed the SSRF check.

## Status Lifecycles

Every durable status has one declared lifecycle in `openeo_ai/lifecycle.py`: its states, the moves between them, and the statuses an API caller may set. Writers check moves against it, and conditional updates keep a settled record from being overwritten by a late writer. Examples: a cancelled batch item stays cancelled when its executor reports back, and a resolved signal can't keep its resolved time after being reopened. Illegal moves answer 409 with the allowed ones. Migration `phase19_status_lifecycles` enforces the same states with CHECK constraints, and a test fails if the two drift apart.

OpenEO jobs (`openeo_app/registers/jobs.py`) follow the same rule with their own table, `JOB_MOVES`. Every status write is a conditional `UPDATE` through `_move_job_status`, so a cancel that loses to completion keeps the results, and two starts can't both launch.

Deliveries record `claimed_at` and `next_attempt_at`. A send abandoned mid-flight is reclaimed by its claim time, and failed attempts back off from one minute up to an hour.

## Untrusted Process Graphs

A process graph can come from the model or straight from an API caller, so the executor treats it as untrusted. `_UNSAFE_PROCESSES` in `openeo_app/execution/executor.py` removes processes that fetch URLs or run code (`load_url`, `load_stac`, `run_udf`, …). Don't register new processes that take a URL.

## Gates Before Work Starts

The agent's tool calls pass through `can_use_tool` in the bridge. For every tool that starts backend work (`_JOB_START_TOOLS`) it checks:

1. **The graph's structure:** no empty graph, no node without a `process_id`, no reference to a missing node, and no cycle. These are faults under any OpenEO backend, so they need no process registry.
2. **The area,** in every shape an extent arrives in: a `load_collection` extent, a `spatial_extent` or an `aoi` bbox. It is measured across the dateline. Above 10° the user must confirm; above 50° the request is refused. Bounds that can't be read are refused.

The SCF compiler refuses index requests it can't build correctly rather than producing a graph that reads the wrong bands. Tools that can't do their job say so: GeoAI without a model returns `geoai_model_unavailable`, and Earth-Agent tools are offered only where Earth-Agent is installed. Details and names are in CLAUDE.md under "Graph safety" and "GeoAI models".

## Data Loading And Limits

- Collections route to AWS Earth Search, except `landsat-c2-l2` and `sentinel-1-grd`, which load free from Microsoft Planetary Computer (signed items). `sentinel-1-grd` is served as `sentinel-1-rtc`, the calibrated, terrain-corrected product.
- A load uses at most `MAX_STAC_ITEMS` acquisition dates, spread across the requested period; all tiles of one date count once. The result notes how many dates were used.
- The load grid keeps pixels roughly square on the ground at any latitude (`core/extent.py`), and extents in projected CRSs are converted to degrees first.
- `OPENEO_MAX_LOAD_ELEMENTS` caps the cube size. A cube up to 4 times over loads at coarser resolution with a note; beyond that the job is refused with an explanation. `OPENEO_MAX_CONCURRENT_JOBS` bounds jobs running at once, synchronous `POST /result` included: a sync job waits up to 300 s for a slot, then gets 503. A job that times out or is cancelled reports it at once but keeps its slot until its computation stops, because a thread can't be killed.
- API routes check the tier's concurrency limit before starting work, and a batch counts each of its locations.
- Spend is bounded per query (`JONA_MAX_QUERY_USD`), per user per day (`JONA_USER_DAILY_USD`) and service-wide per month (`JONA_MONTHLY_USD`), recorded through `openeo_ai/budget.py`.

Defaults are in CLAUDE.md under "Limits"; production values in `docs/deployment.md`.

## Chat Runs

A chat query runs as a `_QueryRun` in `web_interface.py`, independent of the WebSocket that started it:

- Events are numbered and buffered. If the connection drops, the query keeps running; the client reconnects and receives what it missed.
- Each run is logged under `JONA_RUN_LOG_DIR`. After a restart, `_recover_run` replays the log to its owner and says the run was cut off.
- A session runs one query at a time. Only the session's owner can attach to it.
- When the sign-in token expires, the server closes the socket with code 4401 and the client reconnects with a fresh token.
- Idle agent sessions end after `JONA_SDK_SESSION_TTL_SECONDS` (at most `JONA_SDK_SESSION_MAX_COUNT` kept) and resume from history on the next message.
- Stop ends the turn at once. The planner and specialist loop check it, and the agent winds down behind the turn so its cost is still recorded. It is cut off after 60 seconds.
- A clarification ends with `clarification_closed`, however it ends. A reconnecting tab isn't asked a closed question again, and tool events carry `tool_use_id`, so parallel tools pair correctly.
- A run ends with exactly one `done`: the run drops repeats and sends one if its task ends without any. Errors inside a run carry its `run_id` and don't end the turn by themselves.
- The client's connection has one explicit state (`useWebSocket` `connectionState`). If Cloudflare Access answers with its login page, the client stops retrying and offers a fresh sign-in.

## Database And Migrations

Postgres migrations live under:

```text
openeo_app/psql/alembic/versions/
```

The Earth Intelligence migration is:

```text
openeo_app/psql/alembic/versions/phase17_earth_intelligence.py
```

Durable platform data should use SQLAlchemy models and repository patterns. `openeo_ai/_pg_compat.py` supports legacy SQLite-shaped code against Postgres and explicit local/test SQLite paths, but new Earth Intelligence features should prefer the SQLAlchemy repository.

## Worker Model

The Earth Intelligence worker runs by default; only an explicit `0/false/no/off` disables it:

```bash
export EARTH_INTELLIGENCE_WORKER=false      # opt out
export EARTH_INTELLIGENCE_POLL_INTERVAL=15  # poll cadence (seconds)
```

Manual tick endpoint:

```text
POST /api/intelligence/worker/tick
```

Worker responsibilities:

- Enqueue due watches.
- Claim queued or stale running observations.
- Create or resume OpenEO jobs.
- Defer still-running OpenEO jobs with `next_attempt_at`.
- Archive results into `SavedResult`.
- Create `EvidencePackage`, update baselines, create signals, and queue deliveries.
- Classify transient versus permanent failures.
- Requeue retryable failures with exponential backoff until `EARTH_INTELLIGENCE_MAX_ATTEMPTS`.

Relevant worker knobs:

```text
EARTH_INTELLIGENCE_STALE_LOCK_SECONDS
EARTH_INTELLIGENCE_MAX_ATTEMPTS
EARTH_INTELLIGENCE_RETRY_BASE_DELAY
EARTH_INTELLIGENCE_RETRY_MAX_DELAY
EARTH_INTELLIGENCE_JOB_TIMEOUT
EARTH_INTELLIGENCE_JOB_POLL_INTERVAL
```

Deliveries are dispatched to webhook/Slack (HTTP) and email (SMTP, `JONA_SMTP_*`) with retry/backoff and terminal `sent`/`failed` statuses.

## Frontend Architecture

The active frontend is `openeo_ai/frontend/`.

Key points:

- `src/App.tsx` owns authenticated app routes and dashboard routes.
- `src/components/geo/AppLayout.tsx` owns Geo Hub navigation and detail breadcrumbs.
- `src/hooks/usePlatformData.ts` is the canonical REST/Supabase data layer.
- `src/hooks/useConvex.ts` is only a backward-compatible alias shim.
- Visualization components lazy-load in tests; use async queries such as `findByTestId` when asserting lazy-rendered viewers.

## OpenEO And Raster Gotchas

- STAC/xarray data often uses `time`, `latitude`, `longitude`; OpenEO process graphs use `t`, `y`, `x`.
- Keep `DIMENSION_ALIASES` behavior intact.
- SCL bands are integer classes; do not apply reflectance scaling to SCL/classification bands.
- Avoid `.loc[...] = ...` mutation on Dask-backed xarray data. Use functional transforms such as `xr.where()`.
- `openeo_app/execution/executor.py` contains NumPy 2 and openeo-processes-dask compatibility shims.
- Do not use `str(RootModel)` when the payload is needed; access `.root`.
- Colormap/index behavior may need synchronized updates in backend render, frontend map, frontend legend, and visualization extraction.

## Commands

Production deployment is described in `docs/deployment.md`.

Start both services:

```bash
bash start-local.sh
```

Manual backend:

```bash
export PYTHONPATH="$PWD:$PWD/openeo-fastapi${PYTHONPATH:+:$PYTHONPATH}"
export ALEMBIC_DIR="$PWD/openeo_app/psql"
.venv/bin/python3 -m uvicorn openeo_ai.web_interface:asgi_app --host 127.0.0.1 --port 8080 --reload
```

Manual frontend:

```bash
cd openeo_ai/frontend
npm run dev
```

Backend tests:

```bash
OPENEO_DEV_MODE=true .venv/bin/python -m pytest openeo_ai/tests -q
```

Focused Earth Intelligence tests:

```bash
OPENEO_DEV_MODE=true .venv/bin/python -m pytest openeo_ai/tests/test_earth_intelligence_platform.py -q
```

Frontend tests and build:

```bash
cd openeo_ai/frontend
npm run test:run
npm run build
```

## Current Gaps

- True orbital pass prediction.
- Case and intervention workflows beyond analysis-derived action items.
- Domain packs, marketplace scoring, and evaluation loops.
- Deeper production observability around worker throughput, retry rates, delivery outcomes, and API boundary denials.
- A per-graph element limit, and a hard cancel for running jobs.
- State machines: the gaps found on 2026-11-08 are closed. One limit remains: a stopped agent that hasn't wound down within 60 seconds is cut off, and that turn's cost goes unrecorded.
