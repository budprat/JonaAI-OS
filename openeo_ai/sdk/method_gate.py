# ABOUTME: Deterministic checks that a process graph can answer the question asked,
# ABOUTME: applied before a job is created so a wrong method never reaches execution.
"""Pre-execution method checks for agent-built process graphs.

The agent has improvised plausible graphs that could not answer the question: a
"dNBR" job with no subtraction, and a monthly series saved to a format that keeps
only the first period. Both produced credible but wrong numbers.
"""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

_BURN_SEVERITY_RE = re.compile(r"\bdnbr\b|burn severity|fire severity|delta nbr")
_DELTA_NBR_RE = re.compile(r"delta nbr")
_PLAIN_NBR_RE = re.compile(r"\bnbr\b")
_SINGLE_PERIOD_FORMATS = {"gtiff", "geotiff", "png"}
_BAND_DIMENSIONS = {"bands", "band", "spectral"}


def parse_graph(raw: Any) -> Dict[str, Any]:
    """Return the node dict from a graph given as dict, JSON string, or wrapped form."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("process_graph")
    if inner is not None:
        return parse_graph(inner)
    return raw


def _keeps_multiple_periods(graph: Dict[str, Any]) -> bool:
    """True if a single-period output format would receive a temporal series."""
    for node in graph.values():
        if not isinstance(node, dict) or node.get("process_id") != "save_result":
            continue
        args = node.get("arguments", {})
        if str(args.get("format", "")).lower() not in _SINGLE_PERIOD_FORMATS:
            continue
        current = args.get("data")
        seen: Set[str] = set()
        while isinstance(current, dict) and current.get("from_node") in graph:
            name = current["from_node"]
            if name in seen:
                break
            seen.add(name)
            parent = graph[name]
            process_id = parent.get("process_id")
            arguments = parent.get("arguments", {})
            if process_id == "reduce_dimension" and str(arguments.get("dimension")).lower() not in _BAND_DIMENSIONS:
                break
            if process_id in ("aggregate_temporal_period", "aggregate_temporal"):
                return True
            current = arguments.get("data") or arguments.get("x")
    return False


def _start_date(graph: Dict[str, Any], ref: Any) -> Optional[str]:
    """Earliest start of the period a reference covers: its nearest temporal filter on each
    path back, or the load_collection it reaches."""
    pending = [ref.get("from_node")] if isinstance(ref, dict) else []
    seen: Set[str] = set()
    starts = []
    while pending:
        name = pending.pop()
        node = graph.get(name)
        if name in seen or not isinstance(node, dict):
            continue
        seen.add(name)
        arguments = node.get("arguments", {})
        if node.get("process_id") in ("load_collection", "filter_temporal"):
            extent = arguments.get("temporal_extent") or arguments.get("extent")
            if isinstance(extent, list) and extent and extent[0]:
                starts.append(str(extent[0]))
            continue
        pending.extend(
            value["from_node"]
            for value in arguments.values()
            if isinstance(value, dict) and "from_node" in value
        )
    return min(starts) if starts else None


def _subtractions(graph: Dict[str, Any]) -> List[Tuple[Any, Any]]:
    """The (minuend, subtrahend) inputs of each whole-cube subtraction in a graph."""
    pairs = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        arguments = node.get("arguments", {})
        if node.get("process_id") == "subtract":
            pairs.append((arguments.get("x"), arguments.get("y")))
        elif node.get("process_id") == "merge_cubes":
            resolver = arguments.get("overlap_resolver")
            callback = resolver.get("process_graph", {}) if isinstance(resolver, dict) else {}
            for step in callback.values():
                if not isinstance(step, dict) or step.get("process_id") != "subtract":
                    continue
                cubes = {"x": arguments.get("cube1"), "y": arguments.get("cube2")}
                operands = step.get("arguments", {})
                first = operands.get("x", {}).get("from_parameter") if isinstance(operands.get("x"), dict) else None
                second = operands.get("y", {}).get("from_parameter") if isinstance(operands.get("y"), dict) else None
                if first in cubes and second in cubes:
                    pairs.append((cubes[first], cubes[second]))
    return pairs


def compares_periods(raw_graph: Any) -> bool:
    """True if a whole-cube subtraction takes one period from another.

    Band math inside an index also subtracts, and top-level band arithmetic can too, but
    neither compares dates. Periods that can't be traced get the benefit of the doubt.
    """
    graph = parse_graph(raw_graph)
    for first, second in _subtractions(graph):
        start_a, start_b = _start_date(graph, first), _start_date(graph, second)
        if start_a is None or start_b is None or start_a != start_b:
            return True
    return False


def post_minus_pre(raw_graph: Any) -> bool:
    """True if a subtraction takes the earlier period away from the later one."""
    graph = parse_graph(raw_graph)
    for minuend, subtrahend in _subtractions(graph):
        later, earlier = _start_date(graph, minuend), _start_date(graph, subtrahend)
        if later and earlier and later > earlier:
            return True
    return False


def _asks_for_plain_nbr(text: str) -> bool:
    """True if the question also asks for NBR itself, beside burn severity."""
    return bool(_PLAIN_NBR_RE.search(_DELTA_NBR_RE.sub("", text)))


def asks_for_burn_severity(prompt: str) -> bool:
    return bool(_BURN_SEVERITY_RE.search((prompt or "").lower()))


# "dont" and "never" only as an instruction: "We don't run jobs like this" is a statement.
_NO_RUN_LEAD_RE = re.compile(
    r"(?:(?:^|[,:(]|\band\b|\bbut\b|\bplease\b|\bjust\b|\bso\b)\s*(?:do not|dont|never)|\bwithout|\bno need to)\s+"
    r"(?:run|running|rerun|rerunning|creat\w*|start\w*|submit\w*|execut\w*)\b(?P<rest>.*)",
    re.DOTALL,
)
_RUN_OBJECT_RE = re.compile(
    r"\s*(?P<det>(?:(?:any|a|an|the|new|more|another|this|that|satellite|further)\s+)*)"
    r"(?:(?P<noun>anything|nothing|it|jobs?|(?:process\s+)?graphs?|analys[ie]s|quer(?:y|ies)|computations?|processing)\b)?"
)
# What follows a job when the user is limiting it, not ruling it out.
_LIMITS_A_JOB_RE = re.compile(
    r"\s*(?:over|above|larger|bigger|wider|exceeding|longer|more than|out of|twice|else|after|before|while|each|per)\b"
)
_NARROWS_A_JOB_RE = re.compile(r"\s*(?:on|in|with|during|using|from|at|when|if|like|(?:for(?!\s+(?:this|that|now)\b)))\b")
_NO_RUN_PHRASE_RE = re.compile(
    r"\bno new jobs?\b(?!\s+(?:after|beyond))|^\s*from memory\b|\b(?:answer|tell me|respond|reply)\s+from memory\b"
)
# A request for new work elsewhere in the prompt means the "don't" was about something narrower.
_ASKS_FOR_WORK_RE = re.compile(r"\b(?:compute|calculate|map|monitor|analy[sz]e|run|generate|produce)\b")


def _rules_out_running(clause: str) -> bool:
    if _NO_RUN_PHRASE_RE.search(clause):
        return True
    lead = _NO_RUN_LEAD_RE.search(clause)
    if not lead:
        return False
    after_verb = lead.group("rest")
    if _LIMITS_A_JOB_RE.match(after_verb):
        return False
    target = _RUN_OBJECT_RE.match(after_verb)
    noun = target.group("noun")
    rest = after_verb[target.end():]
    if _LIMITS_A_JOB_RE.match(rest):
        return False
    # "anything" and "any job" are absolute. A plain "analysis on cloudy scenes" narrows.
    if noun == "anything" or (noun and (target.group("det") or "").strip().startswith("any")):
        return True
    if not noun and not rest.strip(" .,;:!?)"):
        return True  # "without running", "don't execute"
    return bool(noun) and not _NARROWS_A_JOB_RE.match(rest)


def forbids_execution(prompt: str) -> bool:
    """True if the user clearly said to answer without running any processing.

    Each clause is read on its own. "Don't run jobs over 1 degree" limits a job rather than
    ruling them out, and "without running out of memory" isn't about running at all. When in
    doubt this says no: a wrong yes blocks every job for the turn, while a wrong no leaves
    the instruction with the agent, which can still follow it.
    """
    text = (prompt or "").lower().replace("\u2019", "'").replace("'", "").replace("re-", "re")
    clauses = re.split(r"(?<=[.;!?\n])\s+", text)
    forbidding = [clause for clause in clauses if _rules_out_running(clause)]
    if not forbidding:
        return False
    others = [clause for clause in clauses if clause not in forbidding]
    return not any(_ASKS_FOR_WORK_RE.search(clause) for clause in others)


def graph_fingerprint(raw_graph: Any) -> str:
    """A stable identity for a graph, to notice the same one being resubmitted."""
    return hashlib.sha256(json.dumps(parse_graph(raw_graph), sort_keys=True, default=str).encode()).hexdigest()


def check_graph_method(prompt: str, raw_graph: Any) -> Optional[str]:
    """Return why a graph can't answer the prompt, or None if it passes."""
    graph = parse_graph(raw_graph)
    if not graph:
        return None
    text = (prompt or "").lower()
    burn_question = asks_for_burn_severity(prompt)
    if burn_question and post_minus_pre(graph):
        return (
            "This graph subtracts the earlier period from the later one, so burned land "
            "would read as regrowth: dNBR is pre-fire NBR minus post-fire NBR. Swap the "
            "operands, or build it with openeo_algorithm (algorithm='burn_severity')."
        )
    # A question that also asks for NBR rasters needs those single-period jobs too. The
    # bridge checks at the end of the run that a difference was actually computed.
    if burn_question and not compares_periods(graph) and not _asks_for_plain_nbr(text):
        return (
            "This graph has no subtraction, so it cannot produce dNBR: burn severity is "
            "pre-fire NBR minus post-fire NBR. Build the graph with openeo_algorithm "
            "(algorithm='burn_severity', with separate pre_event and post_event windows). "
            "If that isn't possible, tell the user instead of reporting NBR as severity."
        )
    if _keeps_multiple_periods(graph):
        return (
            "This graph saves a temporal series to a format that keeps only the first "
            "period, so later periods would be silently lost. Reduce the time dimension "
            "before saving, or save as netCDF."
        )
    return None
