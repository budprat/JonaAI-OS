# JonaAI

**AI-powered Earth Observation and persistent Earth Intelligence.**

JonaAI turns a plain-language question — *"Show NDVI change for this farm over the last 90 days"* — into real satellite analysis: it plans the work, generates and runs an [OpenEO](https://openeo.org/) process graph over live STAC imagery, interprets the result, renders maps/charts/reports, and can keep **monitoring** the area on a schedule and alert you when something changes. No GIS, band math, or OpenEO knowledge required from the user.

Last updated: 2026-11-11

---

## Table of Contents

- [Overview](#overview)
- [What's New](#whats-new)
- [Architecture](#architecture)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Repository Layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Setup & Configuration](#setup--configuration)
- [Running End-to-End](#running-end-to-end)
- [Usage](#usage)
- [Persistent Earth Intelligence](#persistent-earth-intelligence)
- [API Reference](#api-reference)
- [Testing](#testing)
- [Documentation](#documentation)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Project Status & Roadmap](#project-status--roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Most Earth Observation tooling stops at the pixel: you get imagery or a raw index and are left to interpret it. JonaAI closes the **pixel-to-decision** gap with a conversational agent that owns the whole workflow:

1. **Understand** the request (intent, area, time, method) and clarify when it is genuinely ambiguous.
2. **Plan & route** it — simple lookups answer directly; analyses go through a planner + worker; complex work fans out to specialists.
3. **Execute** via structured tool calls that build validated OpenEO process graphs (not free-form code), run them over STAC data, and apply cloud masking and quality gates.
4. **Interpret** the output against peer-reviewed domain thresholds, grade quality, and render map / chart / table / report visualizations. Decision-grade questions ("is there stress / drought / burn — act or not?") route to a deterministic **Method Kernel** that returns a quantified headline, change-vs-baseline with a significance test, an honest confidence level, a recommended action, and a reproducible **Method Card**.
5. **Persist** results with ownership and provenance, and optionally keep **watching** the area — re-running on a cadence and emitting signals + deliveries when a threshold or anomaly is crossed.

A guiding principle runs through the system — **deterministic kernel, agentic joints**: anything that must be reproducible, auditable, or safe (watch scheduling, delivery gating, method thresholds, numeric claims) is owned by code or computed by tools; the model owns language and explanation, never the numbers.

The active product lives under `openeo_ai/` (AI + web app), `openeo_app/` (OpenEO backend), and `openeo_ai/frontend/` (React app).

---

## What's New

**Hardening, 2026-11-10:**
- **Refuses instead of guessing:**
  - GeoAI tools refuse without a model file rather than return random numbers.
  - Index requests that can't be built correctly are refused rather than computed wrongly: NDRE read as NDVI, chained indices, duplicate index names.
- **Gates before a job starts:**
  - Every job-starting tool checks the graph's structure: cycles, dangling references, missing process ids.
  - The area limit covers every way an extent can arrive, including across the dateline.
  - An extent with unreadable bounds is refused.
- **Honest job status:** a timed-out or cancelled OpenEO job says so at once. It keeps its slot until the work really stops.
- **Fair use of capacity:**
  - Synchronous jobs share the job slots.
  - Batch and template routes respect the tier's concurrency limit.
  - Callers can't re-queue running work.
- **Tighter access:**
  - Signals can't name another user's records.
  - Input-file symlinks can't bypass the file-type allowlist.
  - GeoAI inputs and outputs stay inside the allowed folders.
- **Steadier chat:**
  - Two tabs can't start two runs.
  - Stop works during a question, and a second question is refused while one is open.
  - A failed "Load on Map" doesn't end the turn.

**Since 2026-11-09:**
- Results never go to `/tmp`.
- Optional offsite copies of saved results (R2), restored automatically when a local file is lost.
- Nightly backups with an optional recovery bucket.
- Crash telemetry.
- Declared status lifecycles for every durable record and for chat runs (2026-11-08).

---

## Architecture

### Two services, one ASGI entry point

```text
┌────────────────────────────┐        ┌──────────────────────────────────────────────┐
│  Frontend (Vite dev :3000) │  HTTP  │  Backend (uvicorn :8080)                       │
│  React + TS + Leaflet      │◄──────►│  openeo_ai.web_interface:asgi_app  (dispatcher)│
│  WebSocket /ws + REST /api  │   WS   │   ├─ /openeo/*, /health*  → openeo_app (OpenEO)│
└────────────────────────────┘        │   └─ everything else       → openeo_ai (AI app)│
                                       └──────────────────────────────────────────────┘
```

> **Critical:** start the backend as `openeo_ai.web_interface:asgi_app`, **not** `:app`. `asgi_app` is the combined dispatcher; `app` is only the AI FastAPI instance and will make `/openeo/*` return the AI/frontend response shape.

### Chat request flow

```text
User prompt
  → WebSocket /ws
  → Claude Agent SDK bridge (openeo_ai/sdk/claude_sdk_bridge.py)
  → tier routing → tool calls → OpenEO process-graph generation & validation
  → /openeo/* job execution → STAC data load (odc-stac + Dask) → raster/vector processing
  → saved-result archive + evidence package
  → visualization extraction (map / chart / table / report / process graph)
  → streamed events back to React
```

### Agent tiers

The bridge classifies each query and routes it. Every tier runs on `JONA_MODEL` (default **Claude Opus 5.5** at `max` effort); `JONA_COMPLEX_MODEL` overrides the complex tier:

| Tier | Trigger | Path |
|------|---------|------|
| **simple** | lookups, definitions, status | direct model, no tools/planning |
| **moderate** | single-index / basic analysis | planner → single worker |
| **complex** | change detection, fusion, **monitoring** | orchestrator → specialist delegation |

Nine specialists exist: `data`, `viz`, `ml`, `qa`, `sentinel` (durable monitoring), plus four research personas `data_scout`, `methodologist`, `publisher`, `timeseries`. The worker/specialists are fenced to the `openeo_*` MCP tools (generic `Bash`/`Write`/`Read`/`Grep`/… are disallowed) so answers are always tool-grounded, never fabricated.

Ahead of tier classification, a **deterministic pre-model route** sends decision-grade questions straight to the Method Kernel (`agentic_decision_query`) — that routing is code, not a model whim, so it always runs.

### Persistent Earth Intelligence spine

A durable data model lets monitoring outlive a single chat session:

```text
Asset → Watch → Catalog Item → Observation → Evidence Package → Baseline → Signal → Delivery
```

See [Persistent Earth Intelligence](#persistent-earth-intelligence).

---

## Features

- **Natural-language geospatial analysis** — intent decomposition, smart defaults, clarifying questions.
- **Tool-mediated OpenEO** — ~92 domain tools generate **validated** process graphs (reliability over raw code generation); ~112 OpenEO processes available in the backend.
- **Data sources** (STAC): `sentinel-2-l2a`, `sentinel-2-l1c`, `sentinel-2-c1-l2a`, `landsat-c2-l1`, `cop-dem-glo-30`, `cop-dem-glo-90` and `naip` come from AWS Earth Search. `landsat-c2-l2` and `sentinel-1-grd` come from Microsoft Planetary Computer, whose items are signed so they load free; `sentinel-1-grd` is served as the calibrated, terrain-corrected `sentinel-1-rtc`. A load uses at most `MAX_STAC_ITEMS` acquisition dates (default 5), spread across the requested period, and the result says how many it used.
- **Analysis** — 17 spectral indices, temporal trend/anomaly/phenology, multi-sensor fusion, zonal statistics, threshold classification, ML/GeoAI (land cover, change, canopy height).
- **Interpretation & quality** — peer-reviewed domain thresholds, A–F quality grades, auto cloud masking (SCL-based).
- **Decision-grade Method Kernel** (`openeo_ai/agentic_eo/`, `openeo_ai/method_kernel/`) — a deterministic pipeline (intent → typed step-DAG plan → execution → decision report) exposed as the `agentic_decision_query` verb. Decision thresholds live as versioned data (`recipe_packs/*.json`); every run emits a **Method Card** with AOI provenance (geocoded / user-supplied / unresolved), a steps-only `plan_hash`, and data lineage. The verb accepts explicit `recipe_id` / `aoi` / `window` / `thresholds` to pin the method and sidestep geocoding.
- **Computed trust (observe-mode)** — the model never originates numbers: a grounding guard flags confidence/percent/score/delta claims absent from any tool result, a system-prompt clause requires quoting `assess_result_quality`/kernel confidence, and the decision path threads tool-computed confidence + coverage.
- **Visualizations** — interactive Leaflet maps, charts, tables, before/after swipe, multi-panel, timelapse, dashboards, process-graph views.
- **Persistent monitoring** — assets, watches (with rolling temporal windows), baselines, signals, and deliveries to webhook / Slack / email.
- **Exports** — GeoTIFF, CSV, HTML reports, reproducible Jupyter notebooks + BibTeX/workflow docs.
- **Multi-tenant auth** — Supabase bearer tokens + scoped API keys with row-level ownership. Saved files, jobs, batches, evidence and chat sessions are checked per user; a deployment can restrict accounts (`JONA_ALLOWED_EMAILS`) and validate the Cloudflare Access token in front of it (`JONA_ACCESS_MODE`). Password reset runs through Supabase's recovery email.
- **Fails closed, not open** — tools refuse rather than fabricate: GeoAI without a model, index requests that can't be built correctly, graphs with a cycle or a dangling reference, extents that are too large or can't be read. Each refusal names its reason.
- **Bounded cost and load** — per-query, per-user daily and monthly spend limits, a cap on cube size and concurrent jobs, and idle agent sessions that end and later resume from history. See "Limits" in `CLAUDE.md`.
- **Runs that survive dropped connections** — a query keeps running when the WebSocket drops; the client reconnects and receives the missed events, and after a restart it is told the run was cut off.
- **Observability** — Langfuse tracing + OpenTelemetry across the agent loop.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| AI orchestration | Claude Agent SDK (in-process MCP tools), Claude Opus 5.5 by default (`JONA_MODEL`) |
| Backend | Python 3.14, FastAPI, uvicorn, combined ASGI dispatcher |
| EO processing | OpenEO, `openeo-processes-dask`, `odc-stac`, xarray, Dask, rasterio/GDAL |
| Data | STAC (AWS Earth Search, Microsoft Planetary Computer), cloud-optimized GeoTIFF |
| Persistence | PostgreSQL (Supabase) via SQLAlchemy + Alembic; SQLite fallback for local/test paths |
| Frontend | React 18, TypeScript, Vite, Leaflet, Recharts, WebSocket streaming |
| Auth | Supabase JWT (ES256/JWKS or HS256), scoped API keys |
| Tooling | `uv` (preferred) / `pip`, `pytest`, Langfuse |

---

## Repository Layout

```text
JonaAI/
├── openeo_ai/                     # AI assistant, FastAPI app, SDK bridge, tools, storage, frontend
│   ├── frontend/                  # React + Vite + TypeScript app (active UI)
│   ├── sdk/                       # Claude Agent SDK bridge, client, tool defs, viz extraction, grounding guard
│   ├── agentic_eo/                # Decision-grade Method Kernel: decompose→plan→execute→report, recipe_packs/
│   ├── method_kernel/             # Kernel façade, fail-closed plan validation, Method Card render
│   ├── tools/                     # OpenEO, spectral, temporal, fusion, export, GeoAI, monitor tools
│   ├── intelligence/              # Persistent Earth Intelligence: routes, repository, catalog, worker
│   ├── evals/                     # Golden-query eval harness, LLM judge, persona A/B lift rig
│   ├── storage/                   # SQLAlchemy models + saved-result archive
│   ├── visualization/             # Raster rendering, colormaps, legends
│   ├── api/                       # Auth, API keys, public API v1
│   ├── tests/                     # Backend test suite
│   └── web_interface.py           # FastAPI app + WebSocket handler + combined ASGI entry point
├── openeo_app/                    # OpenEO-compatible backend
│   ├── execution/                 # Process-graph executor + NumPy/Dask compatibility shims
│   ├── processes/                 # Custom OpenEO processes (load_collection, reduce_dimension_fix, …)
│   └── psql/alembic/versions/     # Database migrations (incl. phase17_earth_intelligence.py)
├── openeo-fastapi/                # Vendored/supporting OpenEO FastAPI package
├── scripts/                       # run-backend.sh, jona_chat.py & jona_smoke.py (live agent harness), backup_results.py
├── docs/                          # Deployment, observability, live smoke test, example reports
├── setup-local.sh                 # One-time local setup (venv, deps, patches, frontend)
├── start-local.sh                 # Starts backend + frontend together
├── .env.example                   # Shell-compatible environment template
├── CONTRIBUTING.md                # How to set up, test and extend
├── CLAUDE.md / AGENTS.md          # Coding-agent working guides
└── ARCHITECTURE.md                # Deeper architecture notes
```

---

## Prerequisites

- **Python 3.14**, the version CI tests.
- **Node.js 20+** and npm (for the frontend).
- **[uv](https://github.com/astral-sh/uv)** recommended for 10–100× faster installs (`setup-local.sh` falls back to pip).
- An **Anthropic API key** (`ANTHROPIC_API_KEY`).
- **PostgreSQL** — a Supabase project or local Postgres. Not strictly required for basic local/test runs (SQLite fallback), but needed for auth and the Earth Intelligence spine.
- Internet access to **AWS Earth Search** and **Microsoft Planetary Computer** STAC, plus their public storage (imagery is pulled at query time).

---

## Setup & Configuration

### 1. Clone and run one-time setup

```bash
git clone https://github.com/budprat/JonaAI-OS.git
cd JonaAI-OS
python3.14 -m venv .venv
.venv/bin/pip install --no-deps --ignore-requires-python -r requirements-ci.txt
(cd openeo_ai/frontend && npm ci)
```

`requirements-ci.txt` is the exact dependency set CI tests on every push, so this is the install path known to work. `openeo-fastapi` is not installed: it's loaded from the repository through `PYTHONPATH` (see the test command below).

`setup-local.sh` is an older helper that installs from looser version ranges, and may need adjusting on a new machine. It performs 7 steps: (1) create the `.venv`, (2) install Python deps, (3) install `openeo-fastapi` `--no-deps`, (4) apply Pydantic v1/v2 compatibility patches, (5) apply `openeo_processes_dask` import patches, (6) install frontend deps, (7) check `.env`.

### 2. Configure environment

```bash
cp .env.example .env
# then edit .env — it uses shell-compatible `export VAR=value` lines (sourced with `set -a`)
```

**Required to do anything useful:**

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access (the agent) |
| `OPENEO_DEV_MODE=true` | Local dev auth mode for the OpenEO backend |
| `OPENEO_DEV_PASSWORD`, `OPENEO_DEV_USER_ID` | Local OpenEO job-tools credentials |
| `STAC_API_URL` | STAC catalog, typically `https://earth-search.aws.element84.com/v1` |

**Auth / multi-user (Supabase):** bring your own Supabase project. [`docs/supabase-setup.md`](docs/supabase-setup.md) walks through creating it, filling these in, and locking down sign-in.

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | Supabase project |
| `SUPABASE_JWT_SECRET` | Verifies bearer tokens (HS256 symmetric path) |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Frontend Supabase config |

**Database (Postgres/Supabase):** `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRESQL_HOST`, `POSTGRESQL_PORT`, `POSTGRES_DB`, `ALEMBIC_DIR`.

**Optional integrations:**

| Variable | Purpose |
|----------|---------|
| `EARTH_INTELLIGENCE_WORKER=false`, `EARTH_INTELLIGENCE_POLL_INTERVAL` | Disable (worker is on by default) / tune the background monitoring worker |
| `JONA_SCHEDULER=false` | Stop stored batch schedules from firing (the scheduler is on by default) |
| `JONA_MODEL`, `JONA_EFFORT` | Model and effort for every agent phase (default `claude-opus-5-5` at `max`) |
| `JONA_COMPLEX_MODEL` | Optional override for complex-tier queries |
| `JONA_HONOR_PLAN_MODEL` | Honor the planner's whitelisted `model_recommendation` for worker sessions |
| `JONA_SMTP_HOST`/`_PORT`/`_USER`/`_PASSWORD`/`_FROM`/`_STARTTLS` | Email delivery of signals |
| `JONA_ALLOW_PRIVATE_WEBHOOKS` | Opt out of the SSRF guard for local webhook testing |
| `OPENEO_MAX_LOAD_ELEMENTS`, `OPENEO_MAX_CONCURRENT_JOBS`, `MAX_STAC_ITEMS` | Job size and concurrency; set them from the host's free memory |
| `JONA_MAX_QUERY_USD`, `JONA_USER_DAILY_USD`, `JONA_MONTHLY_USD` | Spend limits |
| `JONA_SDK_SESSION_TTL_SECONDS`, `JONA_SDK_SESSION_MAX_COUNT` | Idle agent sessions (about 250 MB each) |
| `JONA_REQUIRE_TOOL_USER`, `JONA_ALLOWED_EMAILS` | Refuse tool calls without a signed-in user; restrict accounts |
| `JONA_ACCESS_MODE`, `JONA_ACCESS_TEAM_DOMAIN`, `JONA_ACCESS_AUD` | Validate a Cloudflare Access token (`observe` or `enforce`) |
| `JONA_RESULT_COPIES`, `JONA_R2_*`, `JONA_RESULT_COPIES_DIR` | Optional offsite copies of saved results, restored when the local file is missing (off by default) |
| `JONA_RUN_LOG_DIR` | Where query runs are logged so they can be recovered after a restart |
| `LOG_DIR` | The OpenEO backend's rotating log files (default `~/.openeo_logs`, never `/tmp`) |
| `EARTH_AGENT_PYTHON`, `ENABLE_EARTH_AGENT_DIRECT_TOOLS`, `EARTH_AGENT_API_KEY` | Earth-Agent statistical toolkit (its tools are offered only where its checkout and dependencies exist) |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | LLM tracing/observability |
| `GEOAI_MODELS_PATH`, `RESULT_STORAGE_PATH` | GeoAI model location (without model files the GeoAI tools refuse); folder for job results and tool outputs (default `~/.openeo_results`, never `/tmp`) |
| `AWS_NO_SIGN_REQUEST`, `AWS_REQUEST_PAYER` | STAC/S3 access mode |

The limits and their defaults are described in `CLAUDE.md` under "Limits"; values for a small host are in `docs/deployment.md`.

> If Postgres env vars are unset, selected legacy local stores fall back to SQLite for tests and local dev. Local SQLite files under `data/` are ignored by git.

### 3. Apply database migrations (Postgres)

```bash
set -a; source .env; set +a
export ALEMBIC_DIR="$PWD/openeo_app/psql"
(cd openeo_app/psql && ../../.venv/bin/alembic upgrade head)
```

Migrations live in `openeo_app/psql/alembic/versions/`. They create every table with row-level security on; see [`docs/supabase-setup.md`](docs/supabase-setup.md).

---

## Running End-to-End

### Option A — scripted (both services)

```bash
bash start-local.sh
```

Starts the backend (`:8080`) and frontend (`:3000`), streams logs to `/tmp/jonaai-backend.log` and `/tmp/jonaai-frontend.log`, and cleans up child processes on `Ctrl+C`.

### Option B — manual

```bash
# Backend (combined ASGI dispatcher)
export PYTHONPATH="$PWD:$PWD/openeo-fastapi${PYTHONPATH:+:$PYTHONPATH}"
export ALEMBIC_DIR="$PWD/openeo_app/psql"
.venv/bin/python3 -m uvicorn openeo_ai.web_interface:asgi_app --host 127.0.0.1 --port 8080 --reload

# Frontend (separate terminal)
cd openeo_ai/frontend
npm run dev
```

### Option C — durable backend (macOS launchd)

On a dev machine you can run the backend under a `KeepAlive` LaunchAgent (`com.jonaai.backend`) so it restarts if it dies and starts at login. It executes `scripts/run-backend.sh`; logs go to `/tmp/jonaai-backend.log`. The plist isn't included: create `~/Library/LaunchAgents/com.jonaai.backend.plist` with `ProgramArguments` pointing at `scripts/run-backend.sh`, plus `KeepAlive` and `RunAtLoad` set to true.

```bash
# start (idempotent)
launchctl bootout   gui/$(id -u)/com.jonaai.backend 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jonaai.backend.plist
# stop
launchctl bootout   gui/$(id -u)/com.jonaai.backend
```

> Do not run `start-local.sh` while the LaunchAgent is active — both fight for port 8080.

### Production

The reference deployment runs under systemd behind Cloudflare Access, a Cloudflare Tunnel and nginx, on a single small host. Layout, settings, the deploy steps and backups: [`docs/deployment.md`](docs/deployment.md).

### Verify it's up

```bash
curl -s http://localhost:8080/health        # {"status":"healthy", ...}
```

- Frontend: <http://localhost:3000>
- AI + OpenEO backend: <http://localhost:8080>
- OpenEO API: <http://localhost:8080/openeo/1.1.0>

Then open the frontend and ask, e.g., *"Compute NDVI for Punjab wheat fields, June 2024, and show the map."*

---

## Usage

Ask for analysis in natural language; the agent plans and executes it, streaming each step. Examples:

```text
Is my farm healthy? (draw an area first)
Compute NDVI for west=75.0 south=30.5 east=75.2 north=30.7 for June 2024, show the map and mean NDVI.
Compare vegetation between June 2020 and June 2024 here; rate the change and suggest causes.
Which collections and spectral indices can you work with?
Set up weekly monitoring of NDVI for this area and alert me if vegetation health drops.
```

Results are saved with ownership and provenance. Reloading a saved result ("Load on Map") replays not just the map but its charts, tables, process graph, and report into their tabs — non-map visualizations are persisted via `PUT /saved-jobs/{save_id}/visualizations` and replayed by `load_saved_job`; the map itself always reloads from the saved GeoTIFF.

---

## Persistent Earth Intelligence

The durable spine turns one-off analysis into ongoing monitoring:

```text
Asset → Watch → Catalog Item → Observation → Evidence Package → Baseline → Signal → Delivery
```

- **Assets** — AOIs (farms, forests, watersheds, cities, infrastructure, custom geometry).
- **Watches** — recurring monitoring contracts with a cadence and process graph. On each run the worker **re-windows** the graph's temporal extent to a rolling period ending *now* (sized to the cadence, floored at the ~14-day Sentinel-2 revisit gap), so a watch analyses the latest imagery rather than the dates baked in at creation.
- **Catalog items / Observations** — STAC discovery materializes source acquisitions and idempotent execution opportunities; **observation events** record a durable execution timeline (`started`, `temporal_rewindowed`, `completed`, …).
- **Evidence packages / Baselines / Signals** — immutable outputs, rolling metric statistics, and threshold/anomaly/trend records.
- **Deliveries** — dispatched to webhook / Slack (HTTP) and email (SMTP, env-gated `JONA_SMTP_*`) with retry/backoff, terminal `sent`/`failed` statuses, and an SSRF guard on user-controlled targets.

The background worker runs by default (`EARTH_INTELLIGENCE_WORKER=false` disables it); you can also trigger a manual user-scoped tick:

```bash
curl -X POST http://localhost:8080/api/intelligence/worker/tick   # (authenticated)
```

Key files: `openeo_ai/storage/models.py`, `openeo_ai/intelligence/{repository,catalog,worker,routes,api_v1}.py`, migration `openeo_app/psql/alembic/versions/phase17_earth_intelligence.py`.

---

## API Reference

**App-facing (authenticated):**

```text
GET/POST   /api/assets
DELETE     /api/assets/{asset_id}   (also removes its watches)
GET/POST   /api/watches
POST       /api/assets/{asset_id}/catalog/discover
GET        /api/catalog-items
GET/PATCH  /api/observations/{observation_id}
POST       /api/observations/{observation_id}/execute
GET        /api/evidence
GET        /api/baselines
GET/PATCH  /api/signals/{signal_id}
GET/PATCH  /api/deliveries/{delivery_id}
POST       /api/intelligence/worker/tick
GET        /saved-jobs, GET /saved-jobs/{id}, PUT /saved-jobs/{id}/visualizations
GET        /api/v1/usage/spend
```

**Public API v1:** `/api/v1/{assets,watches,observations,evidence,signals,deliveries}`

Chat is over WebSocket `/ws` (Supabase bearer or scoped API key). A session runs one query at a time; when the token expires the socket closes with code 4401 so the client can sign in again. OpenEO API is under `/openeo/1.1.0`; process graphs are untrusted input, so processes that fetch URLs or run code are refused.

---

## Testing

```bash
# Backend (needs openeo-fastapi on PYTHONPATH, as CI sets it)
export PYTHONPATH="$PWD:$PWD/openeo-fastapi${PYTHONPATH:+:$PYTHONPATH}"
OPENEO_DEV_MODE=true .venv/bin/python -m pytest openeo_ai/tests openeo_ai/batch/tests -q

# Focused Earth Intelligence platform tests
OPENEO_DEV_MODE=true .venv/bin/python -m pytest openeo_ai/tests/test_earth_intelligence_platform.py -q

# Frontend
cd openeo_ai/frontend && npm run test:run && npm run build
```

`OPENEO_DEV_MODE=true` matters in a checkout with no `.env`, such as a git worktree: without it, importing the app stops at the auth settings check.

**Latest results** (2026-11-11):
- Backend: 2256 passed, 2 skipped.
- Frontend: 254 passed, with a clean build.

**Live agent smoke test** — drives a real chat over the WebSocket and asserts tool grounding, specialist routing, and **output content** (not just that a tool was called), plus that the backend does not crash. Requires the backend up; makes real Claude calls and pulls live data.

```bash
.venv/bin/python scripts/jona_smoke.py                          # full assertion suite
.venv/bin/python scripts/jona_chat.py "your prompt" "follow-up"  # ad-hoc single or multi-turn chat
```

Auth for the harness is an HS256 dev JWT signed with `SUPABASE_JWT_SECRET` and `aud="authenticated"`. Its `@dev.local` email is refused when `JONA_ALLOWED_EMAILS` is set, so run it against a backend without that setting. Full writeup: [`docs/jona-terminal-e2e-test.md`](docs/jona-terminal-e2e-test.md).

---

## Documentation

| Document | What it covers |
| --- | --- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Modules, data flow, lifecycles, limits, the worker, the frontend |
| [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md) | Working guide for coding agents: commands, limits, state machines, gotchas. The two are kept identical |
| [`docs/supabase-setup.md`](docs/supabase-setup.md) | Bringing your own Supabase project: settings, migrations, sign-in lockdown |
| [`docs/deployment.md`](docs/deployment.md) | A reference production deployment: layout, settings, deploying, backups, offsite copies |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Setting up, running the tests, and extending the framework |
| [`SECURITY.md`](SECURITY.md) | Reporting a vulnerability |
| [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) | How we work together |
| [`docs/observability.md`](docs/observability.md) | Tracing and telemetry |
| [`docs/jona-terminal-e2e-test.md`](docs/jona-terminal-e2e-test.md) | The live agent smoke test |

---

## Development

Guides for coding agents live in `CLAUDE.md` and `AGENTS.md`; deeper notes in `ARCHITECTURE.md`. High-value gotchas:

- **ASGI entry point** — always `openeo_ai.web_interface:asgi_app` (see the warning above).
- **Dimension names** — STAC/xarray use `time`/`latitude`/`longitude`; OpenEO graphs use `t`/`y`/`x`. Keep `DIMENSION_ALIASES` intact.
- **SCL scaling** — Scene Classification Layer values are integer classes; never apply reflectance scaling to them.
- **Dask** — avoid `.loc[...] = ...` mutation on Dask-backed xarray; use `xr.where()` / functional transforms.
- **NumPy/Dask shims** — `openeo_app/execution/executor.py` has compatibility shims; be careful before removing them.
- **Colormaps** — colormap/index behavior is mirrored in backend render (`visualization/maps.py`), frontend `MapViewer.tsx` / `ColorLegend.tsx`, and auto-detection (`sdk/viz_raster.py`).
- **`.env` format** — shell-compatible `export VAR=value`; keep new variables shell-sourceable.

Common tasks: add an Earth Intelligence endpoint (`intelligence/schemas.py` → `repository.py` → `routes.py` → `api_v1.py` → frontend hook `frontend/src/hooks/usePlatformData.ts` → `tests/test_earth_intelligence_platform.py`); add a persisted field (models → Alembic migration → repository serialization → tests → RLS/ownership check); add a map change (backend `visualization/maps.py` → frontend `MapViewer.tsx` → legend/picker → run the `MapViewer` and `VisualizationPanel` frontend tests).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| OpenEO endpoints return HTML | You started `:app`; start `:asgi_app` instead. |
| Frontend can't reach backend | Confirm backend on `:8080`, Vite on `:3000`; check `/tmp/jonaai-*.log`. |
| Background monitoring doesn't run | On by default — remove `EARTH_INTELLIGENCE_WORKER=false`, check startup logs, or `POST /api/intelligence/worker/tick`. |
| Supabase JWTs fail | Check `SUPABASE_JWT_SECRET` and frontend Supabase config; scoped API keys also work. |
| Backend SIGKILLed under load | macOS jetsam (memory pressure) — reduce concurrent heavy processes; it is not a code fault. |
| A job is refused as too large | The area or period exceeds `OPENEO_MAX_LOAD_ELEMENTS`. Areas up to 4 times over load at coarser resolution with a note; beyond that, narrow the area or period. |
| Chat stops with a spend or model error | A spend limit was reached or the API key can't be used; the message says which. `GET /api/v1/usage/spend` shows today's spend. |
| Every page returns 403 in production | Cloudflare Access token refused; see "Rolling back Access enforcement" in `docs/deployment.md`. |

---

## Project Status & Roadmap

**Implemented:** conversational analysis with tool-mediated OpenEO; multi-tier agent routing + specialists; a deterministic decision-grade **Method Kernel** (typed step-DAG plans, recipes-as-data, Method Cards, AOI provenance, fail-closed plan validation) exposed as the `agentic_decision_query` verb; computed-trust grounding (observe-mode) so the model quotes rather than originates numbers; interpretation, quality grading, and 8 visualization types; the durable Earth Intelligence spine with cadence-scheduled watches, delivery threshold gating (anti-spam), baselines, signals, and webhook/Slack/email delivery; multi-tenant auth with per-user access checks, spend and job-size limits, and runs that survive dropped connections; Langfuse observability + a golden-query eval harness (with an experimental persona A/B lift rig).

**Known gaps:**
- A per-graph element limit.
- A hard cancel for running jobs; today a cancel is reported at once, but the computation runs to its end.
- Starting the schedule loop.
- Reporting scientific fallbacks as caveats.

**Not yet complete:** true orbital pass prediction; case/intervention lifecycle; domain packs and marketplace scoring; typed-property tier routing (a heuristic classifier is the documented interim); structured-output plan contracts; foundation-model UDFs; external context enrichment (weather/news/land-use) for causal narratives. Some legacy assistant/job/session paths remain process-local.

---

## Contributing

Contributions are welcome: new tools, recipe packs for your domain, specialists, data sources and delivery channels. [`CONTRIBUTING.md`](CONTRIBUTING.md) explains how to set up, run the tests and extend each part of the framework. Report security issues privately, as described there.

## License

JonaAI is licensed under the [Apache License 2.0](LICENSE). Bundled and adapted third-party code is credited in [`NOTICE`](NOTICE).

Dependencies keep their own licences. Most are permissive (MIT, BSD, Apache-2.0, ISC). Worth knowing before you deploy:
- The map view uses `react-leaflet`, licensed under the [Hippocratic License 2.1](https://firstdonoharm.dev/), which adds human-rights conditions on use. To avoid it, the map can be ported to plain Leaflet (BSD-2-Clause).
- `psycopg2-binary` is LGPL, used as an unmodified library.
- `yprov4wfs` (GPLv3) is pulled in by `openeo-pg-parser-networkx`. JonaAI doesn't import it.
