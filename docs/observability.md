# JonaAI Observability Operations Guide

This document describes the production-grade Langfuse observability + evals setup
for JonaAI. The code-side work is done (Phases A–I of the observability plan).
This guide covers the **UI-configured** pieces: annotation queues, dashboards,
alerts, and the nightly production eval baseline.

All steps assume you are signed in to the JonaAI Langfuse US cloud project:
<https://us.cloud.langfuse.com>.

---

## 1. What's already wired up in code

| Dimension | Source | Where in code |
|---|---|---|
| Root span name `jonaai.chat.query` + curated input/output | `_run_query` wraps `bridge.query()` with `set_root_trace_name()` | `openeo_ai/web_interface.py` |
| User ID (Supabase sub) | `propagate_attributes(user_id=...)` | `openeo_ai/web_interface.py` |
| Session ID | `propagate_attributes(session_id=...)` | `openeo_ai/web_interface.py` |
| Tool span names `tool.<name>` + masked args + `tool.category` attr | PostToolUse hook calls `annotate_tool_span()` | `openeo_ai/sdk/claude_sdk_bridge.py` |
| `implicit-tool-errors` score (per tool error) | PostToolUse hook | `openeo_ai/sdk/claude_sdk_bridge.py` |
| `quality_<metric>` scores (auto from `openeo_quality_metrics` tool) | WS handler | `openeo_ai/web_interface.py` |
| Prompt management (6 system prompts, 1 judge prompt) | `openeo_ai/sdk/prompts.py` fetcher, `scripts/migrate_prompts_to_langfuse.py` uploader | `openeo_ai/sdk/prompts.py` |
| `trace` WebSocket event → frontend | `_run_query` emits after completion | `openeo_ai/web_interface.py` |
| `user-thumbs` + `implicit-copy` scores | `ChatMessage.tsx` thumbs buttons via `LangfuseWeb` | `openeo_ai/frontend/src/components/chat/ChatMessage.tsx` |
| `implicit-load-on-map` score | `load_saved_job` handler | `openeo_ai/web_interface.py` |
| `judge-{relevance,completeness,correctness}` (LLM-as-judge) | Async `judge_response()` task | `openeo_ai/evals/judge.py` |
| Golden dataset (20 reference queries) | `openeo_ai/evals/golden_dataset.py` | Dataset `jonaai-golden-v1` |
| Experiment runner (fake WS + full trace scoring) | `openeo_ai/evals/run_experiment.py` | Tags traces with `jonaai` + `experiment` + `<run_name>` |
| CI regression gate | `openeo_ai/evals/assert_no_regression.py` | `.github/workflows/evals.yml` |

---

## 2. Initial bootstrap checklist

Run these once per Langfuse project. They populate data the rest of this guide
depends on.

### 2.1 Upload the 6 system prompts + judge prompt

```bash
source .env
python scripts/migrate_prompts_to_langfuse.py
```

Verify at: **Langfuse → Prompts**. You should see:

- `jonaai/chat-assistant`
- `jonaai/multi-agent/planner`
- `jonaai/multi-agent/data-specialist`
- `jonaai/multi-agent/viz-specialist`
- `jonaai/multi-agent/ml-specialist`
- `jonaai/multi-agent/qa-specialist`
- `jonaai/evals/response-judge`

All should be labeled `production`.

### 2.2 Upload the golden dataset

```bash
python scripts/create_golden_dataset.py
```

Verify at: **Langfuse → Datasets → `jonaai-golden-v1`**. Should show 20 items.

### 2.3 Establish the production baseline

```bash
# Run the experiment against main as the "production" baseline.
# This creates the traces the PR regression gate will compare against.
python -m openeo_ai.evals.run_experiment --experiment production
```

Re-run nightly (or on every merge to main) to keep the baseline fresh.
A scheduled GitHub Actions job is the cleanest way to automate this.

---

## 3. Annotation Queue (Phase H)

Flag low-quality traces for manual review.

### 3.1 Create the queue

1. **Langfuse → Annotation Queues → + New queue**
2. **Name**: `JonaAI Low Quality`
3. **Description**: Traces where auto-judge or tool-error signals indicate a bad response.
4. **Score configs** to assign: `judge-relevance`, `judge-completeness`,
   `judge-correctness`, `user-thumbs`
5. **Assign reviewers**: add team members who should see the queue.

### 3.2 Auto-populate the queue with a filter

On the queue page, set the **auto-enqueue filter**:

```
scores.judge-relevance < 0.5
OR scores.implicit-tool-errors < 0
OR scores.user-thumbs = 0
```

Now every trace that meets any of those conditions lands in the queue within
a few seconds of being scored.

### 3.3 Review workflow

Reviewers:

1. Open **Annotation Queues → JonaAI Low Quality**
2. Click a trace, read the user query and assistant response
3. Fill in the score configs (e.g. mark `judge-relevance` as `0` if the auto-judge
   was wrong, or `1` if the response actually was fine)
4. Leave a comment explaining *why*
5. Mark the trace as **Reviewed**

Reviewed annotations train the team's mental model of what's acceptable and
feed into future prompt iterations.

---

## 4. Production Dashboard (Phase J)

Create a single dashboard that shows the health of the JonaAI AI layer at a glance.

### 4.1 Create the dashboard

**Langfuse → Dashboards → + New dashboard**
**Name**: `JonaAI Production`

### 4.2 Cards to add

| Card | Type | Query / config |
|---|---|---|
| **Daily Active Users** | Number / Line | `COUNT DISTINCT user_id` where `trace.name = "jonaai.chat.query"`, grouped by day |
| **Query volume** | Time series | `COUNT(traces)` per hour, last 7 days, filter `tag = "jonaai"` |
| **Token usage** | Time series | `SUM(input_tokens) + SUM(output_tokens)` per day, filter `tag = "jonaai"` |
| **Cost** | Time series | `SUM(total_cost_usd)` per day |
| **Judge scores (7-day mean)** | Line | average of `judge-relevance`, `judge-completeness`, `judge-correctness` over 7 days |
| **Tool error rate** | Percentage | count of `implicit-tool-errors < 0` / total traces, last 24h |
| **User thumbs ratio** | Percentage | `user-thumbs = 1` / all `user-thumbs` scores, last 7d |
| **Top 10 slowest queries** | Table | traces sorted by `latency DESC`, filter `tag = "jonaai"`, last 24h |
| **Queries by tool category** | Bar chart | group by `tool.category` span attribute |
| **Queries per Supabase user** | Bar chart | top 10 `user_id` by trace count, last 30d |

### 4.3 Pin the dashboard

Add it to your Langfuse workspace favorites so the ops team sees it on login.

---

## 5. Alerts (Phase J)

**Langfuse → Alerts → + New alert** for each:

### 5.1 Judge relevance dropping

- **Name**: `JonaAI relevance regression`
- **Trigger**: `judge-relevance` daily mean drops below `0.70`
- **Evaluation window**: last 24 hours
- **Cool-down**: 1 hour (don't spam)
- **Webhook**: post to `#jonaai-alerts` Slack channel

### 5.2 Tool error rate spike

- **Name**: `JonaAI tool error spike`
- **Trigger**: fraction of traces with `implicit-tool-errors < 0` exceeds `5%`
- **Evaluation window**: last 1 hour
- **Webhook**: same Slack channel

### 5.3 Cost spike

- **Name**: `JonaAI cost anomaly`
- **Trigger**: hourly `SUM(total_cost_usd)` exceeds `$10`
- **Evaluation window**: last 1 hour
- **Webhook**: Slack + email to on-call

This alert only reports. Since 2026-11-07 the app also stops spend itself, through
`JONA_MAX_QUERY_USD`, `JONA_USER_DAILY_USD` and `JONA_MONTHLY_USD` (see "Limits" in
CLAUDE.md). With the default model at Opus 5.5, $10 an hour is reachable by a
single busy user, so set this threshold from the configured limits.

### 5.4 P95 latency regression

- **Name**: `JonaAI latency regression`
- **Trigger**: P95 trace latency exceeds `30000` ms
- **Evaluation window**: last 30 minutes
- **Webhook**: Slack

---

## 6. CI Integration (Phase I)

`.github/workflows/evals.yml` runs the golden dataset on every PR that touches
`openeo_ai/sdk/**`, `openeo_ai/tools/**`, `openeo_ai/evals/**`, or
`openeo_ai/langfuse_setup.py`.

### 6.1 Required GitHub secrets

```
LANGFUSE_PUBLIC_KEY         pk-lf-...
LANGFUSE_SECRET_KEY         sk-lf-...
ANTHROPIC_API_KEY           sk-ant-...
SUPABASE_URL                https://xxx.supabase.co
SUPABASE_ANON_KEY           sb_publishable_...
SUPABASE_SERVICE_ROLE_KEY   eyJ...
SUPABASE_JWT_SECRET         <from Supabase Dashboard → API → JWT Settings>
POSTGRES_USER               postgres.<project_ref>
POSTGRES_PASSWORD           <URL-encoded password, e.g. '%40' for '@'>
POSTGRESQL_HOST             aws-1-<region>.pooler.supabase.com
POSTGRESQL_PORT             6543
POSTGRES_DB                 postgres
```

### 6.2 PR workflow

1. Dev opens a PR touching the AI layer
2. `evals.yml` kicks off
3. CI runs all 20 golden queries, scoring each trace with judge + expected-tools + latency
4. `assert_no_regression.py` fetches aggregate scores for `pr-<N>` and compares to `production`
5. If any positive dimension regressed > 0.05, CI fails
6. A bot comment links to the Langfuse traces view filtered to `tag = pr-<N>`

### 6.3 Debugging a failing PR

1. Click the Langfuse link in the PR comment
2. Inspect individual traces for what changed
3. Look at which `exp-tool-*` scores flipped to 0
4. Compare to a successful baseline trace side-by-side (Langfuse Trace → Compare)

---

## 7. Operational runbook

### 7.1 Prompt updated a prompt in Langfuse UI

No code change needed — restart the backend (`bash start-local.sh`). The
`@lru_cache` in `openeo_ai/sdk/prompts.py` is process-local and only
refreshes on boot. For blue/green deploys this is painless; for hot
reloads you can call `clear_cache()` from an admin endpoint.

### 7.2 Judge is producing bad scores

1. Open the trace → find the `judge-*` scores → read the `reasoning` comment
2. If the judge is wrong consistently, update `jonaai/evals/response-judge` in Langfuse UI
3. If individual traces are wrong, manually adjust them via the Annotation Queue
4. The new judge prompt takes effect on the next backend restart

### 7.3 Rotating Langfuse keys

1. **Langfuse → Settings → API Keys → Regenerate**
2. Update `.env` (local) and GitHub secrets (CI) with the new `pk-lf-...` /
   `sk-lf-...`
3. Restart the backend
4. Update `VITE_LANGFUSE_PUBLIC_KEY` in frontend `.env.local` if the public
   key changed — frontend needs a rebuild

### 7.4 Disabling observability temporarily

Unset `LANGFUSE_PUBLIC_KEY` (or set it to empty) in `.env` and restart.
`init_langfuse()` logs a warning and becomes a no-op; the rest of the app
keeps working. Traces stop being recorded until you re-enable.

---

## 8. Cost notes

- **Trace storage**: Langfuse US cloud free tier covers ~50k traces/month.
  Each JonaAI query creates 1 trace with 10-30 child spans.
- **LLM-as-judge**: Each judged response = 1 Claude Haiku call (~500 tokens
  in, ~150 tokens out ≈ $0.0003 per query). For 1000 queries/day this is
  ~$9/month.
- **Golden dataset CI run**: 20 queries per PR at roughly $0.02 each
  (includes the judge) ≈ $0.40 per PR run.
- **Nightly production baseline**: same $0.40 per night ≈ $12/month.

Total: around **$25-40/month** for tracing + evals at moderate usage.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No traces appearing | `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` missing or wrong host | Check `.env`, restart backend, look for `[langfuse] authenticated OK` in startup logs |
| Frontend thumbs buttons do nothing | `VITE_LANGFUSE_PUBLIC_KEY` or `VITE_LANGFUSE_HOST` missing in `.env.local` | Set them, `npm run dev` to pick up the change |
| `trace` WS event not fired | `current_trace_id()` returns `None` | Verify `configure_claude_agent_sdk()` ran at startup |
| Judge scores missing | `ANTHROPIC_API_KEY` missing OR `response_text` empty | Check backend logs for `[judge] ... skipping` |
| CI gate fails on first PR | No `production` baseline exists yet | Run `python -m openeo_ai.evals.run_experiment --experiment production` manually once |
| Scores land on wrong trace | Async judge fired after the trace context was torn down | Ensure `trace_id` is captured from `current_trace_id()` BEFORE `asyncio.create_task()` and passed explicitly |
| Pydantic V1 warning at startup | langfuse 4.x imports pydantic.v1 on Python 3.14 | Cosmetic only, safe to ignore until langfuse drops V1 compat |

---

## 10. Related docs

- Langfuse skill: `~/.agents/skills/langfuse/SKILL.md`
- Instrumentation baseline checklist: `~/.agents/skills/langfuse/references/instrumentation.md`
- User feedback patterns: `~/.agents/skills/langfuse/references/user-feedback.md`
- Prompt migration guide: `~/.agents/skills/langfuse/references/prompt-migration.md`
- JonaAI plan file: `~/.claude/plans/hashed-drifting-moore.md`
