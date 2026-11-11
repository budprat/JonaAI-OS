<!-- ABOUTME: How to drive a real JonaAI agent chat end-to-end from the terminal, -->
<!-- ABOUTME: plus the 2026-07-08 run that surfaced a worker data-fabrication defect. -->

# JonaAI Terminal End-to-End Chat Test

A way to chat with the live JonaAI agent from the terminal and watch every
event (thinking, `tool_use`, tool results, visualizations, usage/cost) while
tailing the backend log. No frontend required.

## Harness

`scripts/jona_chat.py` — a WebSocket client that:

1. Mints a dev JWT and connects to `ws://localhost:8080/ws`.
2. Authenticates via the message-based `{"type":"authenticate","token":...}` frame.
3. Sends the query as `{"type":"message","content":"..."}`.
4. Streams and pretty-prints every event until `done`, then prints a summary
   (tools called, assistant chars, wall-clock).

### Auth recipe (the part that is easy to get wrong)

The WS `authenticate` path calls `verify_supabase_token`. Its symmetric path
(`supabase_auth.py`) decodes **HS256 with `SUPABASE_JWT_SECRET`** (NOT
`JONA_JWT_SECRET`) and **requires `aud: "authenticated"`**. So a valid dev token
is:

```python
jwt.encode(
    {"sub": "<uuid>", "email": "...", "role": "authenticated",
     "aud": "authenticated", "iat": now, "exp": now + 3600},
    SUPABASE_JWT_SECRET, algorithm="HS256",
)
```

Signing with `JONA_JWT_SECRET` (the app/API-key secret) or omitting `aud` yields
`{"type":"auth_result","authenticated":false,"error":"Invalid token"}`.

The same error comes back when the backend sets `JONA_ALLOWED_EMAILS` (since
2026-11-07) and the token's email isn't on the list. The harness uses
`@dev.local` addresses, so run it against a backend without that setting, or
mint the token with an allowed email. Don't point it at production: the public
host also needs a Cloudflare Access token, and `/health` is blocked there.

The run recorded below used Haiku. The default model has since changed to
`JONA_MODEL` (Opus 5.5 at `max` effort), which costs about ten times more per
query.

### Run it

```bash
# backend must be up (launchd com.jonaai.backend); confirm:
curl -s localhost:8080/health

export PYTHONPATH="$PWD:$PWD/openeo-fastapi${PYTHONPATH:+:$PYTHONPATH}"
# one prompt, or several run as a single multi-turn conversation (context carries):
.venv/bin/python scripts/jona_chat.py "compute NDVI for Punjab, June 2024" "now do the same for June 2023" --timeout 180
```

A **real** run makes real Claude API calls (cost) and, for analysis prompts,
pulls real STAC/satellite data (slow, large). Keep discovery/planning prompts
cheap; scope analysis prompts to a tiny AOI + short date range.

## 2026-07-08 run — discovery prompt

Prompt: *"Which satellite collections and spectral indices can you work with?
Just list them concisely — do not run any analysis."*

Observed (stream + `/tmp/jonaai-backend.log`):

- Routing: `Tier=moderate (planner+worker)` → planner produced 2 steps →
  worker `model=claude-haiku-4-5`.
- InProcess MCP registered **196 domain tools** across 11 servers (planner,
  openeo_data/viz/ml/qa, **sentinel**, **data_scout**, **methodologist**,
  **publisher**, **timeseries** — all shipped personas live).
- Guards work: every call logged `[AUDIT] ALLOW`; one `Bash` was **`DENY`'d**
  (`Suspicious pattern matched: '[;|&\`$]'`).
- Cost/latency: `total_cost_usd=0.2447`, 3 turns, ~179s wall-clock.

### 🚩 Defect surfaced: worker fabricated the catalog instead of calling tools

The Haiku worker called generic agent tools — `Task`, `Bash`, `Write`, `Read`,
`ToolSearch`, `ListMcpResourcesTool` — and **never invoked
`openeo_list_collections` / `spectral_index_search`**, even though it
`ToolSearch`'d and found them (they are **deferred** — `total_deferred_tools:
586`). It then **hand-authored** the answer:

- a `Bash` heredoc literally commented
  `# This is a placeholder - in actual usage, this would call the real OpenEO backend`;
- a `Write` to `/tmp/spectral_indices.json` with hand-written index definitions,
  `Read` back to "confirm";
- the final answer even listed collections the backend does not serve
  (Sentinel-3/5P, MODIS, Planet, GEBCO).

So the user received an authoritative-looking catalog that is **partly invented
by the model**, violating the product's "tool-mediated, real data, no
fabrication" invariant.

**Root-cause hypothesis (not yet confirmed):** the planner+worker path exposes
the Claude Agent SDK built-ins (`Bash`/`Write`/`Task`/`ToolSearch`) upfront
while the 196 domain tools are deferred behind `ToolSearch`; a cheap Haiku
worker reaches for the always-visible generic tools and fabricates rather than
loading+calling the real ones.

**Open follow-ups:**
1. Why are domain tools deferred while `Bash`/`Write` are live in the worker path?
2. Does the same fabrication happen on a real analysis prompt (e.g. NDVI on a
   small AOI)? If the worker fabricates *statistics* the way it fabricated the
   catalog, that is critical.

**RESOLVED (2026-07-12):** the hypothesis was
confirmed — the CLI defers ALL MCP tools behind ToolSearch when definitions exceed
~40K chars (197 tools = 167K), leaving only the SDK built-ins eager, and Haiku
fabricates rather than load-then-call. Fixed by the curated `CORE_TOOL_NAMES`
slim-down (35 tools, eager again) + `--strict-mcp-config` isolation; the #18
acceptance driver passed 3/3 with zero ToolSearch, a real job-creating tool call,
and a map on every run. Both follow-ups above are closed.
