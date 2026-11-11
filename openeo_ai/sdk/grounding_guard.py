# ABOUTME: Observe-mode grounding guard. Flags trust-claim numbers (confidence/percent/
# ABOUTME: score/delta) the model states in prose that appear in no tool result. Pure, no I/O.

from __future__ import annotations

import re
from typing import List, NamedTuple

# The narrow set of tokens that mark a number as a *trust* claim — not every number
# (dates, tile counts, band ids). A trailing '%' is itself a trust marker.
# Area/quantity units qualify too: a fabricated hectare table is a trust claim.
# Bare "area" is prose, not a unit ("over the area in the last 30 days") — the units
# carry the signal, so only hectare/ha/km2/km²/acre(s) qualify.
_TRUST_TOKENS = (
    "confidence", "confident", "percent", "score", "grade", "delta",
    "hectare", "hectares", "km2", "km²", "acre", "acres",
)

# "ha" (hectares) counts only when adjacent to a number ("1,651 ha") or glued to it
# ("1,651ha") — a standalone "ha" word false-positives on toponyms like "Ha Long Bay".
_HA_RE = re.compile(r"(?<=\d)\s?ha\b")

# Max character gap between a trust token and a number for them to count as adjacent.
_WINDOW = 25

# Accepts thousands separators ("1,651") only as true 3-digit groups — the (?!\d) guard
# keeps list commas ("[732,1651,2000]") from merging into one number. Strip commas before float().
_NUM_RE = re.compile(r"[-+]?\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|[-+]?\d+(?:\.\d+)?")

# Rounding tolerance when matching a claimed value against a tool number.
_ABS_TOL = 0.01
_REL_TOL = 0.005


class NumericClaim(NamedTuple):
    value: float
    token: str      # the trust marker that qualified it ("%", "confidence", ...)
    context: str    # a short slice of surrounding prose, for the warning payload


def _trust_spans(low: str) -> List[tuple]:
    spans: List[tuple] = []
    for tok in _TRUST_TOKENS:
        start = 0
        while True:
            i = low.find(tok, start)
            if i < 0:
                break
            spans.append((i, i + len(tok), tok))
            start = i + 1
    for m in _HA_RE.finditer(low):
        spans.append((m.start(), m.end(), "ha"))
    return spans


def extract_numeric_claims(text: str) -> List[NumericClaim]:
    """Return the numbers in `text` that read as trust claims: a number with an adjacent
    '%' or within _WINDOW chars of a confidence/score/grade/delta/percent token."""
    low = text.lower()
    trust = _trust_spans(low)
    claims: List[NumericClaim] = []
    for m in _NUM_RE.finditer(text):
        s, e = m.span()
        token = "%" if e < len(text) and text[e] == "%" else None
        if token is None:
            for ts, te, tname in trust:
                gap = s - te if te <= s else ts - e if ts >= e else 0
                if gap <= _WINDOW:
                    token = tname
                    break
        if token is None:
            continue
        try:
            value = float(m.group().replace(",", ""))
        except ValueError:
            continue
        context = text[max(0, s - 12): min(len(text), e + 12)].strip()
        claims.append(NumericClaim(value=value, token=token, context=context))
    return claims


def _grounded(value: float, tool_numbers: List[float]) -> bool:
    # A claim grounds if its value — or its percent/fraction twin — matches a tool
    # number within a rounding tolerance. Tolerant of 87% ↔ 0.87 and small rounding.
    for cand in (value, value / 100.0, value * 100.0):
        tol = max(_ABS_TOL, abs(cand) * _REL_TOL)
        if any(abs(n - cand) <= tol for n in tool_numbers):
            return True
    return False


def check(text: str, tool_results: List) -> List[NumericClaim]:
    """Return the trust claims in `text` that are NOT supported by any tool result.
    Empty tool_results flags every claim (a number with no possible source)."""
    claims = extract_numeric_claims(text)
    if not claims:
        return []
    tool_numbers: List[float] = []
    for tr in tool_results:
        tool_numbers.extend(float(x.replace(",", "")) for x in _NUM_RE.findall(str(tr)))
    return [c for c in claims if not _grounded(c.value, tool_numbers)]


# --- class-table integrity -------------------------------------------------
# Disjoint class shares of one whole cannot exceed 100%. Per-number grounding misses
# a table stitched from several tool results because each number exists *somewhere*;
# the sum is the impossibility proof.

# Whole-cell match only: "67.09%" is a share, "1.5% of 40%" or "dNBR 0.44 (3.57%)" is prose.
# A comma is a thousands separator only in true 3-digit groups, so a comma-decimal
# "12,5%" fails to parse and is skipped rather than read as 125%.
_PCT_CELL_RE = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})*(?!\d)(?:\.\d+)?\s*%$")

# Markdown emphasis around an otherwise-bare cell ("**60.0%**", "`60.0%`") is styling,
# not prose — strip it so an emphasised share is still counted.
_EMPHASIS_CHARS = "*_`~ "

# A cell of only dashes/colons/spaces is a markdown separator row, not data.
_SEP_CELL_RE = re.compile(r"^[-:\s]+$")

# Rows that legitimately re-count the same pixels — excluded from the sum. Matched on
# word boundaries: "consumption" must not read as "sum", "Overall vegetation" is a class.
_RECOUNT_RE = re.compile(
    r"\b(?:total|totals|combined|cumulative|overall|subtotal|sum|aggregate|all classes)\b"
    r"|\bof which\b"
)

# A percentage column is a PARTITION — disjoint shares of ONE whole — only when its
# header says so. Everything else ("Cloud cover %", "% Change vs 2020", "Confidence %")
# is an independent per-row metric whose column legitimately sums past 100%, and
# flagging it would be a false accusation in the user-visible notice.
_PARTITION_HEADER_PHRASES = (
    "% of area", "% of total", "% of pixels", "% of the", "% of scene", "% of aoi",
    "% of region", "% of extent", "% of classes", "percent of", "percentage of",
    "share", "proportion", "area %", "% area", "area percent",
)
# A bare "%"/"percent" header qualifies only when the table also names a class
# dimension. STRONG nouns only: "type"/"zone"/"range"/"bin"/"label"/"cover" appear
# constantly in non-partition tables ("Type" = product level, "Zone" = buffer ring).
_CLASS_COLUMN_TOKENS = (
    "class", "classes", "severity", "category", "land use", "landcover", "land cover",
)
# Never a partition, whatever else the header says. The second row are per-row
# denominators — each row measured against its OWN whole, so the column legitimately
# runs past 100% (per-scene footprints, per-city day counts, per-year land shares).
_NON_PARTITION_TOKENS = (
    "change", "confidence", "cloud", "accuracy", "error", "growth", "delta",
    "difference", " vs", "increase", "decrease", "rate", "margin", "uncertainty",
    "covered", "coverage", "footprint", "overlap", "per scene", "per-scene",
    "of days", "days with", "of total land", "usable", "valid",
)

# A derived roll-up row ("Burned (Low+Moderate+High)") re-counts classes already listed.
_ROLLUP_JOIN = "+"

# Fewer than this many percentage cells in a column is not a class breakdown. Two is
# a real partition (burned/unburned, water/land) — safe now that the header gates it.
_MIN_CLASS_ROWS = 2

# Longest context slice carried in the warning payload.
_CONTEXT_CHARS = 80


class TableSumViolation(NamedTuple):
    column_label: str   # header text of the offending column ("" if unknown)
    total: float        # the summed percentage (e.g. 134.18)
    rows: int           # how many rows were summed
    context: str        # short human slice for the warning payload


def _table_cells(line: str) -> List[str]:
    # "| a | b |" -> ["a", "b"]; the outer pipes are delimiters, not empty cells.
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [c.strip() for c in raw.split("|")]


def _table_blocks(text: str) -> List[List[str]]:
    # Contiguous runs of lines holding >= 2 pipes. A blank or non-table line ends the run.
    # Lines inside a ``` fence are examples/templates, never live results — skipped.
    blocks: List[List[str]] = []
    current: List[str] = []
    fenced = False
    for raw_line in text.splitlines():
        # A quoted table ("> | a | b |") must parse like an unquoted one, or the
        # separator row is unrecognisable and table splitting silently switches off.
        line = re.sub(r"^\s*(?:>\s?)+", "", raw_line)
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            if current:
                blocks.append(current)
                current = []
            continue
        if fenced:
            continue
        if line.count("|") >= 2:
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _is_separator(cells: List[str]) -> bool:
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells if c)


def _split_tables(block: List[str]) -> List[tuple]:
    """Split one pipe-line run into (header_cells, data_rows) per TABLE.

    A separator row marks the row above it as a header, so a second separator means a
    second table started — two adjacent tables (specialist prose joined together) must
    never be summed as one column.
    """
    rows = [_table_cells(line) for line in block]

    # Separator-independent boundary: a data row that repeats the header verbatim is
    # the start of the NEXT table. The live agent emits tables with no separator row
    # at all (see the PALISADES_TABLE fixture), so keying only on separators would
    # leave two adjacent CORRECT tables summed as one impossible column.
    def _same(a: List[str], b: List[str]) -> bool:
        return [c.strip().lower() for c in a] == [c.strip().lower() for c in b]

    starts = [0]
    for i in range(1, len(rows)):
        if _is_separator(rows[i]):
            continue
        if _same(rows[i], rows[starts[-1]]) and i > starts[-1] + 1:
            starts.append(i)

    tables: List[tuple] = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(rows)
        chunk = rows[start:end]
        if len(chunk) < 2:
            continue
        seps = [i for i, cells in enumerate(chunk) if _is_separator(cells)]
        if not seps:
            tables.append((chunk[0], chunk[1:]))
            continue
        for m, sep in enumerate(seps):
            if sep == 0:
                continue  # separator with no header above it
            sub_end = (seps[m + 1] - 1) if m + 1 < len(seps) else len(chunk)
            tables.append((chunk[sep - 1], chunk[sep + 1:sub_end]))
    return tables


def _has_grouping_column(data: List[List[str]], pct_idx: int) -> bool:
    """Is this a grouped ("long-format") table — one facet per row-group?

    "| North | Burned | 60% | / | North | Unburned | 40% | / | South | ... |"
    partitions WITHIN each region, so the column total is n x 100% BY DESIGN. A
    facet column shows the same value on consecutive rows; abstaining on those is
    the safe direction (a miss, never a false accusation).
    """
    if len(data) < 3:
        return False
    width = max((len(r) for r in data), default=0)
    for idx in range(width):
        if idx == pct_idx:
            continue
        vals = [(r[idx].strip().lower() if idx < len(r) else "") for r in data]
        if any(not v for v in vals) or len(set(vals)) == len(vals):
            continue
        if any(vals[i] == vals[i + 1] for i in range(len(vals) - 1)):
            return True
    return False


def _is_partition_column(header: List[str], idx: int) -> bool:
    """Is this column disjoint shares of ONE whole (so >100% is impossible)?

    Header-gated on purpose: a per-row metric column ("Cloud cover %", "% Change vs
    2020") legitimately sums past 100%, and flagging it would put a false accusation
    in the user-visible notice. Precision over recall.
    """
    label = (header[idx] if idx < len(header) else "").strip().lower()
    if not label:
        return False
    if any(tok in label for tok in _NON_PARTITION_TOKENS):
        return False
    if any(phrase in label for phrase in _PARTITION_HEADER_PHRASES):
        return True
    # A bare "%" column counts only when the table also names a class dimension.
    if label.strip("% ") in ("", "pct", "percent", "percentage"):
        others = " ".join(h.lower() for i, h in enumerate(header) if i != idx)
        return any(tok in others for tok in _CLASS_COLUMN_TOKENS)
    return False


def _check_table(header: List[str], data: List[List[str]], context: str,
                 tolerance: float) -> List[TableSumViolation]:
    columns: dict = {}
    for cells in data:
        if _is_separator(cells):
            continue
        joined = " ".join(cells).lower()
        # Summary rows re-count classes already listed, by name ("Total") or by
        # construction ("Burned (Low+Moderate+High)").
        if _RECOUNT_RE.search(joined) or _ROLLUP_JOIN in joined:
            continue
        for idx, cell in enumerate(cells):
            bare = cell.strip(_EMPHASIS_CHARS)
            if not _PCT_CELL_RE.match(bare):
                continue
            try:
                value = float(bare.rstrip("% ").replace(",", ""))
            except ValueError:
                continue
            columns.setdefault(idx, []).append(value)

    violations: List[TableSumViolation] = []
    for idx in sorted(columns):
        values = columns[idx]
        if len(values) < _MIN_CLASS_ROWS:
            continue
        if not _is_partition_column(header, idx):
            continue
        if _has_grouping_column(data, idx):
            continue
        # Whole-percent rounding can cost up to half a point per class.
        eff_tol = max(tolerance, 0.5 * len(values))
        total = sum(values)
        # Under 100 is legitimate — a table showing a subset of classes. Only over-sum lies.
        if total <= 100.0 + eff_tol:
            continue
        label = header[idx].strip() if idx < len(header) else ""
        violations.append(
            TableSumViolation(
                column_label=label, total=round(total, 2), rows=len(values), context=context
            )
        )
    return violations


def _check_block(block: List[str], tolerance: float) -> List[TableSumViolation]:
    context = block[0].strip()[:_CONTEXT_CHARS]
    violations: List[TableSumViolation] = []
    for header, data in _split_tables(block):
        violations.extend(_check_table(header, data, context, tolerance))
    return violations


def check_class_table_sums(text: str, tolerance: float = 2.0) -> List[TableSumViolation]:
    """Return one violation per markdown-table column whose class percentages sum above
    100% + tolerance. Disjoint shares of one whole cannot exceed 100%."""
    violations: List[TableSumViolation] = []
    try:
        for block in _table_blocks(text or ""):
            violations.extend(_check_block(block, tolerance))
    except Exception:
        # Observe-mode guard: malformed prose must never break the answer path.
        return []
    return violations
