# ABOUTME: Agent tools for the Earth Intelligence spine — create/manage/triage
# ABOUTME: assets, watches, signals, deliveries, and evidence chains. User-scoped.

"""Spine/monitoring tools.

Every handler is user-scoped via the request ContextVar (``get_current_user_id``)
— these tools expose multi-tenant platform state, so an unscoped call is a
cross-tenant leak. Handlers never raise: they return structured error dicts the
LLM can relay. Repository/schema imports are lazy (inside the factory) to avoid
the sdk.* circular-import trap documented in goal_tools.py.
"""

import json
import os
from typing import Any, Dict

# Worst-first severity ranking for briefings/portfolio.
_SEVERITY_ORDER = {"critical": 4, "high": 3, "warning": 2, "info": 1}
_MAX_BRIEFING_SIGNALS = 5
_MAX_PORTFOLIO_ASSETS = 25
_MAX_PORTFOLIO_SIGNALS = 3
_EXPLANATION_CAP = 400


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str)}]}


def _err(code: str, detail: str = "") -> Dict[str, Any]:
    return _ok({"error": code, "detail": detail})


def _worker_active() -> bool:
    # Match the authoritative startup gate (web_interface._worker_enabled)
    # exactly, else the worker_active note lies. On by default; only an
    # explicit 0/false/no/off disables.
    return os.environ.get("EARTH_INTELLIGENCE_WORKER", "").lower() not in {"0", "false", "no", "off"}


_MAX_LIST_LIMIT = 200


def _clamp_limit(args: Dict[str, Any], default: int) -> int:
    """Parse a caller-supplied limit defensively and clamp it — an unbounded
    limit (LLM slip or prompt injection) is a memory-DoS."""
    try:
        val = int(args.get("limit", default))
    except (TypeError, ValueError):
        val = default
    return max(1, min(val, _MAX_LIST_LIMIT))


def _truncate(text: Any, n: int = _EXPLANATION_CAP) -> Any:
    if isinstance(text, str) and len(text) > n:
        return text[:n] + "…"
    return text


def create_monitor_tools(config=None) -> Dict[str, Any]:
    """Factory for the spine/monitoring handler dict (mirrors create_saved_jobs_tools)."""
    from pydantic import ValidationError

    from ..intelligence.repository import EarthIntelligenceRepository
    from ..intelligence.schemas import AssetCreate, WatchCreate, WatchUpdate
    from ..lifecycle import IllegalTransition
    from ..sdk.user_context import get_current_user_id
    from ..storage.job_archive import get_job_detail

    def _repo() -> EarthIntelligenceRepository:
        return EarthIntelligenceRepository()

    def _uid():
        return get_current_user_id()

    def _reject_extra(args: Dict[str, Any], schema) -> str:
        extra = set(args) - set(schema.model_fields)
        return f"unknown fields: {sorted(extra)}" if extra else ""

    def _signal_view(sig: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": sig.get("id"),
            "severity": sig.get("severity"),
            "metric": sig.get("metric"),
            "value": sig.get("value"),
            "status": sig.get("status"),
            "explanation": _truncate(sig.get("explanation")),
            "created_at": sig.get("created_at"),
        }

    def _worst(signals) -> int:
        return max((_SEVERITY_ORDER.get(s.get("severity"), 0) for s in signals), default=0)

    # ── assets ────────────────────────────────────────────────────────
    async def _create_asset(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated", "no authenticated user for this turn")
        bad = _reject_extra(args, AssetCreate)
        if bad:
            return _err("invalid_input", bad)
        try:
            data = AssetCreate(**args).model_dump(exclude_unset=True)
        except ValidationError as e:
            return _err("invalid_input", str(e))
        try:
            row = _repo().create_asset(uid, data)
        except Exception:
            # DB constraint (over-length asset_type, bad project_id FK) — never
            # leak the raw exception (SQL/params) to the LLM.
            return _err("write_failed", "could not create asset; check project_id and field lengths")
        return _ok({"id": row["id"], "name": row["name"], "asset": row})

    async def _list_assets(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        assets = _repo().list_assets(uid, limit=_clamp_limit(args, 100))
        return _ok({"count": len(assets),
                    "assets": [{"id": a["id"], "name": a["name"], "asset_type": a.get("asset_type")}
                               for a in assets]})

    # ── watches ───────────────────────────────────────────────────────
    async def _create_watch(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        args = dict(args)
        # Fold the friendly alert intent (flat params the LLM sets easily)
        # into quality_policy, alongside the existing alert_targets folding. The
        # worker's delivery gate (_delivery_allowed) reads metric/direction/threshold.
        alert_targets = args.pop("alert_targets", None)
        alert_metric = args.pop("alert_metric", None)
        alert_direction = args.pop("alert_direction", None)
        alert_threshold = args.pop("alert_threshold", None)
        if alert_threshold is not None and not isinstance(alert_threshold, (int, float)):
            return _err("invalid_input", "alert_threshold must be a number")
        qp = dict(args.get("quality_policy") or {})
        if alert_targets is not None:
            qp["alert_targets"] = alert_targets
        if alert_metric is not None:
            qp["metric"] = alert_metric
        if alert_direction is not None:
            qp["direction"] = alert_direction
        if alert_threshold is not None:
            qp["threshold"] = alert_threshold
        if qp:
            args["quality_policy"] = qp
        bad = _reject_extra(args, WatchCreate)
        if bad:
            return _err("invalid_input", bad)
        try:
            data = WatchCreate(**args).model_dump(exclude_unset=True)
        except ValidationError as e:
            return _err("invalid_input", str(e))
        if not data.get("process_graph"):
            return _err("empty_process_graph",
                        "generate a process graph with openeo_generate_graph before creating a watch")
        try:
            row = _repo().create_watch(uid, data)
        except Exception:
            return _err("write_failed", "could not create watch; check field lengths")
        if row is None:
            return _err("asset_not_found", "asset does not exist or is not yours")
        active = _worker_active()
        row["worker_active"] = active
        row["note"] = (
            "Background worker is active; observations run on schedule."
            if active else
            "Background worker is DISABLED — observations only run on a manual tick. "
            "Do not promise automatic monitoring until it is enabled."
        )
        return _ok(row)

    async def _update_watch_status(watch_id, status):
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        if not watch_id:
            return _err("invalid_input", "watch_id required")
        try:
            row = _repo().update_watch(uid, watch_id, {"status": status})
        except IllegalTransition as e:
            return _err("illegal_transition", str(e))
        if row is None:
            return _err("watch_not_found", "watch does not exist or is not yours")
        return _ok({"id": row["id"], "status": row["status"]})

    async def _pause_watch(args):
        return await _update_watch_status(args.get("watch_id"), "paused")

    async def _resume_watch(args):
        return await _update_watch_status(args.get("watch_id"), "active")

    async def _update_watch(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        args = dict(args)
        watch_id = args.pop("watch_id", None)
        if not watch_id:
            return _err("invalid_input", "watch_id required")
        # Validate through WatchUpdate (which has NO user_id/id/asset_id fields)
        # so a caller can't mass-assign protected columns via repository.setattr.
        bad = _reject_extra(args, WatchUpdate)
        if bad:
            return _err("invalid_input", bad)
        try:
            data = WatchUpdate(**args).model_dump(exclude_unset=True)
        except ValidationError as e:
            return _err("invalid_input", str(e))
        try:
            row = _repo().update_watch(uid, watch_id, data)
        except IllegalTransition as e:
            return _err("illegal_transition", str(e))
        except Exception:
            return _err("write_failed", "could not update watch")
        if row is None:
            return _err("watch_not_found")
        return _ok({"id": row["id"], "status": row.get("status")})

    # ── signals ───────────────────────────────────────────────────────
    async def _list_signals(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        sigs = _repo().list_signals(
            uid, asset_id=args.get("asset_id"), watch_id=args.get("watch_id"),
            status=args.get("status"), limit=_clamp_limit(args, 50),
        )
        return _ok({"count": len(sigs), "signals": [_signal_view(s) for s in sigs]})

    async def _signal_set(signal_id, key):
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        if not signal_id:
            return _err("invalid_input", "signal_id required")
        try:
            row = _repo().update_signal(uid, signal_id, {key: True})
        except IllegalTransition as e:
            return _err("illegal_transition", str(e))
        if row is None:
            return _err("signal_not_found", "signal does not exist or is not yours")
        return _ok({"id": row["id"], "status": row["status"]})

    async def _acknowledge(args):
        return await _signal_set(args.get("signal_id"), "acknowledged")

    async def _resolve(args):
        return await _signal_set(args.get("signal_id"), "resolved")

    # ── briefings ─────────────────────────────────────────────────────
    async def _asset_briefing(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        asset_id = args.get("asset_id")
        if not asset_id:
            return _err("invalid_input", "asset_id required")
        repo = _repo()
        asset = repo.get_asset(uid, asset_id)
        if asset is None:
            return _err("asset_not_found")
        watches = repo.list_watches(uid, asset_id=asset_id)
        signals = repo.list_signals(uid, asset_id=asset_id, limit=_MAX_BRIEFING_SIGNALS)
        return _ok({
            "asset": {"id": asset["id"], "name": asset["name"]},
            "watch_count": len(watches),
            "signals": [_signal_view(s) for s in signals],
        })

    async def _portfolio_briefing(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        repo = _repo()
        assets = repo.list_assets(uid, limit=_MAX_PORTFOLIO_ASSETS)
        rows = []
        for a in assets:
            sigs = repo.list_signals(uid, asset_id=a["id"], limit=_MAX_PORTFOLIO_SIGNALS)
            rows.append({
                "id": a["id"], "name": a["name"],
                "worst_severity_rank": _worst(sigs),
                "signals": [_signal_view(s) for s in sigs],
            })
        rows.sort(key=lambda r: r["worst_severity_rank"], reverse=True)
        for r in rows:
            r.pop("worst_severity_rank", None)
        return _ok({"count": len(rows), "assets": rows})

    # ── deliveries ────────────────────────────────────────────────────
    async def _list_deliveries(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        rows = _repo().list_deliveries(
            uid, signal_id=args.get("signal_id"), status=args.get("status"),
            limit=_clamp_limit(args, 50),
        )
        # NEVER surface Delivery.target — it may be a webhook URL with an embedded
        # secret or an email address.
        return _ok({"count": len(rows), "deliveries": [
            {"id": d["id"], "signal_id": d["signal_id"], "channel": d["channel"],
             "status": d["status"], "attempt_count": d.get("attempt_count"),
             "last_error": d.get("last_error"), "sent_at": d.get("sent_at")}
            for d in rows]})

    # ── evidence ──────────────────────────────────────────────────────
    async def _evidence_chain(args: Dict[str, Any]) -> Dict[str, Any]:
        uid = _uid()
        if not uid:
            return _err("not_authenticated")
        save_id = args.get("save_id")
        if not save_id:
            return _err("invalid_input", "save_id required")
        detail = get_job_detail(save_id, user_id=uid)
        if detail is None:
            return _err("not_found", "saved result does not exist or is not yours")
        return _ok({
            "save_id": save_id,
            "provenance": detail.get("provenance") or detail.get("metadata"),
            "statistics": detail.get("statistics"),
            "process_graph": detail.get("process_graph"),
            "evidence_packages": detail.get("evidence_packages", []),
        })

    return {
        "monitor_create_asset": _create_asset,
        "monitor_list_assets": _list_assets,
        "monitor_create_watch": _create_watch,
        "monitor_update_watch": _update_watch,
        "monitor_pause_watch": _pause_watch,
        "monitor_resume_watch": _resume_watch,
        "monitor_list_signals": _list_signals,
        "signal_acknowledge": _acknowledge,
        "signal_resolve": _resolve,
        "monitor_asset_briefing": _asset_briefing,
        "monitor_portfolio_briefing": _portfolio_briefing,
        "monitor_list_deliveries": _list_deliveries,
        "evidence_get_chain": _evidence_chain,
    }


def _obj(properties, required=None):
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


# Descriptions are the planner API — each states WHEN to use the tool.
MONITOR_TOOL_DEFINITIONS = [
    {
        "name": "monitor_create_asset",
        "description": (
            "Register a durable monitored area/asset. Use AFTER resolving the location "
            "(openeo_resolve_location) when the user wants ONGOING monitoring, not a "
            "one-off analysis. Requires name + bbox (or geometry)."
        ),
        "input_schema": _obj({
            "name": {"type": "string", "description": "Human name for the asset/area"},
            "bbox": {"type": "array", "items": {"type": "number"},
                     "description": "[west, south, east, north]"},
            "geometry": {"type": "object", "description": "GeoJSON geometry (alternative to bbox)"},
            "asset_type": {"type": "string", "description": "e.g. 'area', 'facility' (default 'area')"},
            "project_id": {"type": "string"},
        }, ["name"]),
    },
    {
        "name": "monitor_list_assets",
        "description": "List the current user's monitored assets. Use for 'what am I monitoring?'.",
        "input_schema": _obj({"limit": {"type": "integer"}}),
    },
    {
        "name": "monitor_create_watch",
        "description": (
            "Attach a recurring intelligence contract (watch) to an existing asset. "
            "Requires asset_id and a NON-EMPTY process_graph — generate one first with "
            "openeo_generate_graph (e.g. NDVI median). Set cadence to schedule it. To "
            "alert on change, set alert_metric + alert_direction (drop/rise) + "
            "alert_threshold, and alert_targets ([{channel, target}]) for where to send. "
            "A delivery fires only when the threshold is breached (no threshold → only "
            "warning/critical signals alert). The result reports worker_active — relay it "
            "honestly; with the worker disabled, observations only run on a manual tick."
        ),
        "input_schema": _obj({
            "asset_id": {"type": "string"},
            "name": {"type": "string"},
            "process_graph": {"type": "object", "description": "OpenEO process graph (must be non-empty)"},
            "description": {"type": "string"},
            "cadence": {"type": "string", "description": "e.g. 'weekly', 'daily', 'P1W'"},
            "collections": {"type": "array", "items": {"type": "string"}},
            "alert_metric": {"type": "string", "description": "metric to watch, e.g. 'NDVI'"},
            "alert_direction": {"type": "string", "description": "'drop' or 'rise' — which way triggers an alert"},
            "alert_threshold": {"type": "number", "description": "minimum change vs baseline that triggers an alert"},
            "alert_targets": {"type": "array", "items": {"type": "object"},
                              "description": "[{channel: webhook|slack|email, target: <url/address>}]"},
        }, ["asset_id", "name", "process_graph"]),
    },
    {
        "name": "monitor_update_watch",
        "description": "Update a watch's fields (name, cadence, process_graph, quality_policy). Requires watch_id.",
        "input_schema": _obj({"watch_id": {"type": "string"}}, ["watch_id"]),
    },
    {
        "name": "monitor_pause_watch",
        "description": "Pause a watch so it stops scheduling observations. Use for 'stop/pause monitoring X'.",
        "input_schema": _obj({"watch_id": {"type": "string"}}, ["watch_id"]),
    },
    {
        "name": "monitor_resume_watch",
        "description": "Resume a paused watch. Use for 'resume/re-enable monitoring X'.",
        "input_schema": _obj({"watch_id": {"type": "string"}}, ["watch_id"]),
    },
    {
        "name": "monitor_list_signals",
        "description": (
            "List intelligence signals (alerts) for the user, optionally filtered by "
            "asset_id/watch_id/status. Use for 'any alerts?', 'what changed?'."
        ),
        "input_schema": _obj({
            "asset_id": {"type": "string"}, "watch_id": {"type": "string"},
            "status": {"type": "string", "description": "open|acknowledged|resolved"},
            "limit": {"type": "integer"},
        }),
    },
    {
        "name": "signal_acknowledge",
        "description": "Mark a signal acknowledged ('I've seen it'). Requires signal_id.",
        "input_schema": _obj({"signal_id": {"type": "string"}}, ["signal_id"]),
    },
    {
        "name": "signal_resolve",
        "description": "Mark a signal resolved ('handled/closed'). Requires signal_id.",
        "input_schema": _obj({"signal_id": {"type": "string"}}, ["signal_id"]),
    },
    {
        "name": "monitor_asset_briefing",
        "description": (
            "Summarize ONE asset: its watches and latest signals (newest first, with "
            "severity/metric). Use for 'how is <asset> doing?'."
        ),
        "input_schema": _obj({"asset_id": {"type": "string"}}, ["asset_id"]),
    },
    {
        "name": "monitor_portfolio_briefing",
        "description": (
            "Summarize ALL of the user's assets at once, ranked worst-signal-severity "
            "first. Use for 'how are my monitored places doing?', 'anything urgent?'."
        ),
        "input_schema": _obj({}),
    },
    {
        "name": "monitor_list_deliveries",
        "description": (
            "List alert delivery attempts and their status (sent/failed) for the user. "
            "Use for 'did my alerts go out?'. Targets are never shown."
        ),
        "input_schema": _obj({
            "signal_id": {"type": "string"}, "status": {"type": "string"},
            "limit": {"type": "integer"},
        }),
    },
    {
        "name": "evidence_get_chain",
        "description": (
            "Retrieve the verifiable evidence chain (provenance, source STAC items, "
            "process graph, quality/statistics) for a saved result. Use when the user "
            "asks 'how do you know', 'prove it', or needs audit/MRV documentation."
        ),
        "input_schema": _obj({"save_id": {"type": "string"}}, ["save_id"]),
    },
]
