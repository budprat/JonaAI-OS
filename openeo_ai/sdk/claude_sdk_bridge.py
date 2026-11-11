"""Bridge between Claude Agent SDK and WebSocket for the OpenEO AI chat interface.

Routes SDK messages (StreamEvent, AssistantMessage, ResultMessage) and hook
callbacks (PreToolUse, PostToolUse) to WebSocket messages matching the existing
frontend protocol.

Architecture:
    WebSocket msg → ClaudeSDKBridge.query(prompt, websocket)
        ├── ClaudeSDKClient with McpStdioServerConfig → mcp_stdio_server.py
        ├── PreToolUse hook  → tool_start + thinking + permission check
        ├── PostToolUse hook → tool_result + viz + quality WS messages
        └── StreamEvent loop → text_stream_start/delta/end WS messages
"""

import asyncio
import contextvars
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
import weakref
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Structured audit logger — writes to stderr alongside existing SDK logs
_audit_log = logging.getLogger("openeo.audit")
if not _audit_log.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter(
        "[AUDIT %(asctime)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    ))
    _audit_log.addHandler(_handler)
    _audit_log.setLevel(logging.INFO)
    _audit_log.propagate = False

logger = logging.getLogger(__name__)

# Phase 16: Langfuse @observe decorator for explicit span creation.
# Falls back to a no-op decorator if langfuse isn't installed so the
# bridge works in environments without observability.
try:
    from langfuse import observe as _lf_observe  # type: ignore
except ImportError:  # pragma: no cover — defensive fallback
    def _lf_observe(*args, **kwargs):  # type: ignore
        def _decorator(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return _decorator

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

from openeo_ai import budget

from .client import (
    OpenEOAIClient, OpenEOAIConfig, TOOL_DEFINITIONS,
    CORE_TOOL_NAMES,
    PLANNER_SYSTEM_PROMPT, PLANNER_TOOL_NAMES,
    SPECIALIST_CONFIGS,
)
from .inprocess_tools import (
    create_core_mcp_server, create_openeo_mcp_server, create_planner_mcp_server,
    create_specialist_mcp_server,
)
from .permissions import (
    _validate_graph_structure,
    BLOCKED_TOOLS, GENERIC_BUILTIN_TOOLS_DENY, RATE_LIMIT_DEFAULT, RATE_LIMITS,
    READ_ONLY_TOOLS, SAFE_MODIFY_TOOLS, _validate_extent, check_suspicious_input,
)
from .sessions import SessionManager
from .domain_knowledge import classify_domain, get_domain_context
from .viz_extractor import extract_visualization



def _positive_env(name: str, default: float) -> float:
    """A positive number from the environment, or the default if unset or invalid."""
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


# Each idle session holds an agent process of about 250 MB. Ended sessions resume from
# their saved history on the next message, so these limits cost only start-up time.
_SDK_SESSION_TTL_SECONDS = int(_positive_env("JONA_SDK_SESSION_TTL_SECONDS", 1800))
_SDK_SESSION_MAX_COUNT = int(_positive_env("JONA_SDK_SESSION_MAX_COUNT", 100))
_SESSION_REAP_INTERVAL_SECONDS = 30

# About two characters per token on dense text, the worst case seen, against a 1M context.
_MAX_PROMPT_CHARS = 2_000_000
# A plan adds little to a question that is mostly pasted text, and would pay to read it twice.
_PLANNER_MAX_PROMPT_CHARS = 200_000


_PLANNING_PROGRESS_SECONDS = 30
_CLIENT_REUSE_BUDGET_SHARE = 0.1
_JOB_START_TOOLS = frozenset({
    "openeo_create_job", "openeo_start_job", "agentic_decision_query", "execute_workflow",
    "openeo_aggregate_spatial", "openeo_apply_kernel",
})
_MAX_IDENTICAL_JOB_SUBMISSIONS = 2

# How long an AskUserQuestion waits for the person to answer. Named rather than inline
# so it is discoverable and so a test can shorten it instead of reconstructing the hook.
_CLARIFICATION_TIMEOUT_SECONDS = 600


def _turn_notes(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What a session entry holds besides its agent process: this turn's trace and text."""
    return {key: value for key, value in (entry or {}).items() if key.startswith(("last_", "turn_"))}


class _ModelAccessError(Exception):
    """A model call can't succeed for this query, so retrying or falling back only wastes time."""


def _model_access_problem(detail: Optional[str], subtype: Optional[str] = None) -> Optional[str]:
    """Return what to tell the user if a model error will recur on any retry, else None."""
    if subtype == "error_max_budget_usd":
        return (
            f"This query reached its ${_max_query_usd():.2f} spending limit and was stopped. "
            "Anything above was completed before the limit."
        )
    text = (detail or "").lower()
    cleaned = re.sub(r"^API Error:\s*\d*\s*", "", detail or "").strip()
    if "usage limit" in text or "credit balance" in text:
        return f"The AI service can't be used right now. {cleaned}"
    if "prompt is too long" in text:
        return "That message is too long for the model to read. Shorten it or split it into parts."
    if "authentication_error" in text or "invalid x-api-key" in text or "invalid api key" in text:
        return "The AI service rejected this server's credentials. An administrator needs to fix the API key."
    return None


def _max_query_usd() -> float:
    return _positive_env("JONA_MAX_QUERY_USD", 10.0)


# Spend on the current query, shared by its planner, worker, and specialist clients.
_query_spend: contextvars.ContextVar[Optional[Dict[str, float]]] = contextvars.ContextVar(
    "query_spend", default=None
)


def _record_spend(holder: Dict[str, Any], message: Any) -> None:
    """Add a result's cost to this query. A client reports its running total."""
    total = getattr(message, "total_cost_usd", None)
    if total is None:
        return
    previous = holder.get("reported_cost", 0.0)
    holder["reported_cost"] = total
    spend = _query_spend.get()
    if spend is not None:
        spend["usd"] = spend.get("usd", 0.0) + (total - previous if total >= previous else total)


def _strip_mcp_prefix(tool_name: str) -> str:
    """Strip MCP server prefix: mcp__openeo*__X or mcp__earth_agent__X → X

    Handles both base prefix (mcp__openeo__) and specialist prefixes
    (mcp__openeo_data__, mcp__openeo_viz__, mcp__openeo_planner__, etc.).
    """
    if tool_name.startswith("mcp__openeo") or tool_name.startswith("mcp__earth_agent"):
        parts = tool_name.split("__", 2)  # ['mcp', 'openeo*', 'tool_name']
        if len(parts) == 3:
            return parts[2]
    return tool_name


def _full_toolset_enabled() -> bool:
    """JONA_FULL_TOOLSET=1 restores the full tool surface."""
    return os.environ.get("JONA_FULL_TOOLSET", "").lower() in ("1", "true", "yes", "on")


# Without --strict-mcp-config the CLI merges filesystem MCP configs
# (the operator's personal servers in ~/.claude.json) into every backend
# session — measured 458 tools vs 57 — inflating the surface past the
# auto-eager threshold AND exposing operator tools to chat sessions.
_STRICT_MCP_ARGS = {"strict-mcp-config": None}


def _core_allowed_tools() -> list:
    """allowed_tools for main/worker sessions — core set unless rolled back.

    Keeping the surface at CORE_TOOL_NAMES holds the tool
    definitions under the CLI's auto-eager threshold so nothing defers behind
    ToolSearch.
    """
    defs = TOOL_DEFINITIONS if _full_toolset_enabled() else [
        td for td in TOOL_DEFINITIONS if td["name"] in CORE_TOOL_NAMES
    ]
    allowed = [f"mcp__openeo__{td['name']}" for td in defs]
    # AskUserQuestion stays out of this list on purpose: pre-approved tools skip
    # can_use_tool, which is what actually puts the question to the user.
    return allowed


def _tool_display_name(tool_name: str) -> str:
    """Format tool name for display: openeo_list_collections → List Collections"""
    name = tool_name
    for prefix in ("openeo_", "viz_", "geoai_", "saved_jobs_", "ea_"):
        name = name.replace(prefix, "")
    return name.replace("_", " ").title()


# --- Three-tier query complexity classification ---
# simple   → Haiku direct (no planning, fast lookups)
# moderate → Planner (Haiku) + Worker (Haiku) — single-agent execution
# complex  → Multi-agent: Orchestrator + Specialist delegation

from .method_gate import (
    asks_for_burn_severity,
    check_graph_method,
    compares_periods,
    forbids_execution,
    graph_fingerprint,
)  # noqa: E402

# Every agent phase runs on this model; its 1M context window is the API default.
MODEL_DEFAULT = os.environ.get("JONA_MODEL", "").strip() or "claude-opus-5-5"
# Opus 5.5 thinking can't be turned off or budgeted: effort is the only control.
MODEL_EFFORT = os.environ.get("JONA_EFFORT", "").strip() or "max"
MODEL_HAIKU = "claude-haiku-4-5-20251001"
# Load tools up front: lookups through ToolSearch spend the planner's limited turns.
os.environ.setdefault("ENABLE_TOOL_SEARCH", "false")
MODEL_SONNET = "claude-sonnet-5"   # escalation target for complex workflows (opt-in via env)

# Tier 1: Simple lookups — no planning needed
_SIMPLE_PATTERNS = frozenset([
    "list", "status", "describe", "what collections", "help", "hello", "hi",
    "available", "which bands", "show me", "get info", "check job",
    "what is", "explain", "how to", "capabilities", "supported",
])

# Tier 3: Complex multi-specialist queries — need orchestrator + specialist routing
_COMPLEX_MULTI_AGENT_PATTERNS = frozenset([
    "change detection", "time series", "temporal comparison",
    "compare", "fusion", "multi-sensor", "multi-temporal",
    "seasonal", "anomaly", "trend", "regression",
    "land cover", "deforestation", "urban growth",
    "canopy height", "burn severity", "fire scar", "flood extent",
    # Additional patterns for Decision Layer / Earth-Agent triggers
    "earth-agent", "earth agent", "post-processing",
    "classification", "classify", "threshold",
    "multiple indices", "multi-index", "comprehensive",
    "assessment", "health assessment", "risk assessment",
    "before and after", "two periods", "both periods",
    "january and march", "baseline and current",
    "water stress", "drought", "irrigation",
    "vegetation stress", "crop health", "farm health",
    "snow cover", "glacier", "ice mapping",
])

# Tier 2: Everything else — single planner+worker (NDVI, EVI, single-index,
# elevation, single-collection analysis, cloud masking, etc.)


# Unambiguous durable-monitoring phrases that route to the Sentinel specialist.
# Deliberately excludes bare "watch" (appears in one-off "watch the NDVI change").
_SENTINEL_ROUTING_KEYWORDS = [
    "alert me", "notify me", "keep watching", "keep an eye",
    "keep monitoring", "monitor this", "monitor the", "set up monitoring",
    "watch this", "every week", "every day", "weekly", "daily",
]


# Decision-grade intent → route straight to the self-contained agentic-EO pipeline.
# That pipeline is monolithic (discover→analyze→report in one call), so it does not fit
# the decompose-based multi-agent tool mesh; the model won't reliably pick it there.
# A deterministic pre-model route (mirroring the Sentinel safety-net) guarantees it runs.
_DECISION_GRADE_PHRASES = ("decision-grade", "decision grade")
_DECISION_FRAMING = (
    "should i", "should we", "do i need", "do we need", "is it time",
    "worried about", "worry about", "take action", "act now",
)
_DECISION_EO_SIGNAL = (
    "ndvi", "ndwi", "ndmi", "nbr", "evi", "vegetation", "crop", "greenness",
    "canopy", "forest", "reservoir", "drought", "moisture", "irrigation",
    "burn", "wildfire", "biomass", "farm", "water level", "water extent",
)


def _is_decision_grade_query(prompt: str) -> bool:
    """True when the query asks for a defensible act/don't-act EO decision.

    Fires on an explicit 'decision-grade' phrase, or on a decision framing
    ('should I…') combined with an EO variable — deliberately tight so it does
    not hijack ordinary map/analysis requests.
    """
    p = prompt.lower()
    if any(k in p for k in _DECISION_GRADE_PHRASES):
        return True
    return (any(k in p for k in _DECISION_FRAMING)
            and any(k in p for k in _DECISION_EO_SIGNAL))


async def _emit_decision_report(websocket, prompt: str) -> None:
    """Run the agentic-EO pipeline and stream its decision report over the WebSocket.

    Emits tool_start/tool_result under tool_name 'agentic_decision_query' so the
    frontend routes the report to the Report panel (same path as a model-invoked
    tool), then a short text summary, then done. use_llm is off — the rule-based
    parser is authoritative and avoids a nested model call.

    On success, also emits the K4 Method Card as a live report_sections event.
    live=True adds it to the run's visualization bundle on the frontend; the
    bundle is only persisted when the run carries a saved result, which this
    route does not produce — so the card renders in-session, and persistence
    engages automatically once a decision run saves an artifact.
    """
    from ..agentic_eo import run_query
    from ..method_kernel.method_card import method_card_section, render_method_card

    tool_name = "agentic_decision_query"
    try:
        await websocket.send_json({
            "type": "thinking", "thinking_id": "decision-grade",
            "thinking_type": "executing",
            "content": "Running decision-grade Earth-observation analysis…",
        })
        await websocket.send_json({
            "type": "tool_start", "tool_name": tool_name, "tool_input": {"query": prompt},
        })
        out = await asyncio.to_thread(run_query, prompt, None, False, True)
        # The method-aptness gate returns report=None with a
        # method_clarification — same surfacing path as a missing AOI.
        clarification = out.intent.clarification_needed or out.intent.method_clarification
        if clarification:
            await websocket.send_json({
                "type": "tool_result", "tool_name": tool_name,
                "result": "Clarification needed:\n\n" + clarification,
            })
            await websocket.send_json({"type": "text", "content": clarification})
        else:
            await websocket.send_json({
                "type": "tool_result", "tool_name": tool_name,
                "result": out.report.to_markdown(),
            })
            # Surface the deterministic Method Card in the Report panel.
            # The render carries no timestamp (byte-identical for identical runs);
            # the emitter stamps it here. No cache/fidelity producers on this route.
            section = method_card_section(render_method_card(out.plan, out.execution))
            section["timestamp"] = datetime.now(timezone.utc).isoformat()
            section["toolName"] = tool_name
            await websocket.send_json({
                "type": "report_sections", "live": True, "sections": [section],
            })
            await websocket.send_json({"type": "text", "content": out.report.headline})
    except Exception as e:  # noqa: BLE001 — surface a clean failure, always close the turn
        print(f"[SDK Bridge] decision-grade route error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        await websocket.send_json({
            "type": "error",
            "content": "The decision-grade analysis could not be completed. Please try again or rephrase.",
        })
    finally:
        await websocket.send_json({"type": "done"})


def _data_abort_message(turn_tool_results: list) -> str:
    """Honest abort message: a job that outlived the polling window is still
    RUNNING, not failed — observed live: 'All analysis jobs failed' went out
    two minutes before the job completed successfully."""
    if any('"_polling_timeout": true' in t for t in turn_tool_results):
        return (
            "The analysis job is still running — it needs longer than the "
            "polling window. Ask me to check the job status in a minute and "
            "I'll fetch the results."
        )
    return (
        "All analysis jobs failed. I was unable to produce any results. "
        "Please check the error details above and try again with "
        "different parameters (smaller area, different time period, etc.)."
    )


def _inject_sentinel_monitoring(routing: list, plan_json: dict, prompt_lower: str) -> bool:
    """Route durable-monitoring intent to the Sentinel specialist AND give it a
    step to execute.

    The orchestrator plans monitoring as one-off analysis, so it never assigns a
    step to Sentinel — which left it routed but stepless, and therefore skipped
    (observed 2026-07-08). Inject both the routing entry and an explicit
    watch-creation step so Sentinel actually creates a durable Watch. Mutates
    ``routing`` and ``plan_json`` in place; returns True if it injected.
    """
    if "sentinel" in routing or not any(kw in prompt_lower for kw in _SENTINEL_ROUTING_KEYWORDS):
        return False
    routing.insert(0, "sentinel")  # monitoring set-up is the primary intent
    plan_json.setdefault("steps", []).insert(0, {
        "specialist": "sentinel",
        "tool": "monitor_create_watch",
        "purpose": (
            "Set up durable monitoring for the requested area: create the asset "
            "(monitor_create_asset), then a recurring Watch (monitor_create_watch) "
            "with the requested metric, threshold, and frequency. Do NOT run a "
            "one-off analysis — persist a Watch via the monitor_* tools."
        ),
    })
    return True


# Bridge-local change verbs BEYOND the Method Kernel's set. The kernel
# list stays authoritative for the decision route; these broaden the free-form
# route to deforestation/water/urban change without touching validate.py.
_BRIDGE_CHANGE_VERBS = (
    r"\bgain(?:ed|s)?\b", r"\bloss\b", r"\blost\b", r"\bgrow(?:th|n|s)?\b",
    r"\bgrew\b", r"\bshr(?:ink|ank|unk|inking)\b", r"\bexpand(?:ed|ing|s)?\b",
    r"\breced(?:e|ed|ing)\b", r"\bdeforest(?:ation|ed)?\b", r"\bregrowth\b",
    r"\bincrease[ds]?\b", r"\bdecrease[ds]?\b", r"\bdifference\b", r"\bdelta\b",
)

# Month names anchor "vs"/"between X and Y" to a TEMPORAL comparison. "may" is
# omitted on purpose (collides with the modal verb; a real "May 2024" carries a
# year anchor anyway).
_MONTH_TOKENS = (
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
)


def _prompt_has_change_verb(prompt_lower: str) -> bool:
    """Change-verb detection. The Method Kernel's set is imported (not
    copied, so the two cannot drift) and augmented with bridge-local verbs for
    the free-form route (deforestation/growth/loss/expand/etc.)."""
    from openeo_ai.method_kernel.validate import _CHANGE_VERBS
    return (any(re.search(p, prompt_lower) for p in _CHANGE_VERBS)
            or any(re.search(p, prompt_lower) for p in _BRIDGE_CHANGE_VERBS))


def _has_two_period_signal(prompt_lower: str) -> bool:
    """Does the prompt name two TEMPORAL comparison windows? A bare "vs" or
    "between X and Y" is NOT enough — a spatial comparison ("north vs south",
    "between the north and south fields") must not qualify, so those forms count
    only alongside a temporal anchor (a year or month name)."""
    if "before" in prompt_lower and "after" in prompt_lower:
        return True
    if len(set(re.findall(r"\b(?:19|20)\d{2}\b", prompt_lower))) >= 2:
        return True
    # "since <year>" implies now-vs-then (one explicit period + the present).
    if re.search(r"\bsince\b\s+(?:the\s+)?(?:19|20)\d{2}\b", prompt_lower):
        return True
    _temporal_anchor = bool(re.search(r"\b(?:19|20)\d{2}\b", prompt_lower)) or any(
        re.search(rf"\b{m}\b", prompt_lower) for m in _MONTH_TOKENS
    )
    if _temporal_anchor and (
        re.search(r"\bvs\.?\b", prompt_lower)
        or re.search(r"\bbetween\b.+\band\b", prompt_lower)
    ):
        return True
    return False


def _qa_last(routing: list) -> list:
    """Move 'qa' to the end of the specialist routing order.

    qa inspects result files that earlier specialists produce; a qa-first
    plan probes files that cannot exist yet and leaks a dead-end
    "I cannot execute jobs" reply to the user (seen live 2026-07-26 with
    routing ['qa', 'data', 'ml', 'viz']). A qa-only routing is left alone —
    pure inspection queries are legitimately qa-first.
    """
    if "qa" in routing and len(routing) > 1:
        others = [s for s in routing if s != "qa"]
        if others:
            return others + ["qa"]
    return routing


def _inject_change_detection(routing: list, plan_json: dict, prompt_lower: str) -> bool:
    """Steer two-period change queries to ONE difference-raster job.

    The planner sometimes plans a change question as two snapshot jobs and
    narrates the subtraction in prose (observed 2026-07-24, Palisades dNBR run).
    When the prompt has a change verb AND names two periods, inject an explicit
    difference-raster step (burn/fire wording → openeo_algorithm burn_severity)
    plus a threshold_classify step so class-area tables come from tool output.
    Mutates ``routing`` and ``plan_json`` in place; returns True if it injected.
    Single-window snapshot queries never fire.
    """
    if not (_prompt_has_change_verb(prompt_lower) and _has_two_period_signal(prompt_lower)):
        return False
    steps = plan_json.setdefault("steps", [])
    # Already planned as a difference-raster job? Leave the plan alone.
    for s in steps:
        if not isinstance(s, dict):
            continue
        blob = f"{s.get('tool', '')} {s.get('args', '')} {s.get('purpose', '')}".lower()
        if s.get("tool") == "openeo_algorithm" or "change_detection" in blob or "burn_severity" in blob:
            return False
    if any(w in prompt_lower for w in ("burn", "fire", "wildfire")):
        tool = "openeo_algorithm"
        detail = (
            "Compose ONE per-pixel dNBR difference raster with openeo_algorithm "
            "(algorithm='burn_severity', pre-fire and post-fire windows from the prompt)"
        )
    else:
        tool = "openeo_generate_graph"
        detail = (
            "Compose ONE change-detection difference raster with openeo_generate_graph "
            "(change detection analysis, both time windows in ONE job)"
        )
    steps.insert(0, {
        "specialist": "data",
        "tool": tool,
        "purpose": detail + (
            " — NEVER two snapshot jobs with the subtraction done in prose."
        ),
    })
    steps.append({
        "specialist": "ml",
        "tool": "threshold_classify",
        "purpose": (
            "Per-class area/percentage table computed from the difference raster "
            "via threshold_classify — never computed in prose. The whole table MUST "
            "come from ONE threshold_classify call and its class percentages MUST "
            "sum to 100% (the classes are disjoint) — never stitch it together from "
            "multiple statistics/histogram calls."
        ),
    })
    if "data" not in routing:
        routing.insert(0, "data")
    if "ml" not in routing:
        routing.append("ml")
    return True


# Completion-contract vocabulary. Difference-type openeo processes that
# prove a per-pixel comparison was actually composed, and the job/graph tools
# whose presence means the turn tried to compute something.
_DIFFERENCE_PROCESSES = {"merge_cubes", "subtract"}
# Tools that compute a real per-pixel difference WITHOUT an in-graph merge_cubes:
# result_difference subtracts two executed rasters pixel-by-pixel (a valid dNBR,
# not the scalar-mean fabrication defect). Their presence also satisfies the
# method contract — verified against a live Palisades run 2026-07-24.
_DIFFERENCE_TOOLS = {"result_difference"}
_OPENEO_JOB_TOOLS = {
    "openeo_generate_graph", "openeo_compose_graph", "openeo_raw_graph",
    "openeo_algorithm", "openeo_create_job", "openeo_start_job",
}
_ENFORCE_MODES = ("observe", "annotate", "block")


def _grounding_enforce_mode() -> str:
    """JONA_GROUNDING_ENFORCE ∈ {observe, annotate, block}; default (and
    fallback for unknown values) is "annotate" — NU decision 2026-07-24."""
    mode = os.environ.get("JONA_GROUNDING_ENFORCE", "annotate").strip().lower()
    return mode if mode in _ENFORCE_MODES else "annotate"


def _collect_process_ids(obj: Any, _depth: int = 0) -> set:
    """Walk a tool input/result payload for openeo "process_id" values.
    Depth/width bounded — cheap and fail-open by construction."""
    if _depth > 8 or not isinstance(obj, (dict, list)):
        return set()
    out: set = set()
    if isinstance(obj, dict):
        pid = obj.get("process_id")
        if isinstance(pid, str):
            out.add(pid)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                out |= _collect_process_ids(v, _depth + 1)
    else:
        for v in obj[:50]:
            out |= _collect_process_ids(v, _depth + 1)
    return out


def _method_gap_payload(
    prompt: str, graph_processes: list, tools_ran: list, period_difference_seen: bool = False
) -> "Optional[dict]":
    """Method contract: a change-verb turn that ran openeo job/graph tools
    but never composed a difference-type process (merge_cubes/subtract) narrated
    change it never computed. Pure — the caller decides whether to emit."""
    q = (prompt or "").lower()
    # A change verb ALONE over-fires (bare "since"/"vs" / a spatial "compare");
    # require the injector's two-period signal too, so method-gap and the
    # injector agree on what a change query is (no false gaps on non-comparisons).
    burn = asks_for_burn_severity(q)
    if not (burn or (_prompt_has_change_verb(q) and _has_two_period_signal(q))):
        return None
    if not any(t in _OPENEO_JOB_TOOLS for t in (tools_ran or [])):
        return None
    # A burn run needs two dates compared; band math in NBR also lists subtract.
    if burn and period_difference_seen:
        return None
    if not burn and any(p in _DIFFERENCE_PROCESSES for p in (graph_processes or [])):
        return None
    # A per-pixel difference TOOL (result_difference) satisfies the contract too,
    # even without an in-graph merge_cubes node.
    if any(t in _DIFFERENCE_TOOLS for t in (tools_ran or [])):
        return None
    return {
        "type": "method_gap",
        "detail": (
            "The prompt asks about change between periods, but no difference "
            "raster (merge_cubes/subtract) was computed this turn — change "
            "claims are not grounded in a per-pixel comparison."
        ),
        "processes_seen": sorted(set(graph_processes or [])),
    }


def _prompt_requests_map(prompt_lower: str) -> bool:
    """An explicit map request — word-boundary "map" (verb "map the ..."
    or noun "... map"). Deliberately narrow: "show me the NDVI value" is not a
    map request, so index terms alone do not qualify (avoids false deliverable
    gaps under the annotate default)."""
    return bool(re.search(r"\bmap\b", prompt_lower))


def _deliverable_gap_payload(prompt: str, viz_types: list, tools_ran: list) -> "Optional[dict]":
    """Deliverable contract: the user asked for a map, an analysis JOB ran,
    and no map visualization was emitted this turn. Gated on a job tool (not any
    tool) so a planning/confirm turn — discovery + quality checks, then "shall I
    proceed?" — does not false-fire (observed live 2026-07-24). Pure."""
    q = (prompt or "").lower()
    if not _prompt_requests_map(q):
        return None
    if not any(t in _OPENEO_JOB_TOOLS for t in (tools_ran or [])):
        return None
    if "map" in (viz_types or []):
        return None
    return {
        "type": "deliverable_gap",
        "detail": "The prompt requested a map, but no map visualization was produced this turn.",
        "viz_types_seen": sorted({t for t in (viz_types or []) if t}),
    }


def _class_table_payload(text: str) -> "Optional[dict]":
    """Class-table contract: a per-class table whose percentages do not sum
    to 100% is not a partition of the raster — in the live Palisades run the table
    was stitched from several tool outputs. Pure and fail-open by construction:
    any internal error means "no finding"."""
    try:
        from openeo_ai.sdk.grounding_guard import check_class_table_sums

        # Check each specialist's prose SEPARATELY: concatenating them lets one
        # specialist's unbalanced ``` fence invert fence state for the next, and lets
        # two adjacent correct tables read as one impossible column.
        texts = text if isinstance(text, (list, tuple)) else [text or ""]
        violations = []
        for chunk in texts:
            violations.extend(check_class_table_sums(chunk or ""))
        if not violations:
            return None
        worst = max(violations, key=lambda v: abs(v.total - 100.0))
        return {
            "type": "class_table_gap",
            "detail": (
                f"A per-class table's percentages sum to {worst.total:.2f}%, which is "
                "impossible for disjoint classes — the table was likely stitched "
                "from multiple tool outputs."
            ),
            "violations": [
                {"column_label": v.column_label, "total": v.total, "rows": v.rows}
                for v in violations
            ],
        }
    except Exception:
        return None


def _class_table_notice_enabled() -> bool:
    """Is the class-table finding allowed into the USER-VISIBLE notice?

    Default NO. "Is this percentage column a partition of one whole?" is not
    reliably decidable from markdown, and three review rounds each produced fresh
    verified false accusations on legitimate answers (per-scene coverage columns,
    grouped per-region tables, quoted/fenced examples). The finding still fires as
    a class_table_gap event + Langfuse score, so precision can be measured on real
    traffic and the notice promoted with JONA_CLASS_TABLE_NOTICE=1 once earned.
    Mirrors how the grounding guard itself shipped observe-first.
    """
    return os.environ.get("JONA_CLASS_TABLE_NOTICE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _enforcement_notice(mode: str, grounding_count: int, method_gap: bool,
                        deliverable_gap: bool, class_table_gap: bool) -> "Optional[str]":
    """One short honest notice chunk for annotate/block modes, or None.
    Streamed text cannot be unsent, so even "block" can only append a stronger
    retraction notice after the fact — it does not rewrite what already reached
    the client."""
    if mode == "observe":
        return None
    parts = []
    if grounding_count:
        parts.append(
            f"{grounding_count} numeric claim(s) could not be verified against tool outputs"
        )
    if method_gap:
        parts.append("the change analysis did not compute a per-pixel difference raster")
    if deliverable_gap:
        parts.append("the requested map was not produced this turn")
    if class_table_gap:
        parts.append("a per-class table's percentages do not sum to 100%")
    if not parts:
        return None
    if mode == "block":
        return (
            "\n\nRetraction: the response above is unreliable — "
            + "; ".join(parts)
            + ". Disregard the affected claims and re-run the analysis."
        )
    return "\n\nNote: " + "; ".join(parts) + "."


@_lf_observe(name="jonaai.classify_query_tier")
def _classify_query_complexity(prompt: str) -> str:
    """Classify query into simple/moderate/complex tiers.

    simple   → Direct Haiku, no planning (lookups, status checks)
    moderate → Planner + single Worker (single-index, basic analysis)
    complex  → Multi-agent orchestrator + specialists (change detection, fusion)

    Phase 16/A: wrapped in @observe so the routing decision is a named
    Langfuse span with prompt-length + tier as metadata. Makes misrouted
    queries trivially debuggable in the trace tree.
    """
    lower = prompt.lower().strip()

    tier: str  # assigned below
    reason: str  # why this tier

    # Very short prompts are usually simple
    if len(lower) < 20:
        for p in _SIMPLE_PATTERNS:
            if p in lower:
                tier, reason = "simple", f"short prompt (<20 chars) matches _SIMPLE_PATTERNS: {p!r}"
                _annotate_classify_span(prompt, tier, reason)
                return tier

    # Check for complex multi-agent patterns first (longer matches first)
    for p in _COMPLEX_MULTI_AGENT_PATTERNS:
        if p in lower:
            tier, reason = "complex", f"matched _COMPLEX_MULTI_AGENT_PATTERNS: {p!r}"
            _annotate_classify_span(prompt, tier, reason)
            return tier

    # Durable-monitoring intent → complex tier, so the Sentinel specialist (which
    # owns monitor_create_asset/monitor_create_watch) handles it. Otherwise these
    # fall to the moderate worker, which builds a one-off analysis instead of
    # creating a Watch (observed 2026-07-08). Same keyword list as the Sentinel
    # routing safety-net in _multi_agent_query.
    for kw in _SENTINEL_ROUTING_KEYWORDS:
        if kw in lower:
            tier, reason = "complex", f"matched _SENTINEL_ROUTING_KEYWORDS: {kw!r}"
            _annotate_classify_span(prompt, tier, reason)
            return tier

    # Check for simple patterns
    for p in _SIMPLE_PATTERNS:
        if lower.startswith(p):
            tier, reason = "simple", f"starts with _SIMPLE_PATTERNS: {p!r}"
            _annotate_classify_span(prompt, tier, reason)
            return tier

    # Default: moderate (single planner+worker, covers most EO queries)
    tier, reason = "moderate", "default fallback (covers most EO queries)"
    _annotate_classify_span(prompt, tier, reason)
    return tier


def _resolve_tier(prompt: str, force_tier: "Optional[str]" = None) -> str:
    """Honour an explicit routing-arm override (the persona A/B harness pins the arm),
    otherwise classify normally. Valid overrides: simple | moderate | complex."""
    if force_tier in ("simple", "moderate", "complex"):
        return force_tier
    return _classify_query_complexity(prompt)


def _annotate_classify_span(prompt: str, tier: str, reason: str) -> None:
    """Attach tier/reason as span output — safe no-op if Langfuse off."""
    try:
        from ..langfuse_setup import get_langfuse_client
        client = get_langfuse_client()
        if client is None:
            return
        client.update_current_span(
            input={"prompt_preview": prompt[:200], "prompt_len": len(prompt)},
            output={"tier": tier, "reason": reason},
            metadata={"tier": tier},
        )
    except Exception:
        pass


def _apply_reasoning(opts: Any) -> None:
    """Set thinking and effort for opts.model. Haiku 4.5 supports neither form."""
    if opts.model == MODEL_HAIKU:
        opts.thinking = None
        opts.effort = None
    else:
        opts.thinking = {"type": "adaptive"}
        opts.effort = MODEL_EFFORT
    limit = _max_query_usd()
    spent = (_query_spend.get() or {}).get("usd", 0.0)
    if spent >= limit:
        raise _ModelAccessError(_model_access_problem(None, "error_max_budget_usd"))
    opts.max_budget_usd = limit - spent


def _select_model(prompt: str, config_model: str) -> str:
    """Select model for query execution.

    2026-11-06: every tier now defaults to MODEL_DEFAULT (Opus 5.5).
    2026-04-11: simplified to always route to Haiku-4.5 regardless of tier.
    Previous behavior routed complex multi-agent queries to Sonnet for
    stronger reasoning (change detection, multi-temporal, fusion), but:
      1. Haiku-4.5 handles these workloads well in practice
      2. Cost is ~10x lower on Haiku ($0.001/query vs ~$0.01)
      3. Latency is ~3x faster
      4. Tier classifier still runs so we keep the observability signal

    The tier classifier still emits a `jonaai.classify_query_tier` span
    for each query so we can see WHAT was classified as complex in Langfuse
    and revisit this decision if Haiku-4.5 proves insufficient for a
    specific category.

    2026-07-11 (C06 plumbing, NU-ratified): complex-tier queries MAY escalate
    to the model named in JONA_COMPLEX_MODEL. The shipped default leaves it
    unset, so behavior stays flat Haiku — this is opt-in plumbing, not a
    reversal of the 2026-04-11 policy. Escalate only once C05's eval harness
    can measure whether the extra cost buys accuracy.
    """
    tier = _classify_query_complexity(prompt)
    if tier == "complex":
        complex_model = os.environ.get("JONA_COMPLEX_MODEL", "").strip()
        if complex_model:
            return complex_model
    return MODEL_DEFAULT


def _worker_model_from_recommendation(rec: str) -> str:
    """Map the planner's model_recommendation to a worker model — opt-in.

    The recommendation is LLM output; it must never reach an API call
    unvalidated. Whitelist only, and only when JONA_HONOR_PLAN_MODEL is
    explicitly enabled; otherwise the 2026-04-11 flat-Haiku policy holds.
    """
    honor = os.environ.get("JONA_HONOR_PLAN_MODEL", "").lower() in {"1", "true", "yes", "on"}
    if not honor:
        return MODEL_DEFAULT
    return {"haiku": MODEL_HAIKU, "sonnet": MODEL_SONNET}.get(str(rec).strip().lower(), MODEL_DEFAULT)


class ClaudeSDKBridge:
    """Bridge between Claude Agent SDK and WebSocket connections.

    Manages per-session SDK clients. Each session keeps a ClaudeSDKClient alive
    for the WebSocket connection lifetime, enabling multi-turn conversation via
    client.query(). Hooks close over a mutable ws_ref dict so the websocket
    reference can be updated on reconnection.

    Sessions expire after _SDK_SESSION_TTL_SECONDS of inactivity. Cleanup runs
    on each new query. Maximum _SDK_SESSION_MAX_COUNT concurrent sessions; oldest
    evicted on overflow.
    """

    def __init__(self, config: OpenEOAIConfig):
        self.config = config
        # session_id → {"client", "ws_ref", "thinking_ids", "tools_used", "last_active", "sdk_session_id"}
        self._sessions: Dict[str, dict] = {}
        # In-process MCP servers shared across all sessions (stateless tools).
        # Main/worker sessions get the curated core server so
        # tool definitions stay under the CLI's auto-eager threshold and never
        # defer behind ToolSearch. JONA_FULL_TOOLSET=1 restores the full surface.
        if _full_toolset_enabled():
            self._mcp_server_config = create_openeo_mcp_server(config)
        else:
            self._mcp_server_config = create_core_mcp_server(config)
        self._planner_mcp_server_config = create_planner_mcp_server(config)
        # Specialist MCP servers for multi-agent architecture (Phase 3)
        self._specialist_mcp_configs = {}
        for name, cfg in SPECIALIST_CONFIGS.items():
            self._specialist_mcp_configs[name] = create_specialist_mcp_server(
                name, cfg["tool_names"], config
            )
        # SQLite session persistence for resume support
        self._session_manager = SessionManager(config.sqlite_path)
        # Clear stale SDK session IDs on startup — after a server restart,
        # SDK subprocess contexts no longer exist and resuming them causes
        # Claude to hallucinate results from stale conversation history.
        cleared = self._session_manager.clear_all_sdk_session_ids()
        if cleared:
            print(f"[SDK Bridge] Cleared {cleared} stale SDK session IDs on startup", file=sys.stderr)
        # Pending clarification futures for AskUserQuestion (session_id → Future)
        self._pending_clarifications: Dict[str, asyncio.Future] = {}
        # The id of each session's open question. An answer naming another id was meant
        # for a question that has closed, so it must not answer this one.
        self._pending_question_ids: Dict[str, str] = {}
        # Active project refs per session (mutable dicts shared with hook closures)
        self._project_refs: Dict[str, Dict[str, Any]] = {}
        # Authenticated user refs per session so tool hooks can owner-scope saved results.
        self._user_refs: Dict[str, Dict[str, Any]] = {}
        # The user's current question, for checks in hooks that outlive one turn.
        self._prompt_refs: Dict[str, Dict[str, Any]] = {}
        self._busy_sessions: set = set()
        self._client_costs: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        self._reaper_task: Optional[asyncio.Task] = None
        # Trajectory evaluation store (non-critical, disabled via DISABLE_TRAJECTORY_EVAL)
        self._trajectory_store = None
        if not os.environ.get("DISABLE_TRAJECTORY_EVAL"):
            try:
                from ..evaluation.trajectory_store import TrajectoryStore
                self._trajectory_store = TrajectoryStore(config.sqlite_path)
            except Exception as e:
                print(f"[SDK Bridge] Trajectory eval disabled: {e}", file=sys.stderr)

    async def _record_llm_budget(self, session_id: str, joint: str, model: str) -> None:
        """Observe-mode budget tag for one LLM call site. Fail-open — never raises."""
        try:
            user_id = (self._user_refs.get(session_id) or {}).get("value")
            await asyncio.to_thread(
                budget.record_llm_call, user_id, joint, model, session_id=session_id
            )
        except Exception:  # noqa: BLE001
            logger.debug("[budget] tag failed joint=%s", joint, exc_info=True)

    def _start_session_reaper(self) -> None:
        """End idle sessions on a timer, rather than only when the next query arrives."""
        if self._reaper_task is not None and not self._reaper_task.done():
            return

        async def _reap() -> None:
            while True:
                await asyncio.sleep(_SESSION_REAP_INTERVAL_SECONDS)
                try:
                    await self._cleanup_expired_sessions()
                except Exception as exc:  # noqa: BLE001
                    print(f"[SDK Bridge] Session reaper error: {exc}", file=sys.stderr)

        self._reaper_task = asyncio.create_task(_reap())

    # The turn's root span: _query reads its trace id for the `trace` event and the judge.
    @_lf_observe(name="jonaai.chat.query")
    async def query(self, prompt: str, websocket: Any, session_id: str = "default",
                    stop_event: Optional[asyncio.Event] = None,
                    active_project_id: Optional[str] = None,
                    auth_user_id: Optional[str] = None,
                    force_tier: Optional[str] = None) -> None:
        """Run one query within its limits, ending cleanly if the model can't be used."""
        self._start_session_reaper()
        if auth_user_id:
            # First writer wins: this only records an owner for a session that has none yet.
            try:
                await asyncio.to_thread(self._session_manager.ensure_session, session_id, auth_user_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[SDK Bridge] Could not record session owner: {exc!r}", file=sys.stderr)
        self._busy_sessions.add(session_id)
        self._prompt_refs.setdefault(session_id, {}).update(value=prompt, turn=uuid.uuid4().hex)
        spend_token = _query_spend.set({"usd": 0.0})
        try:
            problem = await self._refusal_before_start(prompt, auth_user_id)
            if problem:
                raise _ModelAccessError(problem)
            await self._query(
                prompt, websocket, session_id, stop_event, active_project_id, auth_user_id, force_tier
            )
        except _ModelAccessError as exc:
            print(f"[SDK Bridge] Query ended: {exc}", file=sys.stderr)
            await websocket.send_json({"type": "error", "content": str(exc)})
            await websocket.send_json({"type": "done"})
        finally:
            spent = (_query_spend.get() or {}).get("usd", 0.0)
            _query_spend.reset(spend_token)
            self._busy_sessions.discard(session_id)
            entry = self._sessions.get(session_id)
            if entry is not None:
                entry["last_active"] = time.monotonic()
                # A client's budget counts its whole life. Keep a warm process only while
                # nearly all of the limit is still available to the next question.
                _client = entry.get("client")
                _client_cost = self._client_costs.get(_client, {}).get("reported_cost", 0.0) if _client else 0.0
                if _client_cost >= _max_query_usd() * _CLIENT_REUSE_BUDGET_SHARE:
                    notes = _turn_notes(entry)
                    await self.disconnect_session(session_id)
                    # The caller reads the trace and final text after this returns.
                    self._sessions[session_id] = {**notes, "last_active": time.monotonic()}
            if spent > 0:
                await asyncio.to_thread(budget.record_llm_cost, auth_user_id, spent, session_id=session_id)

    async def _refusal_before_start(self, prompt: str, auth_user_id: Optional[str]) -> Optional[str]:
        """Return why a query shouldn't start, before it costs anything."""
        if len(prompt or "") > _MAX_PROMPT_CHARS:
            return (
                f"That message is too long for the model to read ({len(prompt):,} characters; the "
                f"limit is about {_MAX_PROMPT_CHARS:,}). Shorten it or split it into parts."
            )
        monthly = _positive_env("JONA_MONTHLY_USD", 0.0)
        if monthly:
            total = await asyncio.to_thread(budget.total_spend_this_month)
            if total >= monthly:
                return (
                    f"This service has reached its ${monthly:.2f} monthly limit for AI analysis. "
                    "It resets on the 1st, or an administrator can raise it."
                )
        limit = _positive_env("JONA_USER_DAILY_USD", 50.0)
        spent = await asyncio.to_thread(budget.user_spend_today, auth_user_id)
        if spent >= limit:
            return (
                f"You have reached today's ${limit:.2f} usage limit for AI analysis. "
                "It resets at midnight UTC."
            )
        return None

    async def _query(self, prompt: str, websocket: Any, session_id: str = "default",
                    stop_event: Optional[asyncio.Event] = None,
                    active_project_id: Optional[str] = None,
                    auth_user_id: Optional[str] = None,
                    force_tier: Optional[str] = None) -> None:
        """Send a query through the SDK and stream results to the WebSocket.

        Phase 16/A: wrapped in @observe as `jonaai.chat.query` — this is the
        root span for every user-facing chat turn. All Claude SDK LLM calls,
        tool invocations, specialist sub-agents, and the assistant message
        stream nest inside it. Matches the Phase A plan in docs/observability.md.

        Not tagged `as_type="generation"` — that's for individual LLM calls,
        which the Claude Agent SDK auto-emits as child `claude.assistant.turn`
        spans. The root `jonaai.chat.query` is an `agent`-style aggregate.
        """
        # Bind the authenticated user for this turn so in-process tool handlers
        # scope by tenant. The SDK dispatches handlers from a reader task frozen
        # at client.connect(), so we bind a MUTABLE per-session ref (not the raw
        # value) BEFORE any connect below and update its "value" each turn — the
        # reader's frozen context still points at this ref, so handlers read the
        # CURRENT turn's user even on a reused client. See user_context.py.
        _user_ref = self._user_refs.setdefault(session_id, {"value": auth_user_id})
        _user_ref["value"] = auth_user_id
        from .user_context import bind_user_ref
        bind_user_ref(_user_ref)

        # Phase A: set curated input on the live @observe span.
        # Runs INSIDE the decorated method so the span is active and the
        # update hits the real observation. Moving this from _run_query
        # eliminated the "No active span in current context" warnings
        # that `set_root_trace_name()` produced when it ran before/after
        # the span existed.
        #
        # Note: `set_current_trace_io` is deprecated in Langfuse 4.x —
        # trace-level input/output now comes from the root observation
        # (propagated automatically). We only call `update_current_span`.
        try:
            from ..langfuse_setup import get_langfuse_client, current_trace_id
            _lf_client = get_langfuse_client()
            _lf_tid = current_trace_id() if _lf_client else None
            if _lf_client is not None:
                _lf_client.update_current_span(
                    name="jonaai.chat.query",
                    input={
                        "prompt": prompt,
                        "session_id": session_id,
                        "project_id": active_project_id,
                    },
                )
            # Stash the live trace_id on the session so _run_query can read
            # it out after this method returns. Once bridge.query exits, the
            # @observe span closes and current_trace_id() returns None — the
            # async judge task and the `trace` WS event need the id to attach
            # scores and feedback to the right trace.
            if _lf_tid:
                self._sessions.setdefault(session_id, {})["last_trace_id"] = _lf_tid
        except Exception as _diag_exc:  # noqa: BLE001
            print(f"[SDK Bridge] query entry telemetry failed: {_diag_exc}", file=sys.stderr)

        # Cleanup expired sessions on each query (matches Anthropic path pattern)
        await self._cleanup_expired_sessions()

        # Per-turn artifact accumulators — cleared at the start of EVERY
        # query turn (unlike turn_tool_results, a per-session rolling window).
        # get(), not setdefault(): never create the entry here — downstream tier
        # routing keys off "entry is None" for new sessions.
        try:
            _acc_entry = self._sessions.get(session_id)
            if _acc_entry is not None:
                _acc_entry["turn_viz_types"] = []
                _acc_entry["turn_graph_processes"] = []
                _acc_entry["turn_period_difference"] = False
                _acc_entry["turn_tool_names"] = []
                _acc_entry["turn_response_texts"] = []
                _acc_entry["turn_data_notes"] = []
        except Exception:
            pass

        # Deterministic pre-model route: a decision-grade EO question goes straight to the
        # self-contained agentic pipeline (monolithic; it does not fit the decompose tool mesh,
        # so the model won't reliably choose it). Mirrors the Sentinel routing safety-net.
        if _is_decision_grade_query(prompt) and not forbids_execution(prompt):
            print(f"[SDK Bridge] Route=decision-grade for: {prompt[:80]}...", file=sys.stderr)
            await _emit_decision_report(websocket, prompt)
            return

        entry = self._sessions.get(session_id)
        # Hooks and complex runs can leave an entry holding only turn notes. With no agent
        # process behind it, this is a new session.
        if entry is not None and not entry.get("client"):
            entry = None

        # Route new-session queries through appropriate tier
        if (entry is None
                and not self._session_manager.get_sdk_session_id(session_id)):
            tier = _resolve_tier(prompt, force_tier)
            if len(prompt) > _PLANNER_MAX_PROMPT_CHARS and tier != "simple":
                print(f"[SDK Bridge] Skipping planning for a {len(prompt):,} character prompt", file=sys.stderr)
                tier = "simple"
            if tier == "complex":
                # Multi-agent: orchestrator + specialist delegation
                print(f"[SDK Bridge] Tier=complex (multi-agent) for: {prompt[:80]}...", file=sys.stderr)
                await self._multi_agent_query(
                    prompt, websocket, session_id, stop_event, active_project_id, auth_user_id
                )
                return
            elif tier == "moderate":
                # Planner + single worker: no specialist delegation
                print(f"[SDK Bridge] Tier=moderate (planner+worker) for: {prompt[:80]}...", file=sys.stderr)
                await self._planner_worker_query(
                    prompt, websocket, session_id, stop_event, active_project_id, auth_user_id
                )
                return
            # simple tier falls through to direct Haiku execution below

        # Start trajectory recording (non-critical)
        trajectory_id = None
        if self._trajectory_store:
            try:
                domain = classify_domain(prompt) or ""
                trajectory_id = self._trajectory_store.start_trajectory(
                    session_id=session_id, user_prompt=prompt, domain=domain,
                )
            except Exception:
                pass

        if entry is None:
            # New session — check SQLite for a resumable SDK session
            stored_sdk_id = self._session_manager.get_sdk_session_id(session_id)
            ws_ref = {"ws": websocket}
            thinking_ids: Dict[str, str] = {}
            session_tools_used: list = []
            session_tool_counts: Counter = Counter()
            options = self._build_options(
                ws_ref, thinking_ids, session_tools_used,
                session_id=session_id,
                resume_session_id=stored_sdk_id,
                tool_call_counts=session_tool_counts,
                active_project_id=active_project_id,
                auth_user_id=auth_user_id,
                prompt=prompt,
                trajectory_id=trajectory_id,
            )
            await self._record_llm_budget(session_id, "session", options.model)
            client = ClaudeSDKClient(options)
            self._sessions[session_id] = {
                **_turn_notes(self._sessions.get(session_id)),
                "client": client,
                "ws_ref": ws_ref,
                "thinking_ids": thinking_ids,
                "tools_used": session_tools_used,
                "last_active": time.monotonic(),
                "sdk_session_id": stored_sdk_id,
                "tool_call_counts": session_tool_counts,
            }
            # connect() initializes the subprocess; query() sends the prompt
            await self._connect_session_client(session_id, client)
            await client.query(prompt, session_id=session_id)
            if stored_sdk_id:
                print(f"[SDK Bridge] Resumed SDK session {stored_sdk_id[:12]}...", file=sys.stderr)
        else:
            # Existing session — update websocket reference, send new query
            client = entry["client"]
            entry["ws_ref"]["ws"] = websocket
            entry["tools_used"].clear()  # Reset for new query
            entry.setdefault("tool_call_counts", Counter()).clear()  # Reset rate limiter
            entry["last_active"] = time.monotonic()
            # Update active project for this query (shared mutable ref with hooks)
            if session_id in self._project_refs:
                self._project_refs[session_id]["value"] = active_project_id
            self._user_refs.setdefault(session_id, {"value": auth_user_id})["value"] = auth_user_id
            session_tools_used = entry["tools_used"]
            thinking_ids = entry["thinking_ids"]
            ws_ref = entry["ws_ref"]
            await client.query(prompt, session_id=session_id)

        # Stream response using shared streaming method
        await self._stream_response(
            client, websocket, session_id, ws_ref,
            thinking_ids, session_tools_used, stop_event, prompt,
            trajectory_id=trajectory_id,
        )

    @_lf_observe(name="jonaai.planner_worker_query")
    async def _run_planner_phase(
        self, prompt: str, websocket, session_id: str, label: str = "Planner",
        auth_user_id: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run the shared planner/orchestrator phase and return the extracted plan.

        Sends the thinking start/complete frames, runs the planner client with
        timeout protection, always disconnects it, and returns the plan JSON
        (None on failure/timeout). `label` only affects log lines — the
        planner/worker and orchestrator/specialist flows are otherwise identical.
        """
        _PLANNER_TIMEOUT_SECONDS = 600  # Max time for planner phase (CLI cold-start ~18s + tool calls)
        print(f"[SDK Bridge] {label} phase starting...", file=sys.stderr)

        planner_analyzing_id = str(uuid.uuid4())
        try:
            await websocket.send_json({
                "type": "thinking",
                "thinking_id": planner_analyzing_id,
                "thinking_type": "analyzing",
                "content": "Planning analysis approach..."
            })
        except Exception:
            pass

        # Build planner options (Haiku + discovery tools only)
        planner_mcp_servers = {"openeo_planner": self._planner_mcp_server_config}
        planner_allowed = [
            f"mcp__openeo_planner__{name}" for name in PLANNER_TOOL_NAMES
        ]

        # Phase C: fetch planner prompt from Langfuse with code fallback
        from .prompts import get_prompt as _lf_get_prompt
        _planner_prompt_text, _ = _lf_get_prompt(
            "jonaai/multi-agent/planner", fallback_attr="PLANNER_SYSTEM_PROMPT"
        )

        # plan-router: seed the planner with the user's own past successful plans
        # for similar queries. Query-specific → goes on the user message, not the
        # (Langfuse-hosted) system prompt. Entirely fail-open: any retrieval error
        # leaves planning untouched.
        _seed_block = ""
        try:
            from .plan_trajectories import (
                filter_valid, render_plan_seeds, retrieve_similar,
            )
            _raw_seeds = retrieve_similar(auth_user_id, prompt, k=3)
            _seeds = filter_valid(_raw_seeds)
            if _seeds:
                _seed_block = "\n\n" + render_plan_seeds(_seeds)
                print(f"[plan-router] injected {len(_seeds)} seed(s)", file=sys.stderr)
            elif _raw_seeds:
                # Retrieved matches but all referenced now-stale tool names —
                # observability so a silent no-op is distinguishable from "no match".
                print(f"[plan-router] {len(_raw_seeds)} seed(s) dropped (stale tool names)", file=sys.stderr)
        except Exception as _seed_exc:  # noqa: BLE001
            print(f"[plan-router] seed injection skipped: {_seed_exc}", file=sys.stderr)

        planner_opts = ClaudeAgentOptions(
            system_prompt=_planner_prompt_text,
            mcp_servers=planner_mcp_servers,
            allowed_tools=planner_allowed,
            disallowed_tools=list(GENERIC_BUILTIN_TOOLS_DENY),
            permission_mode="default",
            max_turns=8,
            model=MODEL_DEFAULT,
            include_partial_messages=True,
            extra_args=dict(_STRICT_MCP_ARGS),
        )
        _apply_reasoning(planner_opts)

        plan_text = ""
        plan_json = None
        planner_client = None

        try:
            async def _run_planner() -> str:
                """Run planner phase and return collected plan text."""
                nonlocal planner_client
                planner_cost: Dict[str, Any] = {}
                await self._record_llm_budget(session_id, "planner", planner_opts.model)
                planner_client = ClaudeSDKClient(planner_opts)
                await planner_client.connect()

                async def _collect_response() -> str:
                    text = ""
                    async for msg in planner_client.receive_response():
                        if isinstance(msg, AssistantMessage):
                            for block in getattr(msg, "content", []):
                                if isinstance(block, TextBlock):
                                    text += block.text
                        elif isinstance(msg, StreamEvent):
                            event = msg.event
                            if event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text += delta.get("text", "")
                        elif isinstance(msg, ResultMessage):
                            _record_spend(planner_cost, msg)
                            if msg.is_error:
                                print(f"[SDK Bridge] {label} error: {msg.result}", file=sys.stderr)
                                problem = _model_access_problem(msg.result, msg.subtype)
                                if problem:
                                    raise _ModelAccessError(problem)
                            break
                    return text

                await planner_client.query(
                    f"Create an execution plan for: {prompt}{_seed_block}"
                )
                collected = await _collect_response()
                if not self._extract_plan_json(collected):
                    # Running out of turns mid-discovery isn't a planning failure yet.
                    print(f"[SDK Bridge] {label} wrote no plan; requesting it now", file=sys.stderr)
                    await planner_client.query(
                        "Stop calling tools. Write your final JSON plan now, using what you "
                        "already know and stating any assumptions."
                    )
                    collected += await _collect_response()
                return collected

            async def _report_planning_time() -> None:
                started = time.monotonic()
                while True:
                    await asyncio.sleep(_PLANNING_PROGRESS_SECONDS)
                    minutes = int((time.monotonic() - started) // 60)
                    seconds = int((time.monotonic() - started) % 60)
                    try:
                        await websocket.send_json({
                            "type": "thinking",
                            "thinking_id": planner_analyzing_id,
                            "thinking_type": "analyzing",
                            "content": f"Still planning the analysis ({minutes} min {seconds:02d} s so far)...",
                        })
                    except Exception:
                        pass

            # Run planner with timeout protection, ending early if the user stops
            progress_task = asyncio.create_task(_report_planning_time())
            planner_task = asyncio.create_task(
                asyncio.wait_for(_run_planner(), timeout=_PLANNER_TIMEOUT_SECONDS)
            )
            stop_task = asyncio.create_task((stop_event or asyncio.Event()).wait())
            try:
                await asyncio.wait({planner_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
                if not planner_task.done():
                    print(f"[SDK Bridge] {label} stopped by the user", file=sys.stderr)
                    if planner_client is not None:
                        try:
                            await planner_client.interrupt()
                        except Exception:  # noqa: BLE001
                            pass
                    planner_task.cancel()
                    try:
                        await planner_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                else:
                    plan_text = planner_task.result()
            finally:
                progress_task.cancel()
                stop_task.cancel()

            # Extract JSON plan from planner response
            plan_json = self._extract_plan_json(plan_text)
            if not plan_json:
                print(
                    f"[SDK Bridge] {label} text had no usable plan: {len(plan_text)} chars, "
                    f"mentions steps={'steps' in plan_text}, fenced={'```' in plan_text}",
                    file=sys.stderr,
                )
            if plan_json:
                _flag_unknown_plan_args(plan_json)
                print(
                    f"[SDK Bridge] {label} produced {len(plan_json.get('steps', []))} steps",
                    file=sys.stderr,
                )

        except _ModelAccessError:
            raise
        except asyncio.TimeoutError:
            print(
                f"[SDK Bridge] {label} timed out after {_PLANNER_TIMEOUT_SECONDS}s, falling back",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[SDK Bridge] {label} failed: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        finally:
            # Always disconnect planner client to avoid leaked connections
            if planner_client is not None:
                try:
                    await planner_client.disconnect()
                except Exception:
                    pass

        # Complete planner thinking indicator
        try:
            await websocket.send_json({
                "type": "thinking",
                "thinking_id": planner_analyzing_id,
                "thinking_type": "analyzing",
                "content": "Planning analysis approach...",
                "thinking_completed": True,
            })
        except Exception:
            pass

        return plan_json

    async def _planner_worker_query(
        self,
        prompt: str,
        websocket: Any,
        session_id: str,
        stop_event: Optional[asyncio.Event] = None,
        active_project_id: Optional[str] = None,
        auth_user_id: Optional[str] = None,
    ) -> None:
        """Two-phase query: Planner creates execution plan, Worker executes it.

        Phase 1 (Planner): Sonnet + extended thinking analyzes the query,
            uses discovery tools to validate params, outputs a JSON execution plan.
        Phase 2 (Worker): Haiku with all 47 tools executes the plan.
            Results stream to WebSocket via normal hook flow.

        Falls back to single-agent flow on planner failure.
        """
        # plan-router: snapshot the user's result count so we can tell whether
        # this turn actually produced output (success gate for learning).
        _results_before = await asyncio.to_thread(self._user_result_count, auth_user_id)

        # --- Phase 1: Planner (shared implementation) ---
        plan_json = await self._run_planner_phase(prompt, websocket, session_id, label="Planner",
                                                  auth_user_id=auth_user_id, stop_event=stop_event)
        if stop_event and stop_event.is_set():
            return

        # --- Phase 2: Worker executes plan ---
        # Build worker prompt with the plan context
        if plan_json and plan_json.get("steps"):
            plan_summary = plan_json.get("summary", "Execute planned analysis")
            steps_desc = "\n".join(
                f"  {i+1}. {s.get('tool', '?')}: {s.get('purpose', '')}"
                for i, s in enumerate(plan_json["steps"])
            )
            worker_prompt = (
                f"Execute this analysis plan for the user's query: \"{prompt}\"\n\n"
                f"Plan summary: {plan_summary}\n"
                f"Steps:\n{steps_desc}\n\n"
                f"Full plan details: {json.dumps(plan_json)}\n\n"
                f"Execute each step using the available tools. "
                f"If a step fails, adapt and continue with the remaining steps. "
                f"After execution, provide a comprehensive summary of the results."
            )
            # 2026-04-11: unified on Haiku-4.5 for all workers. The planner's
            # model_recommendation is honored only behind JONA_HONOR_PLAN_MODEL
            # (whitelisted in _worker_model_from_recommendation) — kept in the
            # plan JSON for Langfuse observability either way.
            rec = plan_json.get("model_recommendation", "haiku")
            worker_model = _worker_model_from_recommendation(rec)
            print(f"[SDK Bridge] Worker phase: model={worker_model} (planner recommended: {rec})", file=sys.stderr)
        else:
            # Planner didn't produce a valid plan — fall back to direct execution
            # A valid plan with no steps means no tools are needed, not that planning failed.
            if plan_json is None:
                print("[SDK Bridge] Planner produced no plan, falling back to direct", file=sys.stderr)
                await self._notify_plan_fallback(websocket)
                print("[plan-router] plan_gate_miss tier=moderate (planner returned no plan)", file=sys.stderr)
            else:
                print("[SDK Bridge] Planner found no steps are needed, answering directly", file=sys.stderr)
            worker_prompt = prompt
            worker_model = MODEL_DEFAULT

        # Create worker session with all tools (reuses normal _build_options flow)
        ws_ref = {"ws": websocket}
        thinking_ids: Dict[str, str] = {}
        session_tools_used: list = []
        session_tool_counts: Counter = Counter()
        worker_opts = self._build_options(
            ws_ref, thinking_ids, session_tools_used,
            session_id=session_id,
            tool_call_counts=session_tool_counts,
            active_project_id=active_project_id,
            auth_user_id=auth_user_id,
            prompt=worker_prompt,
        )
        # Override model to use worker model (may be haiku or sonnet)
        worker_opts.model = worker_model
        _apply_reasoning(worker_opts)
        # More turns for plan execution
        worker_opts.max_turns = 25

        await self._record_llm_budget(session_id, "worker", worker_model)
        client = ClaudeSDKClient(worker_opts)
        self._sessions[session_id] = {
            **_turn_notes(self._sessions.get(session_id)),
            "client": client,
            "ws_ref": ws_ref,
            "thinking_ids": thinking_ids,
            "tools_used": session_tools_used,
            "last_active": time.monotonic(),
            "sdk_session_id": None,
            "tool_call_counts": session_tool_counts,
        }
        await self._connect_session_client(session_id, client)
        await client.query(worker_prompt, session_id=session_id)

        # Stream worker results using the normal streaming loop
        await self._stream_response(
            client, websocket, session_id, ws_ref,
            thinking_ids, session_tools_used, stop_event, prompt,
        )

        # plan-router: learn only if the turn actually produced a new result.
        _succeeded = await asyncio.to_thread(self._user_result_count, auth_user_id) > _results_before
        await asyncio.to_thread(self._save_plan_trajectory, auth_user_id, prompt, "moderate", plan_json, _succeeded)

    async def _data_results_detected(self, result: str, session_id: str, auth_user_id) -> bool:
        """Did the data specialist actually produce result files? Checked in
        order of reliability: the specialist's summary phrase, then real tool
        outputs retained this session (a tool that returned an output_path is
        ground truth regardless of how the model phrased its summary — a false
        abort here skips ml/qa and any earth-agent step), then the saved-jobs
        archive (sync DB, so off-loop)."""
        if "Available result files:" in result and result.count("result.tif") > 0:
            return True
        _tr = self._sessions.get(session_id, {}).get("turn_tool_results", [])
        if any("output_path" in t for t in _tr):
            return True
        try:
            from ..storage.job_archive import list_jobs
            recent = await asyncio.to_thread(list_jobs, limit=5, user_id=auth_user_id)
            return len(recent) > 0
        except Exception:  # noqa: BLE001
            return False

    def _user_result_count(self, auth_user_id) -> int:
        """How many saved results this user owns right now — used as a success
        signal: a turn that produced a durable result grows this count.
        Fail-safe to 0. Sync DB work — call via asyncio.to_thread from async
        paths: the first _get_session() of a process runs DDL + reflection
        against the remote pooler and can stall far past any "cheap COUNT"."""
        if not auth_user_id:
            return 0
        try:
            from sqlalchemy import func, select
            from ..storage import job_archive
            from ..storage.models import SavedResult
            session = job_archive._get_session()
            try:
                return session.execute(
                    select(func.count()).select_from(SavedResult)
                    .where(SavedResult.user_id == auth_user_id)
                ).scalar() or 0
            finally:
                session.close()
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    async def _notify_plan_fallback(websocket: Any) -> None:
        """Tell the user the query is running without the planned workflow."""
        try:
            await websocket.send_json({
                "type": "text",
                "content": (
                    "Planning did not finish, so this is running as a single agent "
                    "instead of the full planned workflow."
                ),
            })
        except Exception:
            pass

    def _save_plan_trajectory(self, auth_user_id, prompt, tier, plan_json, succeeded: bool) -> None:
        """Persist an executed plan as a reusable exemplar — ONLY on a genuine,
        non-trivial success (fail-open, non-critical)."""
        try:
            if not succeeded or not plan_json or len(plan_json.get("steps", [])) < 2:
                return
            from .plan_trajectories import save_trajectory
            if save_trajectory(auth_user_id, prompt, tier, plan_json, succeeded=succeeded):
                print(f"[plan-router] saved trajectory ({tier})", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass

    @_lf_observe(name="claude_sdk_bridge.multi_agent_query")
    async def _multi_agent_query(
        self,
        prompt: str,
        websocket: Any,
        session_id: str,
        stop_event: Optional[asyncio.Event] = None,
        active_project_id: Optional[str] = None,
        auth_user_id: Optional[str] = None,
    ) -> None:
        """Multi-agent query: Orchestrator routes to specialist agents.

        Phase 1 (Orchestrator): Sonnet + extended thinking analyzes the query,
            uses discovery tools to validate params, outputs a JSON plan with
            specialist routing info.
        Phase 2 (Specialists): Domain-specific agents execute their steps
            sequentially with accumulated context from previous specialists.

        Falls back to single-worker flow on orchestrator or routing failure.
        """
        # plan-router: snapshot result count for the success gate.
        _results_before = await asyncio.to_thread(self._user_result_count, auth_user_id)

        # --- Phase 1: Orchestrator (shared planner implementation) ---
        plan_json = await self._run_planner_phase(prompt, websocket, session_id, label="Orchestrator",
                                                  auth_user_id=auth_user_id, stop_event=stop_event)
        if stop_event and stop_event.is_set():
            return

        # --- Phase 2: Route to specialists or fall back ---
        if not plan_json or not plan_json.get("steps"):
            # No valid plan from the orchestrator. Durable-monitoring intent must
            # still reach the Sentinel specialist (it owns monitor_create_watch),
            # so synthesize a minimal Sentinel plan rather than the generic
            # single-worker fallback (which builds a one-off analysis, not a Watch).
            plan_json = plan_json or {}
            plan_json.setdefault("routing", [])
            plan_json.setdefault("steps", [])
            if _inject_sentinel_monitoring(plan_json["routing"], plan_json, prompt.lower()):
                print("[SDK Bridge] Orchestrator produced no plan; synthesized Sentinel monitoring plan", file=sys.stderr)
            else:
                print("[SDK Bridge] Orchestrator produced no plan, falling back to direct", file=sys.stderr)
                print("[plan-router] plan_gate_miss tier=complex (planner returned no plan)", file=sys.stderr)
                await self._notify_plan_fallback(websocket)
                await self._fallback_worker_query(
                    prompt, websocket, session_id, stop_event, active_project_id, auth_user_id
                )
                return

        routing = plan_json.get("routing", ["data"])  # Default to data specialist
        # Extract domain from orchestrator plan, fallback to keyword classification
        domain = plan_json.get("domain")
        if not domain:
            domain = classify_domain(prompt)
        if domain:
            print(f"[SDK Bridge] Domain classified: {domain}", file=sys.stderr)

        # Safety net: ensure ML specialist is included when query involves
        # spectral indices, change detection, temporal analysis, or classification.
        # The orchestrator sometimes omits ML, but these tasks need Earth-Agent.
        _ml_keywords = [
            "ndvi", "ndwi", "ndsi", "ndbi", "ndmi", "evi", "savi", "index",
            "change detection", "before and after", "temporal", "trend",
            "classify", "segment", "threshold", "earth-agent", "earth agent",
        ]
        _prompt_lower = prompt.lower()
        if "ml" not in routing and any(kw in _prompt_lower for kw in _ml_keywords):
            # Insert ML after data but before viz
            if "viz" in routing:
                viz_idx = routing.index("viz")
                routing.insert(viz_idx, "ml")
            else:
                routing.append("ml")
            print(f"[SDK Bridge] Auto-injected 'ml' specialist (query matches ML keywords)", file=sys.stderr)

        # Safety net: route to the Sentinel for durable-monitoring intent that
        # the orchestrator sometimes reads as one-off analysis. Keyword list at
        # module scope (_SENTINEL_ROUTING_KEYWORDS) uses UNAMBIGUOUS monitoring
        # phrases only (not bare "watch", which appears in one-off "watch the
        # NDVI change") to avoid over-routing.
        if _inject_sentinel_monitoring(routing, plan_json, _prompt_lower):
            print("[SDK Bridge] Auto-injected 'sentinel' specialist + watch step (monitoring keywords)", file=sys.stderr)

        # Safety net: a two-period change query must be planned as ONE
        # difference-raster job, never two snapshots + prose subtraction.
        # Fail-open: a bug parsing a malformed plan must not kill the turn.
        try:
            if _inject_change_detection(routing, plan_json, _prompt_lower):
                print("[SDK Bridge] Auto-injected change-detection difference-raster step (two-period change query)", file=sys.stderr)
        except Exception as _inj_e:
            print(f"[SDK Bridge] _inject_change_detection skipped: {_inj_e}", file=sys.stderr)

        # Safety net: qa validates files earlier specialists produce, so it
        # must run after them regardless of the order the planner returned.
        reordered = _qa_last(routing)
        if reordered != routing:
            print("[SDK Bridge] Reordered routing: 'qa' moved after producing specialists", file=sys.stderr)
            routing = reordered

        print(f"[SDK Bridge] Routing to specialists: {routing}", file=sys.stderr)

        # Phase 3: Execute specialists sequentially (context accumulates)
        accumulated_context: Dict[str, str] = {}
        _data_produced_results = False  # Track if data specialist produced any files
        for specialist_name in routing:
            if stop_event and stop_event.is_set():
                print("[SDK Bridge] Stopped; not starting further specialists", file=sys.stderr)
                break
            if specialist_name not in SPECIALIST_CONFIGS:
                print(f"[SDK Bridge] Unknown specialist '{specialist_name}', skipping", file=sys.stderr)
                continue

            # Filter plan steps for this specialist
            specialist_steps = [
                s for s in plan_json.get("steps", [])
                if s.get("specialist") == specialist_name
            ]
            if not specialist_steps:
                print(f"[SDK Bridge] No steps for specialist '{specialist_name}', skipping", file=sys.stderr)
                continue

            # Build specialist prompt with accumulated context and domain knowledge
            specialist_prompt = self._build_specialist_prompt(
                specialist_name, specialist_steps, prompt, accumulated_context,
                domain=domain,
            )

            print(
                f"[SDK Bridge] Running specialist: {specialist_name} "
                f"({len(SPECIALIST_CONFIGS[specialist_name]['tool_names'])} tools, "
                f"{len(specialist_steps)} steps)",
                file=sys.stderr,
            )

            # Record the actual delegation so the persona A/B harness (and frontend)
            # can attribute lift to a persona that genuinely fired, not one that never ran.
            try:
                await websocket.send_json({"type": "specialist_delegated", "specialist": specialist_name})
            except Exception:
                pass

            # Run specialist with domain-specific MCP server
            result = await self._run_specialist(
                specialist_name, specialist_prompt, websocket, session_id,
                stop_event, active_project_id, auth_user_id,
            )
            accumulated_context[specialist_name] = result

            # After data specialist: check if any result files were produced.
            # If all jobs failed, abort downstream specialists to save API costs.
            if specialist_name == "data":
                _data_produced_results = await self._data_results_detected(
                    result, session_id, auth_user_id
                )

                if not _data_produced_results:
                    print(
                        f"[SDK Bridge] DATA SPECIALIST PRODUCED NO RESULTS — "
                        f"aborting downstream specialists to save API costs",
                        file=sys.stderr,
                    )
                    try:
                        await websocket.send_json({
                            "type": "text",
                            "content": _data_abort_message(
                                self._sessions.get(session_id, {}).get("turn_tool_results", [])
                            ),
                        })
                    except Exception:
                        pass
                    break  # Exit the specialist loop

        # plan-router: learn only if the run actually produced a new result —
        # covers empty routing, zero executed specialists, and downstream
        # failures, not just the data-specialist abort above.
        _succeeded = await asyncio.to_thread(self._user_result_count, auth_user_id) > _results_before
        await asyncio.to_thread(self._save_plan_trajectory, auth_user_id, prompt, "complex", plan_json, _succeeded)

        # The complex tier is the Palisades path — its turn-final done
        # lives HERE, not in _stream_response (specialists stream send_done=False,
        # so the contracts never ran for this tier). Run them on the accumulated
        # turn state so a change question answered without a difference raster,
        # or a requested-but-missing map, is caught. Fail-open.
        # A stopped turn is incomplete on purpose; the checks would only flag it.
        if not (stop_event and stop_event.is_set()):
            try:
                _sess_fin = self._sessions.get(session_id, {})
                # The WHOLE turn's prose: last_response_text only holds the last
                # specialist's, so numbers from an earlier one escaped the notice.
                # Blank line between specialists: a single "\n" would let one specialist's
                # trailing table run into the next one's header and read as ONE table.
                _turn_prose = "\n\n".join(_sess_fin.get("turn_response_texts", []) or [])
                await self._emit_completion_contracts(
                    websocket, session_id, prompt,
                    _sess_fin.get("turn_tool_names", []),
                    _turn_prose or _sess_fin.get("last_response_text", ""),
                )
            except Exception:
                pass

        # The single turn-final done for the complex tier — specialist streams
        # suppress theirs (send_done=False); the no-plan fallback path returned
        # earlier and its worker stream already sent one.
        try:
            await websocket.send_json({"type": "done"})
        except Exception:
            pass

    @_lf_observe(name="jonaai.specialist")
    async def _run_specialist(
        self,
        name: str,
        prompt: str,
        websocket: Any,
        session_id: str,
        stop_event: Optional[asyncio.Event] = None,
        active_project_id: Optional[str] = None,
        auth_user_id: Optional[str] = None,
    ) -> str:
        """Run a single specialist agent and stream results.

        Returns:
            Collected text output from the specialist for context passing.

        Phase 16/A: wrapped in @observe so each specialist call is a
        distinct span. The specialist name (data/viz/ml/qa) is attached
        as the span name suffix via update_current_span below, so the
        Langfuse trace tree shows specialist.data → specialist.viz etc.
        """
        # Phase 16/A: rename the span to include the specialist kind so
        # the trace tree shows specialist.data vs specialist.viz etc.
        try:
            from ..langfuse_setup import get_langfuse_client
            _lf_client = get_langfuse_client()
            if _lf_client is not None:
                _lf_client.update_current_span(
                    name=f"jonaai.specialist.{name}",
                    input={"specialist": name, "prompt_preview": prompt[:300], "prompt_len": len(prompt)},
                    metadata={"specialist_kind": name, "session_id": session_id},
                )
        except Exception:
            pass

        cfg = SPECIALIST_CONFIGS[name]
        mcp_config = self._specialist_mcp_configs[name]
        mcp_name = cfg["mcp_name"]

        mcp_servers = {mcp_name: mcp_config}
        allowed_tools = [f"mcp__{mcp_name}__{t}" for t in cfg["tool_names"]]

        ws_ref = {"ws": websocket}
        thinking_ids: Dict[str, str] = {}
        session_tools_used: list = []
        session_tool_counts: Counter = Counter()

        # Build options with hooks for WebSocket streaming (reuses _build_options)
        specialist_opts = self._build_options(
            ws_ref, thinking_ids, session_tools_used,
            session_id=session_id,
            tool_call_counts=session_tool_counts,
            active_project_id=active_project_id,
            auth_user_id=auth_user_id,
            prompt=prompt,
        )
        # Phase C: fetch specialist prompt from Langfuse with code fallback.
        # Uses the same name→attr mapping from PROMPT_REGISTRY so the
        # migration script and runtime stay in sync.
        from .prompts import get_prompt as _lf_get_prompt
        _specialist_prompt_name_map = {
            "data": ("jonaai/multi-agent/data-specialist", "DATA_AGENT_SYSTEM_PROMPT"),
            "viz": ("jonaai/multi-agent/viz-specialist", "VIZ_AGENT_SYSTEM_PROMPT"),
            "ml": ("jonaai/multi-agent/ml-specialist", "ML_AGENT_SYSTEM_PROMPT"),
            "qa": ("jonaai/multi-agent/qa-specialist", "QA_AGENT_SYSTEM_PROMPT"),
        }
        _lf_name, _lf_attr = _specialist_prompt_name_map.get(
            name, (None, None)
        )
        if _lf_name is not None:
            _spec_prompt_text, _ = _lf_get_prompt(_lf_name, fallback_attr=_lf_attr)
        else:
            _spec_prompt_text = cfg["system_prompt"]

        # Override with specialist-specific configuration
        specialist_opts.system_prompt = _spec_prompt_text
        specialist_opts.mcp_servers = mcp_servers
        specialist_opts.allowed_tools = allowed_tools
        specialist_opts.model = MODEL_DEFAULT
        _apply_reasoning(specialist_opts)
        specialist_opts.max_turns = 15

        await self._record_llm_budget(session_id, "specialist", specialist_opts.model)
        client = ClaudeSDKClient(specialist_opts)
        # Store specialist session (keyed with specialist suffix to avoid collision)
        specialist_session_id = f"{session_id}_{name}"
        # It's running even though the idle timer only ever sees it created.
        self._busy_sessions.add(specialist_session_id)
        self._sessions[specialist_session_id] = {
            "client": client,
            "ws_ref": ws_ref,
            "thinking_ids": thinking_ids,
            "tools_used": session_tools_used,
            "last_active": time.monotonic(),
            "sdk_session_id": None,
            "tool_call_counts": session_tool_counts,
        }

        try:
            await client.connect()
            await client.query(prompt, session_id=specialist_session_id)

            # Stream specialist results to WebSocket. send_done=False: this is
            # a mid-turn stream — the turn-final done belongs to _multi_agent_query.
            await self._stream_response(
                client, websocket, session_id, ws_ref,
                thinking_ids, session_tools_used, stop_event, prompt,
                send_done=False,
            )
        finally:
            # Cleanup specialist session — always runs even if stream errors
            self._busy_sessions.discard(specialist_session_id)
            entry = self._sessions.pop(specialist_session_id, None)
            if entry:
                try:
                    await entry["client"].disconnect()
                except Exception:
                    pass

        # Build rich context for the next specialist: include tool calls + result file paths.
        # This is critical for ML/QA specialists that need file paths from the data specialist.
        context_parts = [f"Specialist '{name}' completed {len(session_tools_used)} tool calls."]
        context_parts.append(f"Tools used: {', '.join(session_tools_used)}")

        # Collect result file paths from saved jobs created during this session
        try:
            from ..storage.job_archive import list_jobs
            recent_jobs = list_jobs(limit=20, user_id=auth_user_id)
            if recent_jobs:
                result_paths = []
                for job in recent_jobs:
                    rp = job.get("result_path", "")
                    title = job.get("title", "Untitled")
                    if rp:
                        result_paths.append(f"  - {title}: {rp}")
                if result_paths:
                    context_parts.append("Available result files:\n" + "\n".join(result_paths))
        except Exception:
            pass

        # (flagged off by default): the metadata above omits the specialist's actual
        # findings. When enabled, append its bounded final prose so orchestrator synthesis is
        # coherent. Read from the session where _stream_response stored it this turn.
        _prose = (self._sessions.get(session_id, {}) or {}).get("last_response_text", "")
        _line = _maybe_prose_line(name, _prose, _prose_handoff_enabled())
        if _line:
            context_parts.append(_line)

        return "\n".join(context_parts)

    @staticmethod
    def _build_specialist_prompt(
        specialist_name: str,
        steps: list,
        original_prompt: str,
        accumulated_context: Dict[str, str],
        domain: Optional[str] = None,
    ) -> str:
        """Build a specialist's execution prompt from plan steps + prior context.

        Args:
            specialist_name: Which specialist is executing (data/viz/ml/qa).
            steps: Plan steps assigned to this specialist.
            original_prompt: The user's original query.
            accumulated_context: Results from previously executed specialists.
            domain: Optional EO domain (fire, vegetation, etc.) for context injection.
        """
        steps_desc = "\n".join(
            f"  {i+1}. {s.get('tool', '?')}: {s.get('purpose', '')}"
            for i, s in enumerate(steps)
        )

        context_block = ""
        if accumulated_context:
            context_parts = [
                f"  - {k}: {v}" for k, v in accumulated_context.items()
            ]
            context_block = (
                "\n\nContext from previous specialists:\n"
                + "\n".join(context_parts)
            )

        # Inject domain-specific knowledge when available
        domain_block = ""
        if domain:
            domain_ctx = get_domain_context(domain)
            if domain_ctx:
                domain_block = f"\n\nDomain expertise ({domain}):\n  {domain_ctx.replace(chr(10), chr(10) + '  ')}"

        return (
            f"Execute these analysis steps for the user's query: \"{original_prompt}\"\n\n"
            f"Your assigned steps:\n{steps_desc}\n\n"
            f"Full step details: {json.dumps(steps)}\n"
            f"{context_block}"
            f"{domain_block}\n\n"
            f"Execute each step using the available tools. "
            f"If a step fails, adapt and continue with the remaining steps. "
            f"After execution, provide a summary of results."
        )

    async def _fallback_worker_query(
        self,
        prompt: str,
        websocket: Any,
        session_id: str,
        stop_event: Optional[asyncio.Event] = None,
        active_project_id: Optional[str] = None,
        auth_user_id: Optional[str] = None,
    ) -> None:
        """Fallback: run a single worker with all tools (same as Phase 2 worker)."""
        ws_ref = {"ws": websocket}
        thinking_ids: Dict[str, str] = {}
        session_tools_used: list = []
        session_tool_counts: Counter = Counter()
        worker_opts = self._build_options(
            ws_ref, thinking_ids, session_tools_used,
            session_id=session_id,
            tool_call_counts=session_tool_counts,
            active_project_id=active_project_id,
            auth_user_id=auth_user_id,
            prompt=prompt,
        )
        worker_opts.model = MODEL_DEFAULT
        _apply_reasoning(worker_opts)
        worker_opts.max_turns = 25

        await self._record_llm_budget(session_id, "worker-fallback", worker_opts.model)
        client = ClaudeSDKClient(worker_opts)
        self._sessions[session_id] = {
            **_turn_notes(self._sessions.get(session_id)),
            "client": client,
            "ws_ref": ws_ref,
            "thinking_ids": thinking_ids,
            "tools_used": session_tools_used,
            "last_active": time.monotonic(),
            "sdk_session_id": None,
            "tool_call_counts": session_tool_counts,
        }
        await self._connect_session_client(session_id, client)
        await client.query(prompt, session_id=session_id)

        await self._stream_response(
            client, websocket, session_id, ws_ref,
            thinking_ids, session_tools_used, stop_event, prompt,
        )

    @staticmethod
    def _extract_plan_json(text: str) -> Optional[dict]:
        """Extract JSON plan from planner response text.

        Looks for JSON in code fences (```json ... ```) or raw JSON blocks.
        """
        import re

        # Try code-fenced JSON first
        fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if fence_match:
            try:
                plan = json.loads(fence_match.group(1))
                if isinstance(plan, dict) and "steps" in plan:
                    return plan
            except json.JSONDecodeError:
                pass

        # Try finding raw JSON object with "steps" key
        brace_start = text.find('{"steps"')
        if brace_start == -1:
            brace_start = text.find('"steps"')
            if brace_start != -1:
                # Walk back to find opening brace
                brace_start = text.rfind('{', 0, brace_start)

        if brace_start != -1:
            # Find matching closing brace
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            plan = json.loads(text[brace_start:i + 1])
                            if isinstance(plan, dict) and "steps" in plan:
                                return plan
                        except json.JSONDecodeError:
                            break

        return None

    async def _stream_response(
        self,
        client: ClaudeSDKClient,
        websocket: Any,
        session_id: str,
        ws_ref: Dict[str, Any],
        thinking_ids: Dict[str, str],
        tools_used: list,
        stop_event: Optional[asyncio.Event],
        prompt: str,
        trajectory_id: Optional[str] = None,
        send_done: bool = True,
    ) -> None:
        """Stream SDK responses to WebSocket. Shared between direct and planner/worker flows.

        send_done: turn-final streams only. Specialist streams pass False —
        clients treat the first {"type": "done"} as end-of-turn, so a done from
        the data specialist ends the turn before ml/qa ever run.
        """
        _query_start_time = time.time()
        text_streaming = False
        text_buffer = ""
        # Phase F: accumulate the full assistant response text for the LLM-as-judge.
        # This is separate from text_buffer (which is flushed to the WS every 50ms).
        full_response_text = ""
        last_flush = time.monotonic()
        current_block_type = None
        first_turn = True
        analyzing_id = str(uuid.uuid4())
        stopped = False

        try:
            if first_turn:
                await websocket.send_json({
                    "type": "thinking",
                    "thinking_id": analyzing_id,
                    "thinking_type": "analyzing",
                    "content": "Understanding your request..."
                })

            async for msg in client.receive_response():
                if not stopped and stop_event and stop_event.is_set():
                    try:
                        await client.interrupt()
                    except Exception:
                        pass
                    if text_streaming:
                        if text_buffer:
                            await websocket.send_json({"type": "text_delta", "content": text_buffer})
                            text_buffer = ""
                        await websocket.send_json({"type": "text_stream_end"})
                        text_streaming = False
                    stopped = True
                    continue

                if stopped:
                    if isinstance(msg, ResultMessage):
                        _record_spend(self._client_costs.setdefault(client, {}), msg)
                        # Stopped and SDK finished — send done and exit
                        if send_done:
                            try:
                                await websocket.send_json({"type": "done"})
                            except Exception:
                                pass
                        break
                    continue

                if isinstance(msg, SystemMessage):
                    continue

                elif isinstance(msg, StreamEvent):
                    event = msg.event
                    event_type = event.get("type", "")

                    if event_type == "content_block_start":
                        block = event.get("content_block", {})
                        block_type = block.get("type", "")

                        if block_type == "text":
                            current_block_type = "text"
                            if first_turn:
                                first_turn = False
                                await websocket.send_json({
                                    "type": "thinking",
                                    "thinking_id": analyzing_id,
                                    "thinking_type": "analyzing",
                                    "content": "Understanding your request...",
                                    "thinking_completed": True
                                })
                            if not text_streaming:
                                await websocket.send_json({"type": "text_stream_start"})
                                text_streaming = True

                        elif block_type == "tool_use":
                            current_block_type = "tool_use"
                            if text_streaming:
                                if text_buffer:
                                    await websocket.send_json({"type": "text_delta", "content": text_buffer})
                                    text_buffer = ""
                                await websocket.send_json({"type": "text_stream_end"})
                                text_streaming = False

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        delta_type = delta.get("type", "")

                        if delta_type == "text_delta" and current_block_type == "text":
                            _delta_text = delta.get("text", "")
                            text_buffer += _delta_text
                            full_response_text += _delta_text  # Phase F: accumulate for judge
                            now = time.monotonic()
                            if now - last_flush >= 0.05:
                                if text_buffer:
                                    await websocket.send_json({"type": "text_delta", "content": text_buffer})
                                    text_buffer = ""
                                last_flush = now

                    elif event_type == "content_block_stop":
                        if current_block_type == "text":
                            if text_buffer:
                                await websocket.send_json({"type": "text_delta", "content": text_buffer})
                                text_buffer = ""
                        current_block_type = None

                    elif event_type == "message_stop":
                        if text_streaming:
                            if text_buffer:
                                await websocket.send_json({"type": "text_delta", "content": text_buffer})
                                text_buffer = ""
                            await websocket.send_json({"type": "text_stream_end"})
                            text_streaming = False

                elif isinstance(msg, AssistantMessage):
                    if text_streaming:
                        if text_buffer:
                            await websocket.send_json({"type": "text_delta", "content": text_buffer})
                            text_buffer = ""
                        await websocket.send_json({"type": "text_stream_end"})
                        text_streaming = False

                elif isinstance(msg, ResultMessage):
                    sdk_sid = getattr(msg, "session_id", None)
                    entry = self._sessions.get(session_id)
                    # Only the session's own agent becomes its resume point. Planner and
                    # specialist clients stream under the same session id; resuming one of
                    # theirs would continue the wrong conversation.
                    if sdk_sid and entry is not None and entry.get("client") is client:
                        entry["sdk_session_id"] = sdk_sid
                        try:
                            _owner = (self._user_refs.get(session_id) or {}).get("value")
                            self._session_manager.ensure_session(session_id, _owner or "default")
                            self._session_manager.set_sdk_session_id(session_id, sdk_sid)
                        except Exception as e:
                            print(f"[SDK Bridge] Failed to persist SDK session: {e}", file=sys.stderr)

                    # --- Token counting / cost monitoring ---
                    _record_spend(self._client_costs.setdefault(client, {}), msg)
                    usage = getattr(msg, "usage", None) or {}
                    cost_usd = getattr(msg, "total_cost_usd", None)
                    num_turns = getattr(msg, "num_turns", 0)
                    duration_ms = getattr(msg, "duration_ms", 0)
                    if usage or cost_usd is not None:
                        _audit_log.info(
                            "USAGE session=%s turns=%d duration_ms=%d "
                            "input_tokens=%s output_tokens=%s cost_usd=%s",
                            session_id, num_turns, duration_ms,
                            usage.get("input_tokens", "?"),
                            usage.get("output_tokens", "?"),
                            f"${cost_usd:.6f}" if cost_usd is not None else "?",
                        )
                        # Send usage metrics to frontend for display
                        try:
                            await websocket.send_json({
                                "type": "usage_metrics",
                                "usage": {
                                    "input_tokens": usage.get("input_tokens"),
                                    "output_tokens": usage.get("output_tokens"),
                                    "total_cost_usd": cost_usd,
                                    "num_turns": num_turns,
                                    "duration_ms": duration_ms,
                                },
                            })
                        except Exception:
                            pass

                    if text_streaming:
                        if text_buffer:
                            await websocket.send_json({"type": "text_delta", "content": text_buffer})
                            text_buffer = ""
                        await websocket.send_json({"type": "text_stream_end"})
                        text_streaming = False

                    if first_turn:
                        first_turn = False
                        await websocket.send_json({
                            "type": "thinking",
                            "thinking_id": analyzing_id,
                            "thinking_type": "analyzing",
                            "content": "Understanding your request...",
                            "thinking_completed": True
                        })

                    # Finish trajectory recording (non-critical)
                    if trajectory_id and self._trajectory_store:
                        try:
                            success = not msg.is_error
                            self._trajectory_store.finish_trajectory(
                                trajectory_id, success=success,
                            )
                            # Capture successful trajectories as learned patterns
                            if success and not os.environ.get("DISABLE_LEARNED_PATTERNS"):
                                try:
                                    traj = self._trajectory_store.get_trajectory(trajectory_id)
                                    if traj and len(traj.tool_calls) >= 3:
                                        from ..batch.template_store import TemplateStore
                                        t_store = TemplateStore(
                                            getattr(self.config, "sqlite_path", "data/openeo_ai.db")
                                        )
                                        t_store.capture_trajectory(
                                            tool_sequence=[tc.tool_name for tc in traj.tool_calls],
                                            source_query=traj.user_prompt,
                                            domain=traj.domain,
                                            outcome_score=1.0,  # Successful completion
                                        )
                                except Exception:
                                    pass  # Non-critical
                        except Exception:
                            pass

                    if msg.is_error:
                        _problem = _model_access_problem(msg.result, getattr(msg, "subtype", None))
                        if _problem:
                            raise _ModelAccessError(_problem)
                        await websocket.send_json({
                            "type": "error",
                            "content": msg.result or "An error occurred"
                        })
                        if send_done:
                            await websocket.send_json({"type": "done"})
                    else:
                        suggestions = _generate_suggestions(tools_used, prompt)
                        if suggestions:
                            await websocket.send_json({
                                "type": "suggestions",
                                "suggestions": suggestions,
                            })
                        # Emit sustainability metrics BEFORE done so frontend
                        # receives them while still in "processing" state.
                        try:
                            _query_elapsed = time.time() - _query_start_time
                            _energy_kwh = _query_elapsed * 0.0001
                            _carbon_kg = _energy_kwh * 0.4
                            await websocket.send_json({
                                "type": "sustainability_metrics",
                                "metrics": {
                                    "carbonFootprint": round(_carbon_kg, 6),
                                    "dataTransferred": 0,  # TODO: track actual bytes
                                    "computeTime": round(_query_elapsed, 1),
                                    "energyUsed": round(_energy_kwh, 6),
                                }
                            })
                        except Exception:
                            pass  # Non-critical

                        # Phase F: store the full assistant text on the session
                        # so _run_query can read it out and fire the LLM-as-judge.
                        try:
                            _entry = self._sessions.setdefault(session_id, {})
                            _entry["last_response_text"] = full_response_text
                            _entry["last_query_text"] = prompt
                            # Last_response_text is OVERWRITTEN by every
                            # specialist stream, so the complex tier's turn-final
                            # contracts only ever saw the last specialist's prose.
                            # Keep the whole turn's prose too.
                            if full_response_text:
                                _rt = _entry.setdefault("turn_response_texts", [])
                                _rt.append(full_response_text)
                                del _rt[:-40]  # bound memory — mirrors turn_tool_results
                        except Exception:
                            pass

                        # Observe-mode grounding — flag trust-claim numbers in the final
                        # text that no tool result this turn supports. Telemetry never breaks flow.
                        try:
                            # Numbers the user gave are grounded too: quoting them isn't a claim.
                            _tr = self._sessions.get(session_id, {}).get("turn_tool_results", [])
                            _gp = _grounding_payload(full_response_text, [*_tr, prompt or ""])
                            from openeo_ai.langfuse_setup import emit_score as _emit_score
                            _emit_score(name="implicit-ungrounded-numbers",
                                        value=1.0 if _gp else 0.0, data_type="BOOLEAN")
                            if _gp:
                                await websocket.send_json(_gp)
                        except Exception:
                            pass

                        if send_done:
                            # Completion contracts + enforcement run only on
                            # turn-final streams — specialist streams (send_done=False)
                            # end mid-turn, before later specialists produce their
                            # graphs/visualizations, and would fire false gaps.
                            try:
                                await self._emit_completion_contracts(
                                    websocket, session_id, prompt,
                                    tools_used, full_response_text,
                                )
                            except Exception:
                                pass
                            await websocket.send_json({"type": "done"})
                    break

        except _ModelAccessError:
            if text_streaming:
                try:
                    await websocket.send_json({"type": "text_stream_end"})
                except Exception:
                    pass
            raise
        except Exception as e:
            print(f"[SDK Bridge] Stream error: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            if text_streaming:
                try:
                    await websocket.send_json({"type": "text_stream_end"})
                except Exception:
                    pass
            # Sanitize error — don't leak internal details to frontend
            try:
                import uuid as _uuid
                _ref = str(_uuid.uuid4())[:8]
                print(f"[SDK Bridge] Stream error ref={_ref}: {e}", file=sys.stderr)
                await websocket.send_json({
                    "type": "error",
                    "content": f"An internal error occurred (ref: {_ref}). Please try again."
                })
            except Exception:
                pass
            # Always send done so frontend exits "processing" state
            # (unless this is a specialist stream — the turn continues)
            if send_done:
                try:
                    await websocket.send_json({"type": "done"})
                except Exception:
                    pass

    async def _emit_completion_contracts(
        self,
        websocket: Any,
        session_id: str,
        prompt: str,
        tools_used: list,
        full_response_text: str,
    ) -> None:
        """Turn-final completion contracts — method gap (change question
        answered without a difference raster), deliverable gap (map requested,
        none produced), class-table gap (per-class percentages that do not sum to
        100%), and the JONA_GROUNDING_ENFORCE notice covering
        grounding/method/deliverable/class-table findings uniformly. Each check is
        independent and fail-open; this never raises into the stream loop."""
        _sess = self._sessions.get(session_id, {})

        _mgp = None
        try:
            _mgp = _method_gap_payload(
                prompt, _sess.get("turn_graph_processes", []), tools_used,
                _sess.get("turn_period_difference", False),
            )
            from openeo_ai.langfuse_setup import emit_score as _emit_score
            _emit_score(name="method-gap", value=1.0 if _mgp else 0.0,
                        data_type="BOOLEAN")
            if _mgp:
                await websocket.send_json(_mgp)
        except Exception:
            pass

        _dgp = None
        try:
            _dgp = _deliverable_gap_payload(
                prompt, _sess.get("turn_viz_types", []), tools_used
            )
            from openeo_ai.langfuse_setup import emit_score as _emit_score
            _emit_score(name="deliverable-gap", value=1.0 if _dgp else 0.0,
                        data_type="BOOLEAN")
            if _dgp:
                await websocket.send_json(_dgp)
        except Exception:
            pass

        _ctp = None
        try:
            _ctp = _class_table_payload(
                _sess.get("turn_response_texts") or full_response_text
            )
            from openeo_ai.langfuse_setup import emit_score as _emit_score
            _emit_score(name="class-table-gap", value=1.0 if _ctp else 0.0,
                        data_type="BOOLEAN")
            if _ctp:
                await websocket.send_json(_ctp)
        except Exception:
            pass

        try:
            _notes = _sess.get("turn_data_notes") or []
            if _notes:
                _text = "\n\n**Data coverage:** " + ". ".join(_notes) + ".\n"
                await websocket.send_json({"type": "text_stream_start"})
                await websocket.send_json({"type": "text_delta", "content": _text})
                await websocket.send_json({"type": "text_stream_end"})
        except Exception:
            pass

        # Enforcement: annotate (default) streams one short notice chunk before
        # done; block uses stronger retraction wording. Text already streamed
        # cannot be unsent — even "block" can only append a retraction notice,
        # it cannot rewrite what already reached the client.
        try:
            _g_count = 0
            try:
                _gp = _grounding_payload(
                    full_response_text, [*_sess.get("turn_tool_results", []), prompt or ""]
                )
                _g_count = len(_gp["violations"]) if _gp else 0
            except Exception:
                pass
            _notice = _enforcement_notice(
                _grounding_enforce_mode(), _g_count,
                _mgp is not None, _dgp is not None,
                _ctp is not None and _class_table_notice_enabled(),
            )
            if _notice:
                await websocket.send_json({"type": "text_stream_start"})
                await websocket.send_json({"type": "text_delta", "content": _notice})
                await websocket.send_json({"type": "text_stream_end"})
        except Exception:
            pass

    def _build_options(
        self,
        ws_ref: Dict[str, Any],
        thinking_ids: Dict[str, str],
        session_tools_used: list,
        session_id: str = "default",
        resume_session_id: Optional[str] = None,
        tool_call_counts: Optional[Counter] = None,
        active_project_id: Optional[str] = None,
        auth_user_id: Optional[str] = None,
        prompt: str = "",
        trajectory_id: Optional[str] = None,
    ) -> ClaudeAgentOptions:
        """Build SDK options with hooks that send messages to the WebSocket.

        Args:
            ws_ref: Mutable dict {"ws": websocket} so hooks always see current WS.
            thinking_ids: Shared dict {tool_use_id: thinking_uuid} between pre/post hooks.
            session_tools_used: Mutable list of tool names — populated by post_tool_use.
            session_id: Session ID for this connection (for pending clarifications).
            resume_session_id: SDK session ID to resume (from previous conversation).
            tool_call_counts: Mutable Counter for rate limiting (reset each query).
            prompt: Initial user prompt — used for dynamic model selection.
            trajectory_id: Active trajectory ID for recording tool calls (optional).
        """
        # In-process MCP server (shared instance, no subprocess overhead)
        mcp_servers = {"openeo": self._mcp_server_config}

        # Only allow our MCP tools (excludes all built-in tools).
        # Core set by default; JONA_FULL_TOOLSET=1 rolls back.
        allowed_tools = _core_allowed_tools()
        extra_args = dict(_STRICT_MCP_ARGS)

        # --- can_use_tool: intercept AskUserQuestion to route to frontend ---
        bridge_ref = self  # Capture self for closure

        async def can_use_tool(
            tool_name: str, input_data: dict, context: ToolPermissionContext
        ):
            if _strip_mcp_prefix(tool_name) == "AskUserQuestion":
                ws = ws_ref["ws"]
                questions = input_data.get("questions", [])
                if not questions:
                    return PermissionResultAllow(updated_input=input_data)

                # Send clarification request to frontend. Each question ends with a
                # clarification_closed event, however it ends, so the client stops
                # showing it and a reconnecting tab isn't asked it again.
                # One open question per session is the whole design: the answer is
                # routed by _pending_question_ids, and clarification_closed names a
                # single id. A second question used to overwrite the slot, which
                # orphaned the first future (a 600 s stall whose clarification_closed
                # never fired, so the client kept showing it) and then let the first
                # question's timeout pop the second one's slot, stalling that too.
                # Refusing is fail-closed and leaves the model a usable instruction;
                # queueing would need a second-question UI protocol that doesn't exist.
                open_future = bridge_ref._pending_clarifications.get(session_id)
                if open_future is not None and not open_future.done():
                    return PermissionResultDeny(
                        message=(
                            "A question is already open for this session and is waiting "
                            "for the user. Ask one question at a time: wait for this "
                            "answer before asking another."
                        )
                    )

                future = asyncio.get_running_loop().create_future()
                question_id = uuid.uuid4().hex[:12]
                bridge_ref._pending_clarifications[session_id] = future
                bridge_ref._pending_question_ids[session_id] = question_id

                def _release_slot() -> None:
                    """Drop the slot only while this question still owns it."""
                    if bridge_ref._pending_clarifications.get(session_id) is future:
                        bridge_ref._pending_clarifications.pop(session_id, None)

                async def _close_question() -> None:
                    if bridge_ref._pending_question_ids.get(session_id) == question_id:
                        bridge_ref._pending_question_ids.pop(session_id, None)
                    try:
                        await ws.send_json({"type": "clarification_closed", "question_id": question_id})
                    except Exception:  # noqa: BLE001
                        pass

                try:
                    await ws.send_json({
                        "type": "clarification",
                        "questions": questions,
                        "question_id": question_id,
                    })
                    # Long enough to survive a dropped connection and a reconnect.
                    answers = await asyncio.wait_for(
                        future, timeout=_CLARIFICATION_TIMEOUT_SECONDS
                    )
                    await _close_question()
                    return PermissionResultAllow(
                        updated_input={**input_data, "answers": _answers_by_question(questions, answers)}
                    )
                except asyncio.TimeoutError:
                    await _close_question()
                    _release_slot()
                    return PermissionResultDeny(
                        message=(
                            "The user did not answer. State any assumption you make "
                            "as an assumption, not as their choice."
                        )
                    )
                except Exception as e:
                    _release_slot()
                    await _close_question()
                    print(f"[SDK Bridge] AskUserQuestion error: {e}", file=sys.stderr)
                    return PermissionResultDeny(
                        message=f"Clarification failed: {e}"
                    )

            # All other tools: allow without modification
            return PermissionResultAllow(updated_input=input_data)

        # --- Hook callbacks ---

        # Mutable container to share last job title between pre/post hooks
        _last_job_title = {"value": ""}
        _active_project_id = self._project_refs.setdefault(session_id, {"value": active_project_id})
        _active_project_id["value"] = active_project_id
        _active_user_id = self._user_refs.setdefault(session_id, {"value": auth_user_id})
        _active_user_id["value"] = auth_user_id
        _graph_attempts: Dict[str, Any] = {"prompt": None, "counts": Counter()}

        def _current_prompt() -> str:
            """The user's question this turn; this session's hooks outlive the first one."""
            return (self._prompt_refs.get(session_id) or {}).get("value") or prompt

        # Rate-limit counters — shared with session entry, reset each query()
        _tool_call_counts: Counter = tool_call_counts if tool_call_counts is not None else Counter()
        # Trajectory recording — mutable dict shared with hook closures
        _traj_ctx: Dict[str, Any] = {
            "id": trajectory_id,
            "store": self._trajectory_store,
            "timers": {},      # tool_use_id -> start_time_ms
            "inputs": {},      # tool_use_id -> tool_input_data (for post hook)
        }

        def _deny(tool_name: str, reason: str) -> dict:
            """Build a deny hook response and log audit event."""
            _audit_log.info(
                "DENY session=%s tool=%s reason=%s", session_id, tool_name, reason
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }

        async def pre_tool_use(hook_input, tool_use_id_param, context):
            """Check permissions, then send tool_start and thinking to WebSocket.

            Security layers (in order):
            1. Blocked-tool deny list
            2. Input sanitisation (path traversal, shell metacharacters)
            3. Rate limiting (per-tool and global per-query)
            4. Spatial extent validation
            5. Audit logging of every decision
            """
            ws = ws_ref["ws"]
            tool_name = _strip_mcp_prefix(hook_input.get("tool_name", ""))
            tool_input_data = hook_input.get("tool_input", {})
            t_use_id = hook_input.get("tool_use_id", tool_use_id_param or "")
            display_name = _tool_display_name(tool_name)

            # --- Layer 1: Blocked tools ---
            if tool_name in BLOCKED_TOOLS:
                return _deny(tool_name, (
                    f"Tool '{tool_name}' is blocked for safety. "
                    "Bulk-delete operations are not permitted."
                ))
            # Generic built-ins (Bash/Write/Task/…) let the model fabricate data
            # instead of calling openeo_* tools. Also set via disallowed_tools;
            # denied here as defense-in-depth.
            if tool_name in GENERIC_BUILTIN_TOOLS_DENY:
                return _deny(tool_name, (
                    f"Tool '{tool_name}' is not available. Use the openeo_* tools "
                    "to retrieve real data — do not fabricate results."
                ))

            # --- Layer 2: Input sanitisation ---
            suspicious = check_suspicious_input(tool_input_data)
            if suspicious:
                return _deny(tool_name, (
                    f"Input rejected for tool '{tool_name}': {suspicious}"
                ))

            # --- Layer 3: Rate limiting ---
            _tool_call_counts[tool_name] += 1
            total_calls = sum(_tool_call_counts.values())

            # Per-tool limit
            per_tool_limit = RATE_LIMITS.get(tool_name)
            if per_tool_limit is not None and _tool_call_counts[tool_name] > per_tool_limit:
                return _deny(tool_name, (
                    f"Rate limit exceeded for '{tool_name}': "
                    f"{_tool_call_counts[tool_name]}/{per_tool_limit} calls this query."
                ))

            # Global limit
            if total_calls > RATE_LIMIT_DEFAULT:
                return _deny(tool_name, (
                    f"Global rate limit exceeded: {total_calls}/{RATE_LIMIT_DEFAULT} "
                    "tool calls this query."
                ))

            # --- Layer 3b: Can this graph answer the question at all? ---
            if tool_name in _JOB_START_TOOLS and forbids_execution(_current_prompt()):
                return _deny(tool_name, (
                    "The user asked for this to be answered without running anything. "
                    "Answer from what you already have, and say what you can't determine. "
                    "If you think they did want this run, ask them."
                ))
            if tool_name == "openeo_create_job":
                _submitted = tool_input_data.get("process_graph")
                _method_problem = check_graph_method(_current_prompt(), _submitted)
                if _method_problem:
                    return _deny(tool_name, _method_problem)
                _turn = (self._prompt_refs.get(session_id) or {}).get("turn")
                if _graph_attempts["prompt"] != _turn:
                    _graph_attempts.update(prompt=_turn, counts=Counter())
                _attempts = _graph_attempts["counts"]
                _attempts[graph_fingerprint(_submitted)] += 1
                if _attempts[graph_fingerprint(_submitted)] > _MAX_IDENTICAL_JOB_SUBMISSIONS:
                    return _deny(tool_name, (
                        "This exact graph has already been submitted "
                        f"{_MAX_IDENTICAL_JOB_SUBMISSIONS} times this turn. Change the approach "
                        "(a smaller area, other dates, a different method) or tell the user "
                        "why it isn't working."
                    ))

            # --- Layer 3c: is the graph structurally capable of running? ---
            # Graph validation is otherwise model-elective: validate_pre_execution is a
            # tool the system prompt asks for but nothing enforces. A graph with a cycle
            # or a reference to a node that isn't there cannot run on any backend, so
            # refusing it here replaces a remote failure with an immediate, specific
            # message. Only registry-independent faults are enforced — see
            # _validate_graph_structure for why UnknownProcess deliberately is not.
            if tool_name in _JOB_START_TOOLS:
                structure_problem = _validate_graph_structure(tool_input_data)
                if structure_problem:
                    return _deny(tool_name, structure_problem)

            # --- Layer 4: Spatial extent validation ---
            # SAFE_MODIFY_TOOLS is a *permission* category, so it is the wrong place to
            # add a tool just to get it extent-checked. _JOB_START_TOOLS is the set that
            # actually starts backend work, which is where an oversized extent costs
            # memory, so the gate is the union. See test_extent_gate_covers_job_starts.
            if tool_name in SAFE_MODIFY_TOOLS or tool_name in _JOB_START_TOOLS:
                extent_result = _validate_extent(tool_input_data)
                if extent_result is False:
                    return _deny(tool_name, (
                        "Spatial extent is too large (>50 degrees). "
                        "Please use a smaller area."
                    ))
                if isinstance(extent_result, dict):
                    msg = extent_result.get("message", "Large spatial extent detected")
                    return _deny(tool_name, msg)

            # --- All checks passed — audit ALLOW and proceed ---
            _audit_log.info(
                "ALLOW session=%s tool=%s call#=%d",
                session_id, tool_name, _tool_call_counts[tool_name],
            )

            # Trajectory recording: capture start time and input for this tool call
            if _traj_ctx["id"] and _traj_ctx["store"]:
                _traj_ctx["timers"][t_use_id] = int(time.time() * 1000)
                _traj_ctx["inputs"][t_use_id] = tool_input_data

            # Capture job title for naming saved results — check multiple tools
            _incoming_title = tool_input_data.get("title", "")
            if not _incoming_title:
                _incoming_title = tool_input_data.get("description", "")
            if _incoming_title and (
                tool_name in ("openeo_create_job", "openeo_generate_graph", "openeo_compose_graph",
                              "openeo_algorithm", "viz_show_map", "viz_show_ndvi_map",
                              "export_styled_geotiff", "export_report", "earth_agent_analyze")
                or not _last_job_title["value"]  # accept any title if we have none
            ):
                _last_job_title["value"] = _incoming_title

            tid = str(uuid.uuid4())
            thinking_ids[t_use_id] = tid

            try:
                await ws.send_json({
                    "type": "thinking",
                    "thinking_id": tid,
                    "thinking_type": "executing",
                    "content": f"Running {display_name}..."
                })
                await ws.send_json({
                    "type": "tool_start",
                    "tool_name": tool_name,
                    "tool_input": tool_input_data,
                    "tool_use_id": t_use_id,
                })
            except Exception as e:
                print(f"[SDK Bridge] PreToolUse WS error: {e}", file=sys.stderr)
            if tool_name.startswith("ea_") or tool_name in {"earth_agent_analyze", "get_analysis_strategies"}:
                try:
                    from ..earth_agent.grpo_strategies import (
                        detect_analysis_type,
                        detect_data_type,
                        get_relevant_strategies,
                    )
                    query_type = detect_analysis_type(tool_name, tool_input_data)
                    data_type = detect_data_type(tool_name, tool_input_data)
                    strategies = get_relevant_strategies(query_type, data_type)
                    if strategies:
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "additionalContext": strategies,
                            }
                        }
                except Exception as e:
                    print(f"[SDK Bridge] Earth-Agent strategy hook error: {e}", file=sys.stderr)

            return {}  # Empty SyncHookJSONOutput = approve and continue

        async def post_tool_use(hook_input, tool_use_id_param, context):
            """Send tool_result, thinking_completed, viz, quality, graph to WebSocket."""
            ws = ws_ref["ws"]
            tool_name = _strip_mcp_prefix(hook_input.get("tool_name", ""))
            tool_input = hook_input.get("tool_input", {})
            tool_response = hook_input.get("tool_response", "")
            t_use_id = hook_input.get("tool_use_id", tool_use_id_param or "")

            # Track tool usage for contextual suggestions
            session_tools_used.append(tool_name)

            # Phase B: annotate the auto-captured Claude SDK tool span with
            # masked args, result size, and tool.category attribute.
            # Phase E: emit implicit-tool-errors score on failure.
            try:
                from ..langfuse_setup import annotate_tool_span, emit_score
                _error_text = None
                if isinstance(tool_response, dict) and tool_response.get("isError"):
                    _error_text = str(tool_response.get("content", tool_response))[:500]
                annotate_tool_span(
                    tool_name=tool_name,
                    args=tool_input,
                    result=tool_response,
                    error=_error_text,
                )
                if _error_text:
                    emit_score(
                        name="implicit-tool-errors",
                        value=-1.0,
                        comment=f"{tool_name}: {_error_text[:200]}",
                    )
            except Exception:
                pass  # Tracing never breaks the user flow

            # Trajectory recording: capture tool call with timing and params
            if _traj_ctx["id"] and _traj_ctx["store"]:
                try:
                    from ..evaluation.trajectory_store import ToolCall as TrajToolCall
                    start_ms = _traj_ctx["timers"].pop(t_use_id, 0)
                    duration = int(time.time() * 1000) - start_ms if start_ms else 0
                    result_text = str(tool_response)[:500] if tool_response else ""
                    is_error = (
                        isinstance(tool_response, dict)
                        and tool_response.get("isError", False)
                    )
                    _traj_ctx["store"].record_tool_call(
                        _traj_ctx["id"],
                        TrajToolCall(
                            tool_name=tool_name,
                            parameters=_traj_ctx["inputs"].pop(t_use_id, {}),
                            result_summary=result_text,
                            success=not is_error,
                            duration_ms=duration,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                except Exception:
                    pass  # Non-critical

            # Parse tool response — may be JSON string or MCP content format
            tool_result = _parse_tool_response(tool_response)

            # Harvest openeo process_ids from this turn's graph/job payloads
            # (inputs + results) for the method contract. Cheap, fail-open.
            try:
                _pids = _collect_process_ids(tool_input) | _collect_process_ids(tool_result)
                if isinstance(tool_input, dict) and compares_periods(tool_input.get("process_graph")):
                    self._sessions.setdefault(session_id, {})["turn_period_difference"] = True
                if _pids:
                    _gpz = self._sessions.setdefault(session_id, {}).setdefault(
                        "turn_graph_processes", []
                    )
                    _gpz.extend(p for p in sorted(_pids) if p not in _gpz)
            except Exception:
                pass

            # Record this turn's tool NAMES (turn-level, both tiers) so the
            # complex-tier completion contracts know an openeo job/graph tool ran —
            # the per-stream tools_used list only covers one specialist.
            try:
                _tnl = self._sessions.setdefault(session_id, {}).setdefault(
                    "turn_tool_names", []
                )
                if tool_name not in _tnl:
                    _tnl.append(tool_name)
            except Exception:
                pass

            # Reason: Report tools return formatted markdown + raw JSON blob.
            # Only send the first (human-readable) text block to the frontend.
            _REPORT_DISPLAY_TOOLS = {"interpret_result", "generate_report", "earth_agent_analyze", "raster_statistics"}
            try:
                # 1. Send tool_result — extract text for display, not raw MCP format
                if isinstance(tool_result, dict) and "content" in tool_result:
                    _ci = tool_result["content"]
                    if isinstance(_ci, list):
                        _tp = [it.get("text", "") for it in _ci if isinstance(it, dict) and it.get("type") == "text"]
                        if tool_name in _REPORT_DISPLAY_TOOLS and len(_tp) > 1:
                            _display = _tp[0]  # Only formatted markdown, not JSON blob
                        else:
                            _display = "\n".join(_tp) if _tp else str(tool_result)
                    else:
                        _display = str(tool_result)
                elif isinstance(tool_result, str):
                    _display = tool_result
                else:
                    _display = tool_result
                await ws.send_json({
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "result": _display,
                    "tool_use_id": t_use_id,
                })

                # Retain this turn's tool output (rolling window) so the completion-
                # point grounding guard can check the assistant's numbers against real results.
                try:
                    _tr = self._sessions.setdefault(session_id, {}).setdefault("turn_tool_results", [])
                    _tr.append(str(_display)[:2000])
                    del _tr[:-40]  # bound memory — keep the last 40 tool outputs
                except Exception:
                    pass

                # Keep data-coverage notes so the user sees them whatever the agent writes.
                try:
                    _dn = self._sessions.setdefault(session_id, {}).setdefault("turn_data_notes", [])
                    for _note in _SCENE_NOTE_RE.findall(str(_display)):
                        if _note not in _dn:
                            _dn.append(_note)
                except Exception:
                    pass

                # 2. Send thinking completed
                tid = thinking_ids.pop(t_use_id, None)
                if tid:
                    await ws.send_json({
                        "type": "thinking",
                        "thinking_id": tid,
                        "thinking_type": "executing",
                        "content": f"Running {_tool_display_name(tool_name)}...",
                        "thinking_completed": True
                    })

                # 3. Extract and send visualizations — off-loop: the full-band
                # raster read + sync archive copy inside extract_visualization
                # would otherwise block WS keepalive pongs and kill the socket.
                viz_list = await asyncio.to_thread(
                    extract_visualization,
                    tool_name,
                    tool_result,
                    title_hint=_last_job_title["value"],
                    project_id=_active_project_id["value"],
                    user_id=_active_user_id["value"],
                    source_query=prompt,
                )
                if not viz_list and (tool_name.startswith("ea_") or tool_name == "earth_agent_analyze"):
                    # Only use result_mapper fallback if the tool_result doesn't already contain viz items
                    _has_embedded_viz = (
                        isinstance(tool_result, dict) and
                        isinstance(tool_result.get("content"), list) and
                        any(isinstance(it, dict) and it.get("type") == "visualization" for it in tool_result["content"])
                    )
                    if not _has_embedded_viz:
                        try:
                            from ..earth_agent.result_mapper import to_visualizations
                            viz_list = await asyncio.to_thread(
                                to_visualizations,
                                tool_result,
                                tool_name=tool_name,
                                title=_last_job_title["value"] or None,
                            )
                        except Exception as e:
                            print(f"[SDK Bridge] Earth-Agent viz hook error: {e}", file=sys.stderr)
                for viz in viz_list:
                    # save_id is set directly by viz_extractor; extract for saved_job_created msg
                    _saved_id = viz.get("save_id")
                    # Record this turn's emitted viz types for the
                    # deliverable contract ("asked for a map, produced none").
                    try:
                        _vt = self._sessions.setdefault(session_id, {}).setdefault(
                            "turn_viz_types", []
                        )
                        _vt.append(viz.get("type"))
                        del _vt[:-40]  # bound memory — mirrors turn_tool_results
                    except Exception:
                        pass
                    await ws.send_json({
                        "type": "visualization",
                        "visualization": viz,
                    })
                    # Notify frontend to refresh saved results if auto-save succeeded
                    if _saved_id:
                        await ws.send_json({
                            "type": "saved_job_created",
                            "save_id": _saved_id,
                        })

                # 4. Send quality_metrics (additional message for dashboard)
                if tool_name == "openeo_quality_metrics":
                    await ws.send_json({
                        "type": "quality_metrics",
                        "metrics": tool_result,
                    })

                # 5. Send process_graph (additional message for graph panel)
                _GRAPH_TOOLS = {"openeo_generate_graph", "openeo_compose_graph", "openeo_raw_graph", "openeo_algorithm"}
                if tool_name in _GRAPH_TOOLS:
                    _is_graph = (
                        isinstance(tool_result, dict)
                        and any(isinstance(v, dict) and "process_id" in v for v in tool_result.values())
                    )
                    if _is_graph:
                        await ws.send_json({
                            "type": "process_graph",
                            "graph": tool_result,
                        })

                # 6. Extract and send citations/sources from tool results
                _sources = _extract_sources(tool_name, tool_result)
                if _sources:
                    await ws.send_json({
                        "type": "citations",
                        "tool_name": tool_name,
                        "sources": _sources,
                    })

                # 7. DECISION LAYER — inject decision frame after interpret_result
                # This transforms Claude's response from DATA → ANSWER
                _DECISION_TOOLS = {"interpret_result", "raster_statistics", "result_inspect"}
                if tool_name in _DECISION_TOOLS and isinstance(tool_result, dict):
                    _decision_data = _build_decision_data(tool_name, tool_result)
                    if _decision_data:
                        # Send health score to frontend for the Health Score Card
                        try:
                            await ws.send_json({
                                "type": "health_score",
                                "data": _decision_data,
                            })
                        except Exception:
                            pass
                        # Also inject decision frame into Claude's context
                        _decision_frame = _format_decision_context(_decision_data)
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "PostToolUse",
                                "additionalContext": _decision_frame,
                            }
                        }

                # 8. Post-job analysis prompt — inject rich context after get_results
                if tool_name in ("openeo_get_results", "result_load") and isinstance(tool_result, dict):
                    _file_path = tool_result.get("file_path") or tool_result.get("path") or tool_result.get("output_path")
                    if _file_path and not tool_result.get("error"):
                        _post_job_context = _build_post_job_analysis_prompt(
                            file_path=_file_path,
                            bounds=tool_result.get("bounds"),
                            statistics=tool_result.get("statistics"),
                            file_format=tool_result.get("format", "GTiff"),
                            job_title=_last_job_title.get("value", ""),
                        )
                        if _post_job_context:
                            return {
                                "hookSpecificOutput": {
                                    "hookEventName": "PostToolUse",
                                    "additionalContext": _post_job_context,
                                }
                            }

            except Exception as e:
                print(f"[SDK Bridge] PostToolUse WS error: {e}", file=sys.stderr)

            return {}  # Continue normally

        hooks = {
            "PreToolUse": [
                HookMatcher(matcher=".*", hooks=[pre_tool_use])
            ],
            "PostToolUse": [
                HookMatcher(matcher=".*", hooks=[post_tool_use])
            ],
        }

        # Dynamic model selection: route simple queries to haiku, complex to sonnet
        selected_model = _select_model(prompt, self.config.model)
        print(f"[SDK Bridge] Model selected: {selected_model} for prompt: {prompt[:80]}...", file=sys.stderr)

        # Phase C: fetch the system prompt from Langfuse with code fallback.
        # When Langfuse is available, the returned lf_prompt object is used
        # later to link the generation to the prompt version in the UI.
        from .prompts import get_prompt as _lf_get_prompt
        system_prompt_text, _main_lf_prompt = _lf_get_prompt(
            "jonaai/chat-assistant", fallback_attr="SYSTEM_PROMPT"
        )
        if not os.environ.get("DISABLE_LEARNED_PATTERNS"):
            try:
                from ..batch.template_store import TemplateStore
                store = TemplateStore(getattr(self.config, "sqlite_path", "data/openeo_ai.db"))
                patterns_text = store.get_injection_text(prompt)
                if patterns_text:
                    system_prompt_text = system_prompt_text + "\n\n" + patterns_text
                # Also inject trajectory patterns (if available)
                traj_text = store.get_trajectory_injection(prompt)
                if traj_text:
                    system_prompt_text = system_prompt_text + "\n" + traj_text
            except Exception:
                pass  # Never break chat for pattern retrieval

        opts = ClaudeAgentOptions(
            system_prompt=system_prompt_text,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            disallowed_tools=list(GENERIC_BUILTIN_TOOLS_DENY),
            permission_mode="default",
            max_turns=20,  # Increased from 10 — complex EO workflows need more turns
            model=selected_model,
            include_partial_messages=True,
            hooks=hooks,
            can_use_tool=can_use_tool,
            extra_args=extra_args,
        )

        _apply_reasoning(opts)
        if resume_session_id:
            opts.resume = resume_session_id
        return opts

    async def _cleanup_expired_sessions(self) -> None:
        """Remove SDK sessions that have been inactive beyond the TTL.

        Also evicts the oldest sessions if count exceeds _SDK_SESSION_MAX_COUNT.
        Called at the start of each query() call.
        """
        now = time.monotonic()
        expired = [
            sid for sid, entry in self._sessions.items()
            if sid not in self._busy_sessions
            and now - entry.get("last_active", 0) > _SDK_SESSION_TTL_SECONDS
        ]
        for sid in expired:
            # Each disconnect awaits, so a session can have become busy since the list was made.
            if sid in self._busy_sessions:
                continue
            print(f"[SDK Bridge] Expiring idle session {sid}", file=sys.stderr)
            await self.disconnect_session(sid)

        # Evict the longest-idle sessions over the limit. Running ones are never ended.
        # Only entries with an agent process count; turn notes hold no memory to speak of.
        excess = sum(1 for entry in self._sessions.values() if entry.get("client")) - _SDK_SESSION_MAX_COUNT
        if excess > 0:
            idle = sorted(
                (item for item in self._sessions.items()
                 if item[1].get("client") and item[0] not in self._busy_sessions),
                key=lambda item: item[1].get("last_active", 0),
            )
            for sid, _entry in idle[:excess]:
                if sid in self._busy_sessions:
                    continue
                print(f"[SDK Bridge] Evicting session {sid} (over max count)", file=sys.stderr)
                await self.disconnect_session(sid)

    async def _connect_session_client(self, session_id: str, client: Any) -> None:
        """Start the agent process stored for session_id.

        If it fails, the session keeps only its notes, so the next turn starts a fresh
        client instead of reusing one that never connected.
        """
        try:
            await client.connect()
        except BaseException:
            entry = self._sessions.get(session_id)
            if entry is not None and entry.get("client") is client:
                self._sessions[session_id] = {**_turn_notes(entry), "last_active": time.monotonic()}
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise

    async def disconnect_session(self, session_id: str) -> None:
        """Disconnect and clean up a session's SDK client.

        Its per-session refs go too; they grew forever before. A later query on the same
        session sets them again as it starts.
        """
        entry = self._sessions.pop(session_id, None)
        for refs in (self._user_refs, self._project_refs, self._prompt_refs):
            refs.pop(session_id, None)
        client = entry.get("client") if entry else None
        if client:
            try:
                await client.disconnect()
            except Exception as e:
                print(f"[SDK Bridge] Disconnect error for {session_id}: {e}", file=sys.stderr)

    async def disconnect_all(self) -> None:
        """Disconnect all SDK clients (e.g., on server shutdown)."""
        for sid in list(self._sessions.keys()):
            await self.disconnect_session(sid)

    def resolve_clarification(self, session_id: str, answers: Dict[str, Any],
                              question_id: Optional[str] = None) -> None:
        """Resolve a pending AskUserQuestion clarification with user answers.

        Called by the WebSocket handler when the frontend sends a clarification_response
        message. An answer naming a question other than the open one is ignored: it was
        meant for a question that already closed (a second tab, or an answer queued while
        offline). An answer with no id, from a page loaded before ids existed, is accepted.
        """
        open_id = self._pending_question_ids.get(session_id)
        if question_id is not None and open_id is not None and question_id != open_id:
            print(f"[SDK Bridge] Ignored an answer for a closed question in {session_id}", file=sys.stderr)
            return
        self._pending_question_ids.pop(session_id, None)
        future = self._pending_clarifications.pop(session_id, None)
        if future and not future.done():
            future.set_result(answers)
        else:
            print(f"[SDK Bridge] No pending clarification for {session_id}", file=sys.stderr)

    def stop_pending_clarification(self, session_id: str) -> bool:
        """End an open question because the user pressed Stop. True if one was open.

        The question's wait otherwise ran for up to 600 s, so Stop did nothing while a
        question was open. An exception rather than cancel(): can_use_tool's `except
        Exception` turns it into a denial and closes the question, where a CancelledError
        would escape it.
        """
        self._pending_question_ids.pop(session_id, None)
        future = self._pending_clarifications.pop(session_id, None)
        if future and not future.done():
            future.set_exception(RuntimeError("the user stopped the run"))
            return True
        return False

    def cancel_pending_clarification(self, session_id: str):
        """Cancel a pending clarification future on WebSocket disconnect.

        Prevents the SDK from hanging up to 120s waiting for user input
        that will never arrive after the client disconnects.
        """
        self._pending_question_ids.pop(session_id, None)
        future = self._pending_clarifications.pop(session_id, None)
        if future and not future.done():
            future.cancel()
            print(f"[SDK Bridge] Cancelled pending clarification for {session_id}")


def _parse_tool_response(tool_response: Any) -> Any:
    """Parse raw tool response from SDK hook into usable result dict.

    The tool_response from PostToolUse hook may be:
    - A JSON string
    - A dict with MCP "content" format: {"content": [{"type": "text", "text": "..."}]}
    - A bare list of MCP content items: [{"type": "text", "text": "..."}]
    - A plain string
    - A dict/list already

    Strategy: preserve the full MCP dict when content has multiple items or
    visualization items so extract_visualization() can parse structured JSON.
    Only unwrap single-item text content that parses to a dict (not str/list).
    """
    result = tool_response

    # Debug: log raw tool_response type for viz tools
    if isinstance(tool_response, (list, tuple)):
        first_repr = repr(tool_response[0])[:200] if len(tool_response) > 0 else "empty"
        logger.debug("[_parse_tool_response] list input, len=%d, first=%s", len(tool_response), first_repr)

    # Parse JSON string
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result

    # Unwrap MCP content format — bare list: [{"type": "text", "text": "..."}]
    if isinstance(result, list) and len(result) > 0:
        first = result[0]
        if isinstance(first, dict):
            has_viz = any(
                isinstance(item, dict) and item.get("type") == "visualization"
                for item in result
            )
            if has_viz:
                return {"content": result}
            if len(result) > 1:
                return {"content": result}
            if first.get("type") == "text":
                text = first.get("text", "")
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                    return {"content": result}
                except (json.JSONDecodeError, TypeError):
                    return {"content": result}

    # Unwrap MCP content format — dict wrapper: {"content": [{"type": "text/visualization", ...}]}
    if isinstance(result, dict) and "content" in result:
        content_items = result.get("content", [])
        if isinstance(content_items, list) and len(content_items) > 0:
            has_viz = any(
                isinstance(item, dict) and item.get("type") == "visualization"
                for item in content_items
            )
            if has_viz:
                return result
            if len(content_items) > 1:
                return result
            first = content_items[0]
            if isinstance(first, dict):
                if first.get("type") == "text":
                    text = first.get("text", "")
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            return parsed
                        return result
                    except (json.JSONDecodeError, TypeError):
                        return result

    return result


def _generate_suggestions(tools_used: list, prompt: str) -> list:
    """Generate contextual follow-up suggestions based on tools used during the query."""
    unique_tools = list(dict.fromkeys(tools_used))  # Deduplicate, preserve order
    suggestions = []

    has_viz = any(t.startswith("viz_") for t in unique_tools)
    has_job = "openeo_create_job" in unique_tools or "openeo_get_results" in unique_tools
    has_graph = "openeo_generate_graph" in unique_tools
    has_collections = "openeo_list_collections" in unique_tools
    has_quality = "openeo_quality_metrics" in unique_tools
    has_ndvi = "ndvi" in prompt.lower()
    has_temporal = "openeo_parse_temporal" in unique_tools

    if has_job and has_viz:
        suggestions.append("Compare this result with a different time period")
        suggestions.append("Calculate statistics for this area")
        suggestions.append("Export the result as GeoTIFF")
        suggestions.append("Run the same analysis for a nearby region")
    elif has_job:
        suggestions.append("Show the result on the map")
        suggestions.append("Run the same analysis for a different area")
        suggestions.append("Check the quality metrics for this job")
    elif has_graph:
        suggestions.append("Run this process graph as a job")
        suggestions.append("Validate and optimize the process graph")
        suggestions.append("Modify the graph to add cloud masking")
    elif has_collections:
        suggestions.append("Show NDVI using one of these collections")
        suggestions.append("What bands are available in Sentinel-2?")
        suggestions.append("Compare two collections for my area")
    elif has_quality:
        suggestions.append("Proceed with the analysis")
        suggestions.append("Try a different time period with less cloud cover")
        suggestions.append("Show available data for this region")

    if not suggestions:
        # Generic follow-ups based on what happened
        if has_temporal:
            suggestions.append("Run the analysis for this time period")
        suggestions.append("Show available collections for this area")
        suggestions.append("Calculate NDVI for a different region")
        suggestions.append("Analyze land cover changes over time")

    if has_ndvi and has_job:
        suggestions.insert(0, "Compare NDVI between two seasons")

    return suggestions[:4]


# --- Decision Layer: DATA → ANSWER transformation ---

_DECISION_FRAMES = {
    "ndvi": {"domain": "vegetation_health", "question": "Is this land healthy?"},
    "evi": {"domain": "vegetation_health", "question": "Is this land healthy?"},
    "savi": {"domain": "vegetation_health", "question": "Is this land healthy?"},
    "ndwi": {"domain": "water_risk", "question": "Is there a water problem?"},
    "mndwi": {"domain": "water_risk", "question": "Is there a water problem?"},
    "ndbi": {"domain": "urban_expansion", "question": "Is development encroaching?"},
    "nbr": {"domain": "fire_damage", "question": "How bad is the fire damage?"},
    "dnbr": {"domain": "fire_damage", "question": "How bad is the fire damage?"},
    "ndsi": {"domain": "snow_water", "question": "Is the snowpack normal?"},
    "ndmi": {"domain": "moisture_stress", "question": "Is there moisture stress?"},
}

_SCORE_BANDS = {
    "ndvi": [
        (0.6, 1.0, "Excellent", "Vegetation is healthy, no action needed"),
        (0.4, 0.6, "Moderate Stress", "Some vegetation stress — investigate irrigation, disease, or nutrient issues"),
        (0.2, 0.4, "Severe Stress", "URGENT: Vegetation declining — intervene immediately"),
        (-1.0, 0.2, "Critical/Bare", "Land is barren or dead — assess damage and plan recovery"),
    ],
    "ndwi": [
        (0.3, 1.0, "Flood Risk", "Water levels elevated — assess flood exposure"),
        (0.0, 0.3, "Normal", "Water levels within normal range"),
        (-0.3, 0.0, "Dry", "Below-normal water — monitor for drought"),
        (-1.0, -0.3, "Drought", "Significant water deficit — drought conditions"),
    ],
    "dnbr": [
        (0.44, 1.0, "High Severity Burn", "Significant fire damage — recovery 2-10 years"),
        (0.1, 0.44, "Low-Moderate Burn", "Partial damage — recovery 1-2 years"),
        (-1.0, 0.1, "Unburned", "No fire damage detected"),
    ],
    "ndsi": [
        (0.4, 1.0, "Snow/Ice", "Snow or ice cover detected — normal for winter/alpine"),
        (0.0, 0.4, "Partial Cover", "Mixed snow/land — seasonal transition"),
        (-1.0, 0.0, "Snow-Free", "No snow cover — check if this is expected for the season"),
    ],
}


def _prose_handoff_enabled() -> bool:
    """JONA_SPECIALIST_PROSE_HANDOFF=1 appends a specialist's final prose to the
    orchestrator handoff context. Default off until A1 measures the synthesis-coherence lift."""
    import os
    return os.environ.get("JONA_SPECIALIST_PROSE_HANDOFF", "").strip().lower() in ("1", "true", "yes", "on")


def _maybe_prose_line(name: str, prose: str, enabled: bool, limit: int = 2000) -> "Optional[str]":
    """The specialist's bounded final findings for the orchestrator handoff, or None when
    disabled/empty. The default metadata-only handoff is lossy — the specialist's reasoning never
    reaches orchestrator synthesis; this restores it behind a flag."""
    if not enabled or not prose:
        return None
    return f"Specialist '{name}' findings:\n{prose[:limit]}"


_SCENE_NOTE_RE = re.compile(r"Used \d+ of \d+\+? available dates \([^)\"]*\)")


def _answers_by_question(questions: list, answers: Any) -> Dict[str, str]:
    """Key answers by question text, the form the model reads. The card sends positions."""
    if not isinstance(answers, dict):
        return {}
    keyed: Dict[str, str] = {}
    for position, value in answers.items():
        index = int(position) if str(position).isdigit() else None
        question = questions[index] if index is not None and index < len(questions) else None
        key = question.get("question") if isinstance(question, dict) else None
        keyed[key or str(position)] = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return keyed


def _flag_unknown_plan_args(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Move plan arguments that a tool's schema doesn't define out of its args.

    Workers then see only real argument names, and what was meant stays visible.
    """
    from .client import TOOL_DEFINITIONS

    schemas = {tool["name"]: tool.get("input_schema", {}) for tool in TOOL_DEFINITIONS}
    for step in plan.get("steps") or []:
        if not isinstance(step, dict) or not isinstance(step.get("args"), dict):
            continue
        schema = schemas.get(_strip_mcp_prefix(str(step.get("tool", ""))))
        known = (schema or {}).get("properties")
        if not known or schema.get("additionalProperties"):
            continue
        unknown = {key: value for key, value in step["args"].items() if key not in known}
        if unknown:
            print(f"[plan-router] plan_arg_miss tool={step.get('tool')} args={sorted(unknown)}", file=sys.stderr)
            step["args"] = {key: value for key, value in step["args"].items() if key in known}
            step["unrecognized_args"] = unknown
    return plan


def _grounding_payload(text: str, tool_texts: list) -> "Optional[dict]":
    """Build a grounding_warning payload for trust-claim numbers (confidence/percent/
    score/delta) in `text` that no tool result supports, or None if every number is grounded.
    Pure — no I/O; the caller decides whether to emit it."""
    from openeo_ai.sdk.grounding_guard import check

    violations = check(text or "", tool_texts or [])
    if not violations:
        return None
    return {
        "type": "grounding_warning",
        "violations": [
            {"value": c.value, "token": c.token, "context": c.context} for c in violations
        ],
    }


def _unwrap_mcp_result(tool_result: Any) -> Any:
    """Interpret_result returns 2-item MCP content [markdown, json]; _parse_tool_response
    preserves that wrapper. Unwrap the JSON text block so decision extraction sees the real dict.
    A bare dict (single-tool result) passes through unchanged."""
    if isinstance(tool_result, dict) and isinstance(tool_result.get("content"), list):
        for item in tool_result["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    parsed = json.loads(item.get("text", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(parsed, dict):
                    return parsed
    return tool_result


def _build_decision_data(tool_name: str, tool_result: dict) -> dict | None:
    """Extract decision data from analysis tool results.

    Returns a dict with health_score, assessment, domain, confidence, coverage, etc.
    Used both for WebSocket health_score message AND Claude context injection.
    """
    tool_result = _unwrap_mcp_result(tool_result)
    index_name = None
    mean_val = None

    if isinstance(tool_result, dict):
        index_name = (
            tool_result.get("index_name", "")
            or tool_result.get("index", "")
            or ""
        ).lower().strip()

        stats = tool_result.get("statistics", tool_result.get("stats", {}))
        if isinstance(stats, dict):
            mean_val = stats.get("mean")
        if mean_val is None:
            mean_val = tool_result.get("mean")
        if mean_val is None and "min" in tool_result and "max" in tool_result:
            mean_val = tool_result.get("mean")

        if not index_name:
            for field in ("title", "file_path", "path", "output_path"):
                val = str(tool_result.get(field, "")).lower()
                for idx in _DECISION_FRAMES:
                    if idx in val:
                        index_name = idx
                        break
                if index_name:
                    break

    if not index_name or index_name not in _DECISION_FRAMES:
        return None
    if mean_val is None:
        return None
    try:
        mean_val = float(mean_val)
    except (TypeError, ValueError):
        return None

    frame = _DECISION_FRAMES[index_name]
    bands = _SCORE_BANDS.get(index_name, _SCORE_BANDS.get("ndvi", []))

    assessment = None
    for lo, hi, score, action in bands:
        if lo <= mean_val < hi:
            assessment = {"score": score, "action": action}
            break
    if not assessment:
        return None

    # Compute 0-100 health score
    if index_name in ("ndvi", "evi", "savi"):
        health_score = max(0, min(100, int(mean_val * 100)))
    elif index_name in ("ndwi", "mndwi"):
        health_score = max(0, min(100, int((mean_val + 1) * 50)))
    elif index_name in ("dnbr", "nbr"):
        health_score = max(0, min(100, int((1 - mean_val) * 100)))
    else:
        health_score = max(0, min(100, int((mean_val + 1) * 50)))

    # Determine color based on score
    if health_score >= 60:
        color = "green"
    elif health_score >= 40:
        color = "yellow"
    else:
        color = "red"

    # Thread the tool-computed confidence (grade) and coverage so the health_score
    # message carries trust the LLM must quote rather than originate. None when not computed.
    _quality = tool_result.get("quality", {}) if isinstance(tool_result, dict) else {}
    _interp = tool_result.get("interpretation", {}) if isinstance(tool_result, dict) else {}
    _stats2 = tool_result.get("statistics", tool_result.get("stats", {})) if isinstance(tool_result, dict) else {}
    _confidence = (_quality.get("grade") if isinstance(_quality, dict) else None) \
        or (_interp.get("grade") if isinstance(_interp, dict) else None)
    _coverage = _stats2.get("coverage_pct") if isinstance(_stats2, dict) else None

    return {
        "health_score": health_score,
        "color": color,
        "assessment": assessment["score"],
        "action": assessment["action"],
        "domain": frame["domain"],
        "question": frame["question"],
        "index_name": index_name.upper(),
        "index_value": round(mean_val, 4),
        "confidence": _confidence,
        "coverage": _coverage,
    }


def _format_decision_context(data: dict) -> str:
    """Format decision data as Claude context injection text."""
    return f"""
[DECISION_FRAMEWORK]
Domain: {data['domain']}
Question answered: "{data['question']}"
Assessment: {data['assessment']}
Health Score: {data['health_score']}/100
Recommended action: {data['action']}

RESPONSE INSTRUCTIONS:
- Lead with the ANSWER to "{data['question']}" — not the index value
- Express the assessment as: "{data['assessment']}" (Health Score: {data['health_score']}/100)
- Put recommended ACTIONS before technical details
- The map is EVIDENCE supporting your answer, not the answer itself
- Use plain language a non-technical executive would understand
- Include the {data['index_name']} value ({data['index_value']}) only as supporting data, parenthetically
- End with: what to DO, when to CHECK again, where to LOOK
"""


def _build_post_job_analysis_prompt(
    file_path: str,
    bounds: list | None = None,
    statistics: dict | None = None,
    file_format: str = "GTiff",
    job_title: str = "",
) -> str:
    """Build a structured post-job analysis prompt injected via PostToolUse hook.

    This prompt guides Claude to automatically inspect, visualize, and interpret
    job results without relying on the model to remember the full tool chain.
    """
    import os
    lines = [
        "## Post-Job Analysis Checklist",
        "(Internal guidance. Don't mention this checklist to the user.)",
        "",
        f"Job result ready: `{file_path}`",
    ]
    if job_title:
        lines.append(f"Analysis: {job_title}")
    lines.append(f"Format: {file_format}")
    if bounds:
        lines.append(f"Bounds: {bounds}")
    if statistics:
        lines.append(f"Pre-computed stats: {statistics}")
    lines.append("")
    lines.append("**Execute these steps NOW in order:**")
    lines.append(f"1. `result_inspect(file_path=\"{file_path}\")` — get bands, CRS, resolution, statistics")
    lines.append(f"2. `viz_show_map(geotiff_path=\"{file_path}\")` — display on interactive map")
    lines.append(f"3. `interpret_result(file_path=\"{file_path}\")` — domain-aware interpretation with grade")
    lines.append("")

    # Detect index from file path for smarter guidance
    path_lower = os.path.basename(file_path).lower()
    index_hints = {
        "ndvi": ("vegetation", "RdYlGn"),
        "ndwi": ("water", "coolwarm"),
        "mndwi": ("water", "coolwarm"),
        "nbr": ("fire", "RdYlGn_r"),
        "dnbr": ("fire", "inferno"),
        "ndbi": ("urban", "YlOrRd"),
        "ndsi": ("snow", "coolwarm"),
        "ndmi": ("moisture", "YlGnBu"),
        "evi": ("vegetation", "RdYlGn"),
        "savi": ("vegetation", "RdYlGn"),
        "bsi": ("soil", "YlOrBr"),
    }
    detected_domain = None
    detected_colormap = "viridis"
    for index_key, (domain, cmap) in index_hints.items():
        if index_key in path_lower:
            detected_domain = domain
            detected_colormap = cmap
            break

    if detected_domain:
        lines.append(f"**Detected domain**: {detected_domain} (colormap: {detected_colormap})")
        lines.append(f"Use colormap `{detected_colormap}` for viz_show_map.")
    lines.append("")
    lines.append("**After inspection**, SUGGEST deeper analysis to the user:")
    lines.append('"I can run advanced spatial analysis (hotspot detection, clustering, segmentation) on these results using Earth-Agent. Would you like me to proceed?"')
    lines.append(f"- Call `get_analysis_strategies(query_type=\"{detected_domain or 'general'}\")` for Earth-Agent strategies")
    lines.append(f"- Call `earth_agent_analyze(query=..., input_files=[\"{file_path}\"])` for complex multi-step analysis")
    lines.append("For disaster/fire/change_detection/urban domains, PROACTIVELY call earth_agent_analyze without waiting for user confirmation.")
    lines.append("")
    lines.append("Use EXACT file path from this result in all subsequent tool calls.")
    return "\n".join(lines)


def _extract_sources(tool_name: str, tool_result: Any) -> list:
    """Extract citation source URLs from tool results for provenance.

    Looks for 'sources' keys added by collection info, discovery tools,
    and STAC catalog links. Returns list of source strings (URLs, licenses).
    """
    sources = []

    if not isinstance(tool_result, dict):
        return sources

    # Direct 'sources' key (e.g., from get_collection_info)
    if "sources" in tool_result:
        src = tool_result["sources"]
        if isinstance(src, list):
            sources.extend(str(s) for s in src)
        elif isinstance(src, str):
            sources.append(src)

    # Collection ID → build STAC catalog URL
    coll_id = tool_result.get("id") or tool_result.get("collection_id")
    if coll_id and tool_name in (
        "openeo_get_collection_info", "openeo_list_collections",
        "recommend_collection",
    ):
        sources.append(
            f"https://earth-search.aws.element84.com/v1/collections/{coll_id}"
        )

    # Extract from nested content blocks (MCP format)
    content = tool_result.get("content", [])
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                # Try to parse as JSON and extract sources
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict) and "sources" in parsed:
                        psrc = parsed["sources"]
                        if isinstance(psrc, list):
                            sources.extend(str(s) for s in psrc)
                except (json.JSONDecodeError, TypeError):
                    pass

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique
