# ABOUTME: Natural-language query → typed EOIntent. Deterministic rule-based parser (always on)
# ABOUTME: + optional headless-claude enhancement + Nominatim AOI geocode + CLARIFY-on-missing-AOI policy.

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .models import (
    AnalysisType,
    AOISpec,
    BaselineSpec,
    EOIntent,
    TimeRange,
    Variable,
)

# --------------------------------------------------------------------------- #
# Keyword tables (real rule-based NLU — not mock; the LLM path only refines this)
# --------------------------------------------------------------------------- #

_VARIABLE_KEYWORDS: List[Tuple[Variable, Tuple[str, ...]]] = [
    (Variable.NBR, ("fire", "wildfire", "burn", "burnt", "burned", "scar")),
    # Reason: NDMI (vegetation-moisture) is checked before NDWI (open-water) so a specific
    # "water stress"/"drought" query is not swallowed by NDWI's bare "water" substring.
    # Bare "dry" is intentionally excluded (kept "dryness") — it collided with water-body
    # queries like "dry lake bed" while adding little over moisture/drought/dryness.
    (Variable.NDMI, ("moisture", "drought", "water stress", "dryness", "irrigation")),
    (Variable.NDWI, ("water", "lake", "reservoir", "flood", "river extent",
                     "shoreline", "waterbody", "water body", "dam")),
    (Variable.EVI, ("rainforest", "jungle", "dense canopy", "tropical forest")),
    (Variable.NDVI, ("vegetation", "veg", "crop", "greenness", "green", "forest",
                     "farm", "vigor", "vigour", "health", "biomass", "canopy",
                     "deforest", "growth", "grassland")),
]

# Ordered by signal SPECIFICITY, not by type — the first matching bucket wins.
# An explicit "compared to X"/"before and after" is a stronger, less ambiguous intent than a
# generic time-window phrase ("over the last N months"), so strong-CHANGE is checked before TREND.
# ANOMALY's "compared to normal/average" must beat generic CHANGE's "compared to", so it comes first.
_ANALYSIS_KEYWORDS: List[Tuple[AnalysisType, Tuple[str, ...]]] = [
    (AnalysisType.ANOMALY, ("anomaly", "anomalous", "unusual", "abnormal", "vs normal",
                            "versus normal", "compared to normal", "vs average",
                            "versus average", "vs the average")),
    # Strong CHANGE: explicit baseline comparison — unambiguous, beats a time-window phrase.
    (AnalysisType.CHANGE, ("compared to", "compared with", "versus", " vs ",
                           "before and after", "difference between")),
    (AnalysisType.TREND, ("trend", "over time", "trajectory", "declining", "decline",
                          "over the past", "over the last", "year over year", "long-term",
                          "long term", "multi-year", "degradation")),
    # Weak CHANGE: bare change verbs — used only when nothing more specific matched.
    (AnalysisType.CHANGE, ("change", "changed", "compare", "difference", "since last",
                           "gained", "lost", "increase", "decrease")),
]

_CLOUD_WORDS = {
    "cloud-free": 5.0, "cloud free": 5.0, "clear-sky": 10.0, "clear sky": 10.0,
    "clear": 10.0,
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "JonaAI-agentic-eo/1.0 (decision-intelligence)"


# --------------------------------------------------------------------------- #
# Deterministic field extraction
# --------------------------------------------------------------------------- #

def _detect_variable(q: str) -> Tuple[Variable, bool]:
    """Return (variable, was_explicit). NDVI is the default when nothing matches."""
    for var, kws in _VARIABLE_KEYWORDS:
        if any(k in q for k in kws):
            return var, True
    return Variable.NDVI, False


def _detect_analysis(q: str) -> Tuple[AnalysisType, bool]:
    for at, kws in _ANALYSIS_KEYWORDS:
        if any(k in q for k in kws):
            return at, True
    return AnalysisType.SNAPSHOT, False


_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20,
}
_UNIT_DAYS = {"week": 7, "month": 30, "season": 365, "year": 365, "decade": 3650}


def _detect_cloud(q: str) -> Tuple[float, bool]:
    m = re.search(r"(?:cloud[\s-]*(?:cover)?\s*(?:under|below|<|less than)?\s*)(\d{1,2})\s*%", q)
    if m:
        return float(m.group(1)), True
    for word, val in _CLOUD_WORDS.items():
        if word in q:
            return val, True
    return 20.0, False


def _detect_time(q: str, today: date) -> Tuple[TimeRange, bool]:
    """Parse an explicit time expression; fall back to the last 6 months."""
    # "last/past N months|years|weeks|seasons", N as digits or a word ("last three dry seasons").
    # N seasons recur yearly, so they span N years.
    m = re.search(
        r"(?:last|past|previous)\s+(\d+|" + "|".join(_NUMBER_WORDS) + r")\s*(?:\w+\s+)?"
        r"(week|month|year|season|decade)s?\b",
        q,
    )
    if m:
        n = _NUMBER_WORDS.get(m.group(1)) or int(m.group(1))
        unit = m.group(2)
        # Cap the span so an absurd integer can't overflow timedelta (its max is ~2.7e6 days).
        days = min(_UNIT_DAYS[unit] * n, 366 * 60)
        return TimeRange(start=today - timedelta(days=days), end=today), True

    # "last year", "past decade": one unit
    m = re.search(r"(?:last|past|previous)\s+(week|month|year|decade)\b", q)
    if m:
        return TimeRange(start=today - timedelta(days=_UNIT_DAYS[m.group(1)]), end=today), True

    # "between YYYY[-MM] and YYYY[-MM]" or "from X to Y"
    m = re.search(r"(?:between|from)\s+(\d{4})(?:-(\d{1,2}))?\s+(?:and|to|-)\s+(\d{4})(?:-(\d{1,2}))?", q)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(3))
        mo1 = int(m.group(2)) if m.group(2) else 1
        mo2 = int(m.group(4)) if m.group(4) else 12
        if _valid_ym(y1, mo1) and _valid_ym(y2, mo2):
            start = date(y1, mo1, 1)
            end = _end_of_month(y2, mo2)
            return TimeRange(start=start, end=min(end, today)), True

    # "since YYYY"
    m = re.search(r"since\s+(\d{4})", q)
    if m:
        yr = int(m.group(1))
        if _valid_ym(yr, 1):
            return TimeRange(start=date(yr, 1, 1), end=today), True

    # "in <month> YYYY" or "in YYYY"
    m = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{4})", q)
    if m:
        mo = _MONTHS[m.group(1)]
        yr = int(m.group(2))
        if _valid_ym(yr, mo):
            return TimeRange(start=date(yr, mo, 1), end=_end_of_month(yr, mo)), True
    m = re.search(r"\bin\s+(\d{4})\b", q)
    if m:
        yr = int(m.group(1))
        if _valid_ym(yr, 1):
            return TimeRange(start=date(yr, 1, 1), end=min(date(yr, 12, 31), today)), True

    # default: last 6 months (also the honest fall-back for an unparseable/out-of-range date)
    return TimeRange(start=today - timedelta(days=182), end=today), False


def _end_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _valid_ym(year: int, month: int) -> bool:
    """Guard date() construction: reject an out-of-range year/month before it raises ValueError."""
    return 1 <= year <= 9999 and 1 <= month <= 12


def _shift_year(d: date, delta: int) -> date:
    """Shift a date by whole years, clamping Feb 29 → Feb 28 in a non-leap target year."""
    target_year = max(1, d.year + delta)
    try:
        return d.replace(year=target_year)
    except ValueError:
        return d.replace(year=target_year, day=28)


def _extract_aoi_text(q_raw: str) -> Optional[str]:
    """Pull the place phrase after a spatial preposition. Stops at time/clause words."""
    stop = (r"(?:\s+(?:in|during|over the|from|between|since|for the|last|past|"
            r"this|to determine|to see|to check|to assess|compared|versus|vs|and|"
            r"\d{4}|over|before|after)\b|[?.,;]|$)")
    for prep in (r"over", r"in", r"near", r"around", r"at", r"within", r"across", r"of", r"for"):
        m = re.search(rf"\b{prep}\s+([A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){{0,4}})", q_raw)
        if m:
            cand = m.group(1).strip()
            # trim a trailing capitalized time word like "April"
            cand = re.sub(r"\s+(" + "|".join(w.capitalize() for w in _MONTHS) + r")$", "", cand)
            if len(cand) >= 3:
                return cand
    return None


# --------------------------------------------------------------------------- #
# Geocoding (real HTTP, Nominatim)
# --------------------------------------------------------------------------- #

def geocode_aoi(text: str, timeout: float = 15.0) -> Optional[AOISpec]:
    """Resolve a place name to a WGS84 bbox via Nominatim. None if unresolved."""
    try:
        r = httpx.get(
            _NOMINATIM_URL,
            params={"q": text, "format": "jsonv2", "limit": 1, "polygon_geojson": 0},
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        r.raise_for_status()
        hits = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not hits:
        return None
    h = hits[0]
    # Nominatim boundingbox = [south, north, west, east] as strings
    s, n, w, e = (float(x) for x in h["boundingbox"])
    bbox = [w, s, e, n]
    return AOISpec(
        raw_text=text,
        bbox=bbox,
        resolved_name=h.get("display_name", text),
        area_km2=_bbox_area_km2(bbox),
    )


_MAX_SPAN_DEG = 0.30   # ~33 km — keeps the AOI inside a single Sentinel-2 tile's coverage


def _clamp_bbox(aoi: AOISpec, assumptions: List[str], max_span: float = _MAX_SPAN_DEG) -> AOISpec:
    """Shrink an over-large geocoded bbox to a centred window so AOI-median reads stay tile-consistent."""
    if not aoi.bbox:
        return aoi
    w, s, e, n = aoi.bbox
    if (e - w) <= max_span and (n - s) <= max_span:
        return aoi
    cx, cy = (w + e) / 2.0, (s + n) / 2.0
    h = max_span / 2.0
    clamped = [cx - h, cy - h, cx + h, cy + h]
    assumptions.append(
        f"AOI '{aoi.resolved_name or aoi.raw_text}' spans a large area → analysed a centred "
        f"{max_span:.2f}°×{max_span:.2f}° (~{max_span * 111:.0f} km) window to keep pixels tile-consistent."
    )
    return AOISpec(raw_text=aoi.raw_text, bbox=clamped,
                   resolved_name=aoi.resolved_name, area_km2=_bbox_area_km2(clamped))


def _bbox_area_km2(bbox: List[float]) -> float:
    import math
    w, s, e, n = bbox
    mean_lat = math.radians((s + n) / 2.0)
    dx = (e - w) * 111.32 * math.cos(mean_lat)
    dy = (n - s) * 110.57
    return abs(dx * dy)


# --------------------------------------------------------------------------- #
# Optional LLM refinement (headless claude CLI — real model, standard context)
# --------------------------------------------------------------------------- #

_LLM_PROMPT = """You are an Earth-observation query parser. Read the user question and output ONLY a
JSON object (no prose, no code fence) with these keys:
  "variable": one of ["ndvi","ndwi","ndmi","nbr","evi"]  (vegetation=ndvi, open water=ndwi, \
vegetation moisture/drought=ndmi, fire/burn=nbr, dense tropical canopy=evi)
  "analysis_type": one of ["snapshot","change","trend","anomaly"]
  "aoi_text": the place name / area of interest as a geocodable string, or null
  "decision_context": one short sentence on the decision this informs, or ""
Question: {query}
JSON:"""


def _llm_parse(query: str, model: str, timeout: float) -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", model, _LLM_PROMPT.format(query=query)],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def _aoi_from_bbox(bbox: Any, label: Optional[str]) -> Optional[AOISpec]:
    """Build a user-supplied AOISpec from an explicit [west, south, east, north].

    Returns None for a malformed box so the caller can ask for a correction.
    """
    try:
        w, s, e, n = (float(x) for x in bbox)
    except (TypeError, ValueError):
        return None
    if not (w < e and s < n and -180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90):
        return None
    return AOISpec(
        raw_text=label or f"bbox [{w}, {s}, {e}, {n}]",
        bbox=[w, s, e, n],
        area_km2=_bbox_area_km2([w, s, e, n]),
        provenance="user-supplied",
    )


def _window_to_timerange(window: Any) -> Optional[TimeRange]:
    """Coerce an explicit [start, end] (ISO strings or date objects) into a TimeRange.
    Returns None if malformed or start >= end, so the caller can raise a structured error."""
    try:
        start_raw, end_raw = window
        start = start_raw if isinstance(start_raw, date) else date.fromisoformat(str(start_raw))
        end = end_raw if isinstance(end_raw, date) else date.fromisoformat(str(end_raw))
    except (TypeError, ValueError):
        return None
    if start >= end:
        return None
    return TimeRange(start=start, end=end)


def decompose(
    query: str,
    today: Optional[date] = None,
    use_llm: bool = True,
    do_geocode: bool = True,
    llm_model: str = "claude-opus-5-5",
    llm_timeout: float = 180.0,
    aoi_bbox: Optional[List[float]] = None,
    recipe_id: Optional[str] = None,
    window: Optional[List[Any]] = None,
    thresholds: Optional[Dict[str, float]] = None,
) -> EOIntent:
    """Turn a natural-language EO question into a typed EOIntent with logged assumptions.

    recipe_id pins the (variable × analysis_type) cell explicitly, window pins the
    time range, thresholds override recipe gate values for this run — the decision verb's
    structured controls. Each overrides the parsed/LLM value; malformed input clarifies."""
    today = today or date.today()
    q = query.lower()
    assumptions: List[str] = []

    variable, var_explicit = _detect_variable(q)
    analysis_type, at_explicit = _detect_analysis(q)
    cloud, cloud_explicit = _detect_cloud(q)
    time, time_explicit = _detect_time(q, today)
    aoi_text = _extract_aoi_text(query)
    decision_context = ""

    # LLM refinement (optional; only fills categorical/text fields, never coordinates)
    if use_llm:
        llm = _llm_parse(query, llm_model, llm_timeout)
        if llm:
            variable, var_explicit = _merge_enum(Variable, llm.get("variable"), variable, var_explicit)
            analysis_type, at_explicit = _merge_enum(
                AnalysisType, llm.get("analysis_type"), analysis_type, at_explicit
            )
            if not aoi_text and llm.get("aoi_text"):
                aoi_text = str(llm["aoi_text"]).strip() or None
            if llm.get("decision_context"):
                decision_context = str(llm["decision_context"]).strip()

    # Structured overrides — authoritative over anything the parser/LLM inferred.
    recipe_error: Optional[str] = None
    if recipe_id is not None:
        from .recipes import REGISTRY  # local import avoids a module cycle at import time
        resolved = REGISTRY.parse_recipe_id(recipe_id)
        if resolved is None:
            recipe_error = (f"Unknown recipe '{recipe_id}'. Use '{{variable}}_{{analysis_type}}' "
                            f"where variable ∈ ndvi/ndwi/ndmi/nbr/evi and analysis_type ∈ "
                            f"snapshot/change/trend/anomaly (e.g. 'ndvi_change').")
        else:
            variable, analysis_type = resolved
            var_explicit = at_explicit = True

    window_error: Optional[str] = None
    if window is not None:
        wr = _window_to_timerange(window)
        if wr is None:
            window_error = ("The analysis window is invalid; give [start, end] as ISO dates "
                            "(e.g. ['2024-01-01', '2024-06-30']) with start before end.")
        else:
            time, time_explicit = wr, True

    # Log defaulted fields as assumptions
    if not var_explicit:
        assumptions.append(f"Variable not stated → defaulted to {variable.value.upper()}.")
    if not at_explicit:
        assumptions.append(f"Analysis type not stated → defaulted to {analysis_type.value}.")
    if not cloud_explicit:
        assumptions.append(f"Cloud threshold not stated → defaulted to {cloud:.0f}% max cloud cover.")
    if not time_explicit:
        assumptions.append(
            f"Time range not stated → defaulted to last 6 months ({time.start} … {time.end})."
        )

    if time_explicit and re.search(r"\b(?:dry|wet|rainy|monsoon|growing|summer|winter)\s+seasons?\b", q):
        assumptions.append(
            f"Season dates are not known for this area → analyzed the whole span "
            f"({time.start} … {time.end}), not only those seasons."
        )

    # Baseline for change/anomaly analyses
    baseline: Optional[BaselineSpec] = None
    if analysis_type == AnalysisType.CHANGE:
        span = time.end - time.start
        prev_end = time.start - timedelta(days=1)
        prev_start = prev_end - span
        baseline = BaselineSpec(mode="previous_period",
                                time=TimeRange(start=prev_start, end=prev_end))
        assumptions.append(
            f"Change baseline not stated → using the preceding equal-length window "
            f"({prev_start} … {prev_end})."
        )
    elif analysis_type == AnalysisType.ANOMALY:
        prev = TimeRange(start=_shift_year(time.start, -1),
                         end=_shift_year(time.end, -1))
        baseline = BaselineSpec(mode="previous_year", time=prev)
        assumptions.append(
            f"Anomaly baseline not stated → using the same calendar window one year earlier "
            f"({prev.start} … {prev.end})."
        )

    # AOI resolution + CLARIFY policy. A malformed recipe_id/window is a caller error and
    # takes precedence over AOI resolution — surface it first.
    clarification: Optional[str] = recipe_error or window_error
    if aoi_bbox is not None:
        # An explicit bbox is authoritative — no geocode, provenance recorded.
        aoi = _aoi_from_bbox(aoi_bbox, aoi_text)
        if aoi is None:
            aoi = AOISpec(raw_text=aoi_text or "", provenance="unresolved")
            clarification = ("The bounding box is invalid; give [west, south, east, north] "
                             "in degrees with west<east and south<north.")
    elif aoi_text is None:
        aoi = AOISpec(raw_text="", provenance="unresolved")
        clarification = ("No area of interest found in the question. Name a place "
                         "(e.g. 'Fresno County'), or give a bounding box / coordinates.")
    elif do_geocode:
        resolved = geocode_aoi(aoi_text)
        if resolved is None:
            aoi = AOISpec(raw_text=aoi_text, provenance="unresolved")
            clarification = (f"Could not resolve '{aoi_text}' to a location. "
                             f"Try a more specific place name or a bounding box.")
        else:
            aoi = _clamp_bbox(resolved, assumptions)
    else:
        aoi = AOISpec(raw_text=aoi_text, provenance="unresolved")

    return EOIntent(
        query=query,
        aoi=aoi,
        time=time,
        variable=variable,
        analysis_type=analysis_type,
        baseline=baseline,
        max_cloud_cover_pct=cloud,
        decision_context=decision_context,
        assumptions=assumptions,
        threshold_overrides=thresholds or None,
        # A caller error (bad recipe_id/window) outranks an AOI-resolution message.
        clarification_needed=recipe_error or window_error or clarification,
    )


def _merge_enum(enum_cls, llm_value, current, was_explicit):
    """Overlay an LLM-provided enum value if valid; keep explicit keyword detection otherwise."""
    if llm_value is None:
        return current, was_explicit
    try:
        return enum_cls(str(llm_value).lower()), True
    except ValueError:
        return current, was_explicit
