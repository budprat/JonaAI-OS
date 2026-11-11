# CLAUDE.md

Last updated: 2026-11-11

This file guides coding agents working in this repository.

## Current Product Path

JonaAI is an AI-powered Earth Observation and persistent Earth Intelligence platform. The active app is:

- `openeo_ai/`: AI web interface, SDK bridge, tools, storage, APIs, tests, and active frontend folder.
- `openeo_app/`: OpenEO-compatible backend and database migrations.
- `openeo_ai/frontend/`: active React + Vite + TypeScript frontend.

## Runtime Architecture

Two local services serve the active app:

1. Backend on port 8080:
   - ASGI dispatcher: `openeo_ai.web_interface:asgi_app`
   - Routes `/openeo/*` and `/health*` to `openeo_app/`
   - Routes everything else to the AI FastAPI app in `openeo_ai/`
2. Frontend on port 3000:
   - Vite dev server from `openeo_ai/frontend/`

Use `asgi_app`, not `app`, when starting uvicorn. `app` is only the AI FastAPI instance; `asgi_app` is the combined dispatcher required for OpenEO routes.

## Persistent Earth Intelligence

The new durable platform spine is:

```text
Asset -> Watch -> Catalog Item -> Observation -> Evidence Package -> Baseline -> Signal -> Delivery
```

Important files:

| File | Purpose |
| --- | --- |
| `openeo_ai/storage/models.py` | SQLAlchemy models, including Earth Intelligence tables |
| `openeo_ai/intelligence/schemas.py` | Pydantic request/response schemas |
| `openeo_ai/intelligence/repository.py` | Persistence and serialization layer |
| `openeo_ai/intelligence/catalog.py` | STAC search and catalog item normalization |
| `openeo_ai/intelligence/worker.py` | Observation scheduling, claiming, execution, baselines, signals, deliveries |
| `openeo_ai/intelligence/routes.py` | App-facing authenticated Earth Intelligence API |
| `openeo_ai/intelligence/api_v1.py` | Public `/api/v1` Earth Intelligence controls |
| `openeo_app/psql/alembic/versions/phase17_earth_intelligence.py` | Database migration |
| `openeo_ai/tests/test_earth_intelligence_platform.py` | Focused platform tests |

Current implementation status:

- Durable tables/models exist for assets, watches, catalog items, observations, observation events, evidence packages, baselines, signals, and deliveries.
- STAC discovery can materialize catalog items and idempotent observations.
- Worker ticks can enqueue due watches, claim observations, execute/resume OpenEO work, archive evidence, update baselines, create signals, and queue deliveries.
- Live Monitoring and Satellite/Acquisition UI are backed by persistent APIs.
- Deliveries dispatch via webhook/Slack (HTTP) and email (SMTP, env-gated `JONA_SMTP_*`) with retry/backoff, an SSRF guard on user-controlled targets (`JONA_ALLOW_PRIVATE_WEBHOOKS` opt-out for local testing), and terminal `sent`/`failed` statuses.
- Plan-router: the planner phase is retrieval-augmented — successfully executed multi-step plans persist as `PlanTrajectory` rows and seed the planner for similar future queries (user-scoped, injection-safe, fail-open; `sdk/plan_trajectories.py`). Tier routing (simple→direct, moderate→planner+worker, complex→orchestrator+specialists) is the authoritative gate; misses log `plan_gate_miss`.
- Specialist personas: alongside data/viz/ml/qa, `SPECIALIST_CONFIGS` adds **Sentinel** (durable monitoring — spine tools + resolve/generate; deterministic route safety-net `_SENTINEL_ROUTING_KEYWORDS`) plus four research personas — **Data Scout** (availability), **Methodologist** (method/result validity), **Publisher** (reproducibility), **Time-series** (temporal decomposition). The research four reuse existing tools (overlap with data/viz/ml/qa is intentional; the planner prompt disambiguates by intent). Routing lives in `PLANNER_SYSTEM_PROMPT`. The persona A/B lift harness (`openeo_ai/evals/persona_ab.py`, 2026-07-15) measured **zero net delegation lift** for the research four — the roster is FROZEN (Auditor will NOT be added); persona judgment is being harvested into the Method Kernel instead (first: the method-aptness gate).
- Phase 0 coordination substrate: shared canonical hashing (`openeo_ai/hashing.py` — `hash_json`/`graph_hash`/`method_hash`/`plan_hash`, byte-identical to the historical worker algorithm); ordered severity pipeline with provenance (`intelligence/severity_pipeline.py`, behavior-identical stages 1–2; `signal_created` events carry `severity_provenance`); one async retry/backoff decorator (`openeo_ai/retry.py`) adopted by STAC search and the eval judge; LLM budget meter (`openeo_ai/budget.py` on phase14 `usage_events`, fail-open; since 2026-11-07 it also records query cost for the bridge's daily limit) tagging all 8 call-site joints (session/planner/worker/specialist/worker-fallback/judge/validator/direct); evals score typed process-graph artifacts (`evals/artifacts.py`), the tool-match is exact/MCP-suffix (no substring false positives), and `assert_no_regression` fails closed on a missing baseline (`--allow-missing-baseline` bootstrap escape; nightly cron tags `production`).
- Method Kernel (decision-grade route): `openeo_ai/agentic_eo/` — NL query → typed `EOIntent` → step-DAG `MethodPlan` → real Sentinel-2 L2A execution (STAC + rasterio) → scipy stats (Theil–Sen, Mann-Kendall, Welch) → 5-gate decision report. Promoted via the `openeo_ai/method_kernel` façade; a deterministic pre-model router sends decision-grade chat queries to `agentic_decision_query` before tier classification. Frozen steps-only `plan_hash` (anchor `f479b7f36d3982d5`); method thresholds live in the fail-closed `recipe_packs/*.json` registry; the decision verb accepts `recipe_id`/`window`/`thresholds`; AOI provenance (`geocoded`/`user-supplied`/`unresolved`) rides every report's lineage line; the bridge's decision-grade success path emits the Method Card as a live `report_sections` event (`type: method_card`, `live: true`) that the frontend renders and adds to the run's visualization bundle — the bundle persists only when the run carries a saved result, which decision-grade runs do not produce, so the card is in-session until that route saves artifacts. `run_query` gates every plan through `validate_plan` (schema → DAG → `method_fit`): an ill-posed method (change/anomaly with no baseline window, empty time window, change-verb question planned as a snapshot) sets `intent.method_clarification` and returns without executing, WARN findings (sub-annual trend, snapshot-with-baseline, water-question-vs-NDVI) ride the report caveats as `Method check:` lines, and `JONA_METHOD_FIT_GATE=0` flips the gate to observe mode.
- Computed trust (observe-mode): `sdk/grounding_guard.py` flags confidence/percent/score/delta claims absent from tool results (`grounding_warning` WS event + `exp-`/`implicit-ungrounded-numbers` eval scores — flags, never blocks); signal `confidence` (1−nan) is serialized as `coverage`; `_build_decision_data` threads computed confidence/coverage.
- True orbital pass prediction, case/intervention workflows, domain packs, marketplace scoring, and eval loops are not complete yet.

Background worker (on by default; only an explicit `0/false/no/off` disables):

```bash
export EARTH_INTELLIGENCE_WORKER=false      # opt out
export EARTH_INTELLIGENCE_POLL_INTERVAL=15  # poll cadence (seconds)
export JONA_SCHEDULER=false                 # opt out of firing stored batch schedules (on by default)
```

Manual worker tick endpoint:

```text
POST /api/intelligence/worker/tick
```

## Core Modules

| Module | Purpose |
| --- | --- |
| `openeo_ai/web_interface.py` | FastAPI app, WebSocket handler, router wiring, ASGI dispatcher, optional worker startup |
| `openeo_ai/web_routes.py` | Saved jobs, projects, raster rendering, SPA catch-all |
| `openeo_ai/api/auth.py` | Supabase/API-key authentication and scope checks |
| `openeo_ai/api/router.py` | Legacy/public API v1 analyze/job/batch endpoints |
| `openeo_ai/batch/` | Templates, schedules, batch items, learned patterns, learned trajectories |
| `openeo_ai/sdk/client.py` | OpenEO AI client, tool definitions, system prompt path |
| `openeo_ai/sdk/claude_sdk_bridge.py` | Claude Agent SDK bridge and WebSocket streaming |
| `openeo_ai/sdk/viz_extractor.py` | Converts tool results into map/chart/table visualizations |
| `openeo_ai/tools/` | Tool implementations for OpenEO, spectral indices, temporal analysis, exports, QA, GeoAI |
| `openeo_app/app.py` | OpenEO FastAPI backend |
| `openeo_app/execution/executor.py` | Process graph executor and NumPy/xarray compatibility shims |
| `openeo_app/processes/load_collection.py` | STAC data loading via odc-stac |

## Data Flow

Chat analysis:

```text
User prompt
  -> WebSocket /ws
  -> Claude SDK bridge/client
  -> tool calls and OpenEO process graph generation
  -> /openeo/* job execution
  -> STAC data loading and raster/vector processing
  -> saved result archive
  -> visualization extraction
  -> WebSocket events back to React
```

Saved result reload ("Load on Map"):

```text
Saved job select
  -> WebSocket load_saved_job
  -> map emitted from the saved GeoTIFF
  -> visualizations.json bundle replayed (charts, tables, process graph)
  -> report_sections event
  -> Charts/Table/Report/Process tabs repopulate
```

Persistent monitoring:

```text
Asset/watch creation
  -> STAC catalog discovery
  -> catalog item persistence
  -> idempotent observation creation
  -> worker claim/execution
  -> evidence package
  -> baseline update
  -> signal creation
  -> delivery queue
```

## Commands

Production (systemd behind Cloudflare Access) is described in `docs/deployment.md`. The commands below are for a development machine.

Backend watchdog (optional, macOS only; you write the plist yourself, see the README's Option C):

With it, the backend runs under a launchd LaunchAgent (`com.jonaai.backend`) with
`KeepAlive` — macOS respawns it if it dies and starts it at login. The agent
executes `scripts/run-backend.sh`; stdout/stderr go to `/tmp/jonaai-backend.log`,
and `faulthandler` writes native-crash tracebacks to
`/tmp/jonaai-backend-fault.log` (live all-threads dump: `kill -USR1 <pid>`). Crash telemetry
turns on when the app is imported (`openeo_ai/crash_telemetry.py`), so it runs under uvicorn too;
on the server it writes `/var/lib/jonaai/jonaai-backend-fault.log`. `JONA_FAULT_LOG` overrides it.

```bash
# start (idempotent)
launchctl bootout gui/$(id -u)/com.jonaai.backend 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jonaai.backend.plist

# stop
launchctl bootout gui/$(id -u)/com.jonaai.backend
```

Warning: `bash start-local.sh` fights the watchdog for port 8080 (the script
kills the listener at startup, launchd respawns it seconds later, killing the
script's backend). Stop the agent with `bootout` before using the script.

Start both services (only after stopping the watchdog):

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
export PYTHONPATH="$PWD:$PWD/openeo-fastapi${PYTHONPATH:+:$PYTHONPATH}"
OPENEO_DEV_MODE=true .venv/bin/python -m pytest openeo_ai/tests openeo_ai/batch/tests -q
```

Without `openeo-fastapi` on `PYTHONPATH`, collection stops at `test_job_status_races.py` and nothing runs. CI sets the same path. A checkout with no `.env` (a git worktree, say) also needs `OPENEO_DEV_MODE=true`, or importing the app stops at the auth settings check.

Focused Earth Intelligence tests:

```bash
OPENEO_DEV_MODE=true .venv/bin/python -m pytest openeo_ai/tests/test_earth_intelligence_platform.py -q
```

Live agent smoke test (needs the backend up; makes real Claude calls + pulls live
data). Asserts tool grounding, specialist routing, and output content — not just
that a tool was called. Auth = HS256 dev JWT signed with `SUPABASE_JWT_SECRET`,
`aud="authenticated"`. Its `@dev.local` email fails `JONA_ALLOWED_EMAILS`, so run it
against a backend without that setting. Full writeup: `docs/jona-terminal-e2e-test.md`.

```bash
.venv/bin/python scripts/jona_smoke.py                          # full assertion suite
.venv/bin/python scripts/jona_chat.py "your prompt" "follow-up"  # ad-hoc single/multi-turn chat
```

Frontend tests and build:

```bash
cd openeo_ai/frontend
npm run test:run
npm run build
```

Recent verification baseline:

- Backend suite (`openeo_ai/tests` and `openeo_ai/batch/tests`): **2256 passed, 2 skipped** (2026-11-11). No expected failures remain.
- **Job cancellation is status-only.** It fixes the *status*, so a timed-out or cancelled job now reports that honestly and at once. It is **not** a hard cancellation — the computation is not killed, and the job keeps its slot until the work finishes on its own. With `OPENEO_MAX_CONCURRENT_JOBS` at 1 that means a following sync request still queues behind it.
- A known open finding can be pinned with a strict `xfail`. It fails the run once the finding is fixed, so remove its marker at that point.
- Frontend suite: 254 passed; `tsc` clean; frontend build clean (Vite large-chunk warnings only).

## Authentication

The active auth model is Supabase bearer tokens plus scoped API keys.

Important files:

- `openeo_ai/api/auth.py`
- `openeo_ai/api/schemas.py`
- `openeo_ai/frontend/src/components/auth/useAuth.ts`
- `openeo_ai/frontend/src/hooks/usePlatformData.ts` (authenticated API calls)

Notes:

- API keys support exact scopes, `admin`, and `*`.
- Every API key expires: 90 days unless the request asks for fewer or more, up to 365. An API key can revoke only itself; revoking a user's other keys takes a signed-in session.
- Operator-only routes, such as `POST /api/intelligence/deadman/check`, require the `admin` scope. A signed-in user gets it only through Supabase `app_metadata.scopes`, which users can't edit themselves.
- Supabase JWT verification must be allowed to run before declaring a bearer token invalid.
- Avoid new direct `localStorage.getItem("jonaai-token")` usage in frontend code. Use the auth context/hook path.
- Saved results must carry `user_id` so RLS and project ownership stay correct.
- Checks on result files, saved results and evidence artifacts live in `openeo_ai/storage/result_access.py`. Use them rather than writing new path checks.
- `verify_supabase_token` also applies `JONA_ALLOWED_EMAILS`, so a token for an account outside the list is invalid everywhere.
- `openeo_ai/access_guard.py` wraps the ASGI app and validates the Cloudflare Access token on proxied requests only; direct loopback calls (the agent's tools to `/openeo`) skip it.

## Database

Postgres migrations live under:

```text
openeo_app/psql/alembic/versions/
```

Earth Intelligence tables are in:

```text
phase17_earth_intelligence.py
```

New durable platform data should use SQLAlchemy/repository patterns, not ad hoc in-memory stores.

`openeo_ai/_pg_compat.py` still supports legacy SQLite-shaped code against Postgres. It also falls back to real SQLite for explicit local/test database paths when Postgres env vars are not configured. Do not use `_pg_compat` for new Earth Intelligence tables unless there is a strong compatibility reason.

## Limits

Set these to fit the host. Values that don't parse keep the default rather than removing the limit.

| Setting | Default | What it bounds |
| --- | --- | --- |
| `OPENEO_MAX_LOAD_ELEMENTS` | 250M | Cube size. Measured at about 20 MB per million elements, so set it from free memory. Larger areas load up to 4 times coarser with a note, then are refused |
| `MAX_STAC_ITEMS` | 5 | Acquisition dates per load, spread across the period. A date's tiles count once |
| `OPENEO_MAX_CONCURRENT_JOBS` | 4 | Jobs running at once, synchronous `POST /result` included; others wait. A sync job waits up to 300 s for a slot (then 503) and times out 300 s after it starts. A job that times out or is cancelled says so at once but keeps its slot until its computation stops, since a thread can't be killed |
| `JONA_MAX_QUERY_USD` | 10 | Spend across all of one query's clients |
| `JONA_USER_DAILY_USD` | 50 | One user's spend per UTC day |
| `JONA_SDK_SESSION_TTL_SECONDS`, `JONA_SDK_SESSION_MAX_COUNT` | 1800, 100 | Idle agent sessions, about 250 MB each. Ended ones resume from history |
| `JONA_REQUIRE_TOOL_USER` | off | Refuses tool calls with no signed-in user. Turn on in any deployment |
| `JONA_MONTHLY_USD` | off | All users' spend per UTC month |
| `JONA_ALLOWED_EMAILS` | anyone | Accounts this deployment accepts |
| `JONA_ACCESS_MODE`, `JONA_ACCESS_TEAM_DOMAIN`, `JONA_ACCESS_AUD` | off | Validates the Cloudflare Access token: `observe` logs, `enforce` refuses |
| `JONA_RESULT_COPIES` | off | Offsite copies of saved results: `r2` with the four `JONA_R2_*` settings, or `directory` with `JONA_RESULT_COPIES_DIR`. A missing setting leaves it off |
| `JONA_R2_HARD_LIMIT_GB`, `JONA_R2_SOFT_LIMIT_GB`, `JONA_R2_USER_LIMIT_GB` | 3, 2, 1 | JonaAI's share of the R2 free allowance, which belongs to the Cloudflare account and may be shared with other projects on it. The hard stop counts only JonaAI's own copies: it can't see the account total, and going over the allowance is billed rather than refused |

## State machines

Status lifecycles are declared once, as data, and writers obey them. Don't add a status string or a move anywhere else.

- **Durable records:** `openeo_ai/lifecycle.py` declares observations, signals, deliveries, watches, batch items, batches, schedules and offsite result copies.
  - Writers call `LIFECYCLE.check(current, target)`. Racing SQL updates use `WHERE status IN (LIFECYCLE.sources(target))`.
  - `USER_SETTABLE` limits what an API caller may request, and `USER_SOURCES` (through `check_user_move`) where from: an observation re-queues only from failed or cancelled, a delivery retries only from failed. The worker's own retries pass `by_user=False`. An illegal move raises `IllegalTransition`, which the app answers with 409 naming the allowed moves, and the agent's monitor tools return it as an `illegal_transition` error.
  - OpenEO job statuses (`finished`, `error`, `canceled`) go through `openeo_outcome()`. Don't compare their spellings yourself.
  - The database enforces the same states (migration `phase19_status_lifecycles`). When a lifecycle gains a state, add a migration too. `test_migration_constraints_match_the_lifecycles` fails until you do.
  - OpenEO jobs have their own `JOB_MOVES` in `openeo_app/registers/jobs.py`. Write their status only through `_move_job_status`, never `modify()`.
  - Deliveries are claimed with `claimed_at` and retried after `next_attempt_at` (`delivery_retry_delay`).
- **Chat runs:** a `_QueryRun` in `web_interface.py` ends with exactly one `done`. It drops repeats and sends one itself if its task ends without one, except during shutdown, so a restart is still reported. After `done` it forwards only `trace` (sent once the bridge returns) and drops the rest with a log line, so output from a stopped agent winding down can't land in the next turn. Events that belong to a run carry `run_id` and `seq`. The client ends a turn on `done`, not on a run's `error`. Stop goes through `_stop_run`: the turn ends at once, and the agent winds down within `_STOP_DRAIN_SECONDS`. A question carries a `question_id`, sent again with its `clarification_closed` and echoed with the answer; the bridge ignores an answer naming a question that is no longer open. Replays skip closed questions. A message that arrives while a run is still going, or winding down after Stop, waits in `pending_messages`, and the user gets a `thinking` step (`_QUEUED_STEP_ID`) saying why, completed when it starts. Tool events carry `tool_use_id`.
- **Agent sessions:** only the bridge's reaper ends idle sessions, and it skips busy ones. A failed `connect()` leaves just the turn notes, and only a session's own client becomes its resume point. The owner is recorded when its first query starts.
- **Chat events (frontend):** `useWebSocket`'s switch handles every type the server sends: `grounding_warning` and `citations` show under the turn's answer, the contract and delegation events are marked as telemetry, and anything else is logged with `console.debug`. Add a case when the server gains an event type.
- **Chat connection (frontend):** `useWebSocket` exposes `connectionState` (`connecting`, `authenticating`, `open`, `reconnecting`, `signed_out`, `access_expired`). Only the two sign-in states stop retrying. `ChatPanel`'s banner is derived from it by `connectionBanner()`.

## Result files

Job results and the agent's tool outputs go under `result_root()` in `openeo_app/core/paths.py`. It reads `RESULT_STORAGE_PATH` and defaults to `~/.openeo_results`. Ask it for the folder; don't name a path. Never write results to `/tmp`: it is wiped on reboot, and production runs with `PrivateTmp`, which wipes it on every restart. Files there expire after a day. Saving a result copies it to `~/.openeo_jobs/results/`, where it stays. Files under the old `/tmp/openeo_results` stay readable.

Saved results can also be copied offsite (`openeo_ai/storage/result_copies.py`; setup and limits in `docs/deployment.md`). The local archive stays the primary copy. Opening a lost result (loading it, or its detail) restores it from its copy through `restore_saved_result`, off the event loop and after the access check; `get_job` and `get_job_detail` never download. The saved-results list flags `available` and `restorable`. A restore that finds the object gone from the bucket clears the claim, and a successful one records the restored file's size. The orphan sweep refuses to delete every copy, or more than its mass-delete limit, at once: that is what an empty or wrong database looks like. R2 copies are a second copy, not a backup: the app's token can delete them. Copies go through the `rclone` binary with credentials passed as `RCLONE_CONFIG_*` variables, never written to a file. Off is a normal state: every hook does nothing. Tests use `MemoryStore` or rclone's local backend, never R2.

## Graph safety

A process graph is untrusted input. `_UNSAFE_PROCESSES` in `openeo_app/execution/executor.py` removes processes that fetch URLs or run code. Add to it, and don't register new processes that take a URL.

The bridge's `can_use_tool` checks every tool in `_JOB_START_TOOLS` (`sdk/claude_sdk_bridge.py`) before any backend work starts:

- **Structure** (layer 3c, `_validate_graph_structure` in `sdk/permissions.py`): an empty graph, a node that isn't an object, a missing `process_id`, a reference to an absent node, or a cycle is refused, with the node named.
  - Unknown processes and missing parameters are deliberately not enforced. They depend on a process registry that is empty when `openeo_processes_dask` fails to import, and that must not block every job. The backend decides which processes exist.
- **Area** (layer 4, `_validate_extent`): the limit applies to every extent shape (`load_collection` extents, a top-level `spatial_extent`, an `aoi` bbox).
  - A box that crosses the dateline is measured across it.
  - Above 10° the user must confirm (`confirm_large_extent`); above 50° the request is refused.
  - A missing, null, non-numeric, infinite or NaN bound is refused. An extent in a CRS that can't be resolved is skipped, not refused.
  - Add a tool that starts backend work to `_JOB_START_TOOLS`, not to `SAFE_MODIFY_TOOLS`, which is a permission category.

The SCF compiler (`openeo_ai/validation/scf/lowering.py`) refuses rather than guesses:
- an index it can't build (`UnsupportedIndex`, for example NDRE, which once compiled silently as NDVI);
- a second spectral index after the first (`UnsupportedIndexChain`);
- a duplicate node id (`NodeIdCollision`).

Band names and aliases are matched as whole identifiers, never substrings: `red` is not part of `rededge`.

Input files the agent names go through `validate_input_file` (`openeo_ai/earth_agent/security.py`), which checks symlinks on the path as given, before resolving it.

## GeoAI models

`geoai_segment`, `geoai_detect_change` and `geoai_estimate_canopy_height` need a model file and its framework (onnxruntime or torch). Without them they refuse with a `geoai_model_unavailable` error naming the model and the reason. This is intended (2026-11-10): before, they answered with random numbers. Don't add a fallback. The random `StubModel` is for tests only, through the `allow_stub=True` constructor argument, never a setting. `GEOAI_MODELS_PATH` is resolved from the working directory, which is `/var/lib/jonaai` in production.

## Critical Gotchas

### ASGI Entry Point

Always start the server with:

```text
openeo_ai.web_interface:asgi_app
```

Starting `openeo_ai.web_interface:app` makes OpenEO endpoints return the frontend/AI app response shape instead of the OpenEO API.

### .env Format

`.env` uses shell-compatible `export VAR=value` lines. `start-local.sh` sources it with `set -a`. Keep new variables compatible with shell sourcing.

### OpenEO Dimension Names

STAC/xarray data often uses `time`, `latitude`, `longitude`; OpenEO process graphs use `t`, `y`, `x`. Keep `DIMENSION_ALIASES` behavior intact.

### SCL Band Scaling

Scene Classification Layer values are integer classes. Do not apply reflectance scaling to SCL/classification bands.

### Dask Assignment

Avoid `.loc[...] = ...` mutation on Dask-backed xarray data. Use `xr.where()` or functional transforms.

### NumPy/xarray Compatibility

`openeo_app/execution/executor.py` contains compatibility shims for NumPy 2 and openeo-processes-dask behavior. Be careful before removing them.

### Pydantic RootModel

Do not use `str(RootModel)` when you need the payload. Access `.root`.

### Colormap Sync

Colormap/index behavior may need updates in multiple places:

- Backend render: `openeo_ai/visualization/maps.py`
- Frontend map: `openeo_ai/frontend/src/components/visualizations/MapViewer.tsx`
- Frontend legend: `ColorLegend.tsx`
- Auto-detection: `openeo_ai/sdk/viz_raster.py` (re-exported via `viz_extractor.py`)

### Tests and Lazy Components

Some frontend visualization components are lazy-loaded and mocked in tests. Use async queries like `findByTestId` when asserting lazy-rendered viewers.

### Saved-Result Visualization Bundle

Loading a saved result repopulates the Charts, Data Table, Report, and Process Graph tabs, not just the map. On turn completion the frontend (`useJonaChat` `handleDone`) PUTs that run's non-map visualizations plus report sections to `PUT /saved-jobs/{save_id}/visualizations`, stored as `visualizations.json` beside the GeoTIFF. `load_saved_job` emits the map from the saved GeoTIFF, then replays the bundle as `visualization` events and one `report_sections` WebSocket event. Maps are excluded from the bundle on purpose: their `/render-raster?path=...` URLs point at working files that expire after a day, so the map always reloads from the persistent GeoTIFF. Saves without a `visualizations.json` fall back to map-only. Storage helpers (`read/write/save/get_visualizations`) live in `openeo_ai/storage/job_archive.py`.

## Documentation

Root developer docs:

- `README.md`: project overview, quick start, platform model, API/test guide.
- `ARCHITECTURE.md`: broader architecture notes.
- `CLAUDE.md` / `AGENTS.md`: agent-specific working guides. Keep them identical apart from their titles.
- `docs/deployment.md`: a reference production deployment (systemd, nginx, Cloudflare Tunnel and Access), its settings, and how to deploy.
- `CONTRIBUTING.md`: how to set up, test and extend the framework.
- `docs/supabase-setup.md`: bringing your own Supabase project.

Keep these docs in sync when adding platform-level features, routes, migrations, or worker behavior.
