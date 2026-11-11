# Contributing to JonaAI

Thanks for helping. JonaAI is an agent framework for Earth Observation: a plain-language question becomes a reproducible openEO workflow over public satellite data, tested statistically and explained. Contributions of every size are welcome, from a typo fix to a new specialist agent.

The principle that guides review: **code owns the numbers, AI owns the explanation.** A tool computes every number the agent reports. When a tool can't do its job correctly, it refuses and says why; it never guesses.

## Before you start

- Read `ARCHITECTURE.md` for the big picture, and `CLAUDE.md` for the working rules. `AGENTS.md` is the same guide under another name, for other coding agents. The rules include the gotchas that bite: dimension names, SCL scaling, Dask assignment and the ASGI entry point.
- The active app is `openeo_ai/` (agents, tools, API, frontend in `openeo_ai/frontend/`) and `openeo_app/` (the OpenEO backend).
- For anything bigger than a small fix, open an issue first and describe the change, so we can agree on the shape before you write it.

## Set up

Python 3.14 and Node 20 or newer, the versions CI uses.

```bash
python3.14 -m venv .venv
.venv/bin/pip install --no-deps --ignore-requires-python -r requirements-ci.txt   # the exact set CI tests
cp .env.example .env          # fill in only what you need; the tests run without real keys
                              # for sign-in and Postgres, set up your own Supabase: docs/supabase-setup.md
cd openeo_ai/frontend && npm ci && cd -
```

Run the app locally with `bash start-local.sh`. The backend serves on port 8080 and the frontend on port 3000. A live agent needs an `ANTHROPIC_API_KEY`. Data comes from public STAC catalogs and needs no account.

## Run the tests

The backend suite, as CI runs it:

```bash
OPENEO_DEV_MODE=true PYTHONPATH="$PWD:$PWD/openeo-fastapi" \
  .venv/bin/python -m pytest openeo_ai/tests openeo_ai/batch/tests -q
```

The frontend:

```bash
cd openeo_ai/frontend
npm run test:run
npx tsc --noEmit
npm run build
```

Every pull request must keep both suites green. A bug fix comes with a test that fails without the fix. A known open problem can be pinned with a strict `xfail` test. When a change fixes it, the test passes and the run fails, so remove the marker in the same pull request.

## Ways to extend JonaAI

### Add a tool

Tools live in `openeo_ai/tools/`, one module per family.

1. Define the tool's JSON schema in the module's `*_TOOL_DEFINITIONS` list, with a description written for the model: what it does, when to use it, and what it refuses.
2. Implement the handler and return it from the module's `create_*_tools(config)` factory.
3. Register the module in `openeo_ai/sdk/client.py`, in the definitions list and the factory list. Wire it into `openeo_ai/sdk/inprocess_tools.py` and `openeo_ai/sdk/mcp_stdio_server.py`.
4. Add the tool name to the right specialist's tool set in `client.py` (`DATA_AGENT_TOOL_NAMES`, `VIZ_AGENT_TOOL_NAMES`, and so on).
5. If the tool starts an OpenEO job, add it to `_JOB_START_TOOLS` in `openeo_ai/sdk/claude_sdk_bridge.py`. That puts it behind the graph-structure and area-limit gates.
6. If it produces something to show, add a builder in `openeo_ai/sdk/viz_builders.py`.

File paths from the model are untrusted. Use the checks in `openeo_ai/tools/path_security.py` and `openeo_ai/storage/result_access.py`, not your own.

### Add a recipe pack

Decision-grade thresholds live in versioned JSON files in `openeo_ai/agentic_eo/recipe_packs/`, one per variable (`ndvi.json`, `ndwi.json` and others). Each threshold names its value, unit, comparator, where it applies, and its source. The registry fails closed: a malformed pack is refused, not half-loaded. A pack for your domain (agriculture, forests, water, cryosphere, disaster response) is one of the most useful contributions. Cite the source of every threshold.

### Add a specialist

Specialist personas are declared in `SPECIALIST_CONFIGS` in `openeo_ai/sdk/client.py`, and the planner routes to them through `PLANNER_SYSTEM_PROMPT` in `openeo_ai/sdk/system_prompts.py`. A new specialist should earn its place: `openeo_ai/evals/persona_ab.py` measures whether it lifts results over the existing roster. Include that measurement in the pull request.

### Add a data source

Collections are loaded through STAC (`openeo_app/processes/load_collection.py`, using odc-stac). Add a collection's metadata where the discovery and comparison tools describe collections. Keep band names and scaling explicit: classification bands such as SCL must never be scaled as reflectance.

### Add a delivery channel

Monitoring deliveries (webhook, Slack, email) are dispatched in `openeo_ai/intelligence/worker.py`. A new channel must go through the SSRF guard for user-supplied targets, and must reach the terminal `sent` or `failed` statuses declared in `openeo_ai/lifecycle.py`.

## Rules every change follows

- **Statuses go through the declared state machines** in `openeo_ai/lifecycle.py` (OpenEO jobs: `JOB_MOVES` in `openeo_app/registers/jobs.py`). Don't add a status string anywhere else. A new state also needs a migration; a test fails until it exists.
- **Process graphs are untrusted input.** Don't register a process that fetches a URL or runs code. `_UNSAFE_PROCESSES` in `openeo_app/execution/executor.py` lists the ones removed.
- **Results never go to `/tmp`.** Ask `result_root()` in `openeo_app/core/paths.py` for the folder.
- **Database changes** are Alembic migrations in `openeo_app/psql/alembic/versions/`, using the SQLAlchemy and repository patterns.
- **Never commit a secret.** Settings go in `.env`, which is ignored. Add new settings to `.env.example` with a placeholder value and a comment.
- **Keep the docs in step.** A platform-level change updates `README.md`, `ARCHITECTURE.md` and `CLAUDE.md`. Keep `AGENTS.md` identical to `CLAUDE.md` apart from its title.
- Match the style of the code around you. Keep pull requests focused: one change, with its tests and docs.

## Pull requests

1. Fork the repository and branch from `main`.
2. Make the change, with tests and docs.
3. Run both suites.
4. Open the pull request. Say what changed, why, and how you verified it. Link the issue.

By contributing, you agree that your contribution is licensed under the Apache License 2.0, the licence of this project (see `LICENSE`).

## Reporting a security issue

See [`SECURITY.md`](SECURITY.md). In short: don't open a public issue for a vulnerability. Report it privately through GitHub's "Report a vulnerability" button on the repository's Security tab. Include the steps to reproduce it and the impact you expect. We'll acknowledge it and keep you informed while we fix it.

## Conduct

This project follows the [code of conduct](CODE_OF_CONDUCT.md). Be kind and assume good faith. Critique code, not people. Maintainers may remove comments or contributions that are abusive or harassing.
