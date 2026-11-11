# ABOUTME: Workflow template CRUD operations against SQLite.
# Handles saving, loading, versioning, and parameterizing analysis templates.

"""
Workflow Template Store

Manages lifecycle of reusable workflow templates:
- Save a completed analysis as a template
- Parameterize which fields are variable (location, dates) vs fixed (index, thresholds)
- Version templates on update
- Track usage counts
"""

import uuid
import json
import hashlib
import copy
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

from openeo_ai import _pg_compat as sqlite3  # PostgreSQL shim with sqlite3 API

from .models import (
    WorkflowTemplateCreate,
    WorkflowTemplateResponse,
    TemplateParameter,
    ParameterType,
    LearnedPattern,
    LearnedTrajectory,
    SCHEMA_SQL,
    init_batch_db,
)


class TemplateStore:
    """SQLite-backed store for workflow templates."""

    def __init__(self, db_path: str = "data/openeo_ai.db"):
        self.db_path = db_path
        # For :memory: databases, reuse one connection (each new conn is empty)
        self._shared_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:")
            self._shared_conn.row_factory = sqlite3.Row
            self._shared_conn.execute("PRAGMA foreign_keys=ON")
            self._shared_conn.executescript(SCHEMA_SQL)
            self._shared_conn.commit()
        elif sqlite3.uses_sqlite_backend(db_path):
            self._init_sqlite_db(db_path)
        else:
            init_batch_db(db_path)

    @staticmethod
    def _init_sqlite_db(db_path: str) -> None:
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        if self._shared_conn is not None:
            return self._shared_conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        """Close connection unless it's the shared :memory: connection."""
        if conn is not self._shared_conn:
            conn.close()

    # ── Create ──

    def create_template(
        self,
        data: WorkflowTemplateCreate,
        user_id: str,
    ) -> WorkflowTemplateResponse:
        """Save a new workflow template.

        Args:
            data: Template creation payload.
            user_id: Authenticated user ID.

        Returns:
            Created template response.
        """
        template_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO workflow_templates
                    (id, name, description, process_graph, parameters, tags,
                     source_query, collection_id, index_type,
                     version, user_id, use_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0, ?, ?)
                """,
                (
                    template_id,
                    data.name,
                    data.description,
                    json.dumps(data.process_graph),
                    json.dumps([p.model_dump() for p in data.parameters]),
                    json.dumps(data.tags),
                    data.source_query,
                    data.collection_id,
                    data.index_type,
                    user_id,
                    now,
                    now,
                ),
            )
            conn.commit()
            return self.get_template(template_id)
        finally:
            self._close_conn(conn)

    # ── Read ──

    def get_template(self, template_id: str) -> Optional[WorkflowTemplateResponse]:
        """Load a template by ID."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM workflow_templates WHERE id = ?", (template_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_response(row)
        finally:
            self._close_conn(conn)

    def list_templates(
        self,
        user_id: str,
        *,
        tag: Optional[str] = None,
        collection_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[WorkflowTemplateResponse], int]:
        """List templates with optional filtering.

        Returns:
            Tuple of (templates list, total count).
        """
        conn = self._conn()
        try:
            conditions = ["(user_id = ? OR is_public = 1)"]
            params: List[Any] = [user_id]

            if tag:
                # JSON array search: tags like '%"tag_name"%'
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

            if collection_id:
                conditions.append("collection_id = ?")
                params.append(collection_id)

            where = " AND ".join(conditions)

            count_row = conn.execute(
                f"SELECT COUNT(*) FROM workflow_templates WHERE {where}", params
            ).fetchone()
            total = count_row[0]

            rows = conn.execute(
                f"""
                SELECT * FROM workflow_templates
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()

            return [self._row_to_response(r) for r in rows], total
        finally:
            self._close_conn(conn)

    # ── Update ──

    def update_template(
        self,
        template_id: str,
        user_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        process_graph: Optional[Dict[str, Any]] = None,
        parameters: Optional[List[TemplateParameter]] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[WorkflowTemplateResponse]:
        """Update a template, bumping its version.

        Only the owning user can update their template.
        """
        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT * FROM workflow_templates WHERE id = ? AND user_id = ?",
                (template_id, user_id),
            ).fetchone()
            if not existing:
                return None

            now = datetime.utcnow().isoformat()
            updates = ["version = version + 1", "updated_at = ?"]
            params: List[Any] = [now]

            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            if process_graph is not None:
                updates.append("process_graph = ?")
                params.append(json.dumps(process_graph))
            if parameters is not None:
                updates.append("parameters = ?")
                params.append(json.dumps([p.model_dump() for p in parameters]))
            if tags is not None:
                updates.append("tags = ?")
                params.append(json.dumps(tags))

            params.append(template_id)

            conn.execute(
                f"UPDATE workflow_templates SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
            return self.get_template(template_id)
        finally:
            self._close_conn(conn)

    # ── Delete ──

    def delete_template(self, template_id: str, user_id: str) -> bool:
        """Delete a template owned by user_id."""
        conn = self._conn()
        try:
            cursor = conn.execute(
                "DELETE FROM workflow_templates WHERE id = ? AND user_id = ?",
                (template_id, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    # ── Usage tracking ──

    def increment_use_count(self, template_id: str) -> None:
        """Bump the use counter when a template is executed."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE workflow_templates SET use_count = use_count + 1 WHERE id = ?",
                (template_id,),
            )
            conn.commit()
        finally:
            self._close_conn(conn)

    # ── Graph parameterization ──

    @staticmethod
    def apply_parameters(
        process_graph: Dict[str, Any],
        parameters: List[TemplateParameter],
        overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply parameter overrides to a process graph.

        Walks the graph and replaces parameter placeholders with provided values.
        Placeholders in the graph use the format: {"$parameter": "param_name"}

        Args:
            process_graph: The template's process graph (will not be mutated).
            parameters: Template parameter definitions.
            overrides: User-supplied values keyed by parameter name.

        Returns:
            A new process graph with placeholders resolved.

        Raises:
            ValueError: If a required parameter is missing from overrides.
        """
        # Validate required parameters
        param_map = {p.name: p for p in parameters}
        for p in parameters:
            if p.required and p.name not in overrides and p.default_value is None:
                raise ValueError(
                    f"Required parameter '{p.name}' not provided and has no default"
                )

        # Build effective values: override > default
        effective = {}
        for p in parameters:
            if p.name in overrides:
                effective[p.name] = overrides[p.name]
            elif p.default_value is not None:
                effective[p.name] = p.default_value

        # Deep-copy and substitute
        graph = copy.deepcopy(process_graph)
        return _substitute_placeholders(graph, effective)

    @staticmethod
    def auto_detect_parameters(process_graph: Dict[str, Any]) -> List[TemplateParameter]:
        """Auto-detect parameterizable fields from a process graph.

        Scans for spatial_extent, temporal_extent, and threshold arguments
        that are likely candidates for parameterization.

        Returns:
            List of suggested TemplateParameter definitions.
        """
        params = []
        _scan_graph_for_params(process_graph, params, set())
        return params

    # ── Internal ──

    def _row_to_response(self, row: sqlite3.Row) -> WorkflowTemplateResponse:
        raw_params = sqlite3.json_loads(row["parameters"], default=[])
        params = [TemplateParameter(**p) for p in raw_params]
        return WorkflowTemplateResponse(
            id=row["id"],
            is_public=bool(row["is_public"]),
            name=row["name"],
            description=row["description"],
            process_graph=sqlite3.json_loads(row["process_graph"], default={}),
            parameters=params,
            tags=sqlite3.json_loads(row["tags"], default=[]),
            source_query=row["source_query"],
            collection_id=row["collection_id"],
            index_type=row["index_type"],
            version=row["version"],
            user_id=row["user_id"],
            use_count=row["use_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── Learned Patterns ──

    @staticmethod
    def _compute_pattern_hash(process_graph: Dict[str, Any]) -> str:
        """Compute a structural hash of a process graph.

        Normalizes by zeroing out spatial_extent values, temporal_extent
        values, and save_result path so that structurally identical graphs
        hash the same regardless of location/time.
        """
        normalized = copy.deepcopy(process_graph)
        _normalize_graph(normalized)
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _extract_graph_metadata(process_graph: Dict[str, Any]) -> Dict[str, Any]:
        """Extract metadata from a process graph for pattern classification.

        Returns dict with: collection_id, index_type, cloud_masked,
        time_reducer, temporal_strategy.
        """
        meta: Dict[str, Any] = {
            "collection_id": "",
            "index_type": "",
            "cloud_masked": False,
            "time_reducer": "median",
            "temporal_strategy": "single_snapshot",
        }

        load_count = 0
        has_aggregate_temporal = False

        for node in process_graph.values():
            if not isinstance(node, dict):
                continue
            pid = node.get("process_id", "")
            args = node.get("arguments", {})

            # Collection ID
            if pid == "load_collection" and "id" in args:
                if not meta["collection_id"]:
                    meta["collection_id"] = args["id"]
                load_count += 1

            # Index type detection
            if pid == "normalized_difference":
                # Try to infer index from band args
                meta["index_type"] = _infer_index_from_bands(args)
            elif pid in ("ndvi", "ndwi", "nbr", "evi", "savi", "ndbi", "ndsi", "mndwi"):
                meta["index_type"] = pid

            # Cloud mask detection
            if pid == "mask":
                meta["cloud_masked"] = True

            # Time reducer detection
            if pid == "reduce_dimension":
                reducer = args.get("reducer", {})
                if isinstance(reducer, dict):
                    # Walk sub-graph to find the reducer process
                    for sub_node in reducer.values():
                        if isinstance(sub_node, dict):
                            sub_pid = sub_node.get("process_id", "")
                            if sub_pid in ("median", "mean", "max", "min", "sum", "count"):
                                meta["time_reducer"] = sub_pid

            # Temporal aggregation detection
            if pid == "aggregate_temporal_period":
                has_aggregate_temporal = True

        # Temporal strategy
        if load_count >= 2:
            meta["temporal_strategy"] = "change_detection"
        elif has_aggregate_temporal:
            meta["temporal_strategy"] = "time_series"

        return meta

    def capture_pattern(
        self,
        process_graph: Dict[str, Any],
        source_query: str = "",
        domain: str = "",
    ) -> Optional[str]:
        """Capture a workflow pattern from a successful job execution.

        Args:
            process_graph: The OpenEO process graph that succeeded.
            source_query: Original job title / natural language query.
            domain: Optional domain classification.

        Returns:
            Pattern ID if newly created, None if duplicate (use_count bumped).
        """
        pattern_hash = self._compute_pattern_hash(process_graph)
        now = datetime.utcnow().isoformat()

        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT id FROM learned_patterns WHERE pattern_hash = ?",
                (pattern_hash,),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE learned_patterns SET use_count = use_count + 1, last_used_at = ? WHERE pattern_hash = ?",
                    (now, pattern_hash),
                )
                conn.commit()
                return None

            # New pattern — extract metadata and insert
            meta = self._extract_graph_metadata(process_graph)
            if not domain:
                # Attempt domain classification from source_query
                try:
                    from ..sdk.domain_knowledge import classify_domain
                    domain = classify_domain(source_query) or ""
                except Exception:
                    domain = ""

            # Override extracted fields if metadata found them
            index_type = meta["index_type"]
            collection_id = meta["collection_id"]

            summary = _build_summary(
                index_type=index_type,
                collection_id=collection_id,
                temporal_strategy=meta["temporal_strategy"],
                cloud_masked=meta["cloud_masked"],
                time_reducer=meta["time_reducer"],
                source_query=source_query,
            )

            pattern_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO learned_patterns
                    (id, pattern_hash, domain, index_type, collection_id,
                     temporal_strategy, cloud_masked, time_reducer,
                     source_query, summary, process_graph,
                     use_count, success_count, created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    pattern_id,
                    pattern_hash,
                    domain,
                    index_type,
                    collection_id,
                    meta["temporal_strategy"],
                    1 if meta["cloud_masked"] else 0,
                    meta["time_reducer"],
                    source_query,
                    summary,
                    json.dumps(process_graph),
                    now,
                    now,
                ),
            )
            conn.commit()
            return pattern_id
        finally:
            self._close_conn(conn)

    def find_relevant_patterns(
        self,
        domain: str = "",
        index_type: str = "",
        collection_id: str = "",
        limit: int = 3,
    ) -> List[LearnedPattern]:
        """Find learned patterns relevant to a query context.

        Uses weighted scoring: domain=3, index_type=2, collection_id=1.
        Returns patterns sorted by relevance score, then use_count.
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT *,
                    (CASE WHEN domain = ? AND domain != '' THEN 3 ELSE 0 END +
                     CASE WHEN index_type = ? AND index_type != '' THEN 2 ELSE 0 END +
                     CASE WHEN collection_id = ? AND collection_id != '' THEN 1 ELSE 0 END) AS score
                FROM learned_patterns
                WHERE (domain = ? AND domain != '')
                   OR (index_type = ? AND index_type != '')
                   OR (collection_id = ? AND collection_id != '')
                ORDER BY score DESC, use_count DESC, last_used_at DESC
                LIMIT ?
                """,
                (domain, index_type, collection_id,
                 domain, index_type, collection_id,
                 limit),
            ).fetchall()

            return [_row_to_learned_pattern(r) for r in rows]
        finally:
            self._close_conn(conn)

    def get_injection_text(self, user_prompt: str) -> Optional[str]:
        """Generate injection text for the system prompt based on user query.

        Classifies the prompt, finds relevant patterns, and formats them
        into a concise text block (~200 tokens max).

        Returns:
            Formatted text block to append to system prompt, or None.
        """
        # Classify domain from prompt
        try:
            from ..sdk.domain_knowledge import classify_domain
            domain = classify_domain(user_prompt) or ""
        except Exception:
            domain = ""

        # Detect index from prompt keywords
        index_type = _detect_index_from_prompt(user_prompt)

        # Detect collection from prompt keywords
        collection_id = _detect_collection_from_prompt(user_prompt)

        # If nothing detected, skip retrieval
        if not domain and not index_type and not collection_id:
            return None

        patterns = self.find_relevant_patterns(
            domain=domain,
            index_type=index_type,
            collection_id=collection_id,
        )

        if not patterns:
            return None

        # Format concise injection block
        lines = [
            "\n## Learned Workflow Patterns",
            "These patterns were learned from previously successful jobs. "
            "Use them as reference when building similar workflows:\n",
        ]
        for i, p in enumerate(patterns, 1):
            cloud = "yes" if p.cloud_masked else "no"
            lines.append(
                f"Pattern {i}: {p.summary} (used {p.use_count}x)\n"
                f"  Collection: {p.collection_id} | Index: {p.index_type} | "
                f"Strategy: {p.temporal_strategy}\n"
                f"  Cloud mask: {cloud} | Reducer: {p.time_reducer}"
            )

        return "\n".join(lines)

    # ── Learned Trajectories ──

    @staticmethod
    def _compute_trajectory_hash(domain: str, tool_sequence: List[str]) -> str:
        """Compute a dedup hash from domain + sorted tool set.

        Different orderings of the same tools within the same domain
        hash to the same value — they represent one "pattern".
        """
        canonical = domain + "|" + ",".join(sorted(tool_sequence))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def capture_trajectory(
        self,
        tool_sequence: List[str],
        source_query: str = "",
        domain: str = "",
        outcome_score: float = 0.0,
    ) -> Optional[str]:
        """Capture a successful tool sequence as a learned trajectory.

        Deduplicates by domain + sorted tool_sequence hash.
        If duplicate: bumps use_count, updates outcome_score if higher.
        If new: inserts new entry.

        Args:
            tool_sequence: Ordered list of tool names used.
            source_query: Original user prompt.
            domain: Domain classification (fire, vegetation, etc.).
            outcome_score: Quality score for this trajectory (0.0-1.0).

        Returns:
            Trajectory ID if newly created, None if duplicate (use_count bumped).
        """
        if not tool_sequence:
            return None

        seq_hash = self._compute_trajectory_hash(domain, tool_sequence)
        now = datetime.utcnow().isoformat()

        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT id, outcome_score FROM learned_trajectories WHERE sequence_hash = ?",
                (seq_hash,),
            ).fetchone()

            if existing:
                # Bump use_count; update score only if higher
                update_score = max(existing["outcome_score"], outcome_score)
                conn.execute(
                    """UPDATE learned_trajectories
                       SET use_count = use_count + 1,
                           last_used_at = ?,
                           outcome_score = ?
                       WHERE sequence_hash = ?""",
                    (now, update_score, seq_hash),
                )
                conn.commit()
                return None

            # New trajectory — insert
            trajectory_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO learned_trajectories
                    (id, sequence_hash, domain, tool_sequence,
                     source_query, outcome_score, use_count,
                     created_at, last_used_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    trajectory_id,
                    seq_hash,
                    domain,
                    json.dumps(tool_sequence),
                    source_query,
                    outcome_score,
                    now,
                    now,
                ),
            )
            conn.commit()
            return trajectory_id
        finally:
            self._close_conn(conn)

    def find_relevant_trajectories(
        self,
        domain: str = "",
        limit: int = 3,
    ) -> List[LearnedTrajectory]:
        """Find successful trajectories for a domain.

        Returns TOP trajectories by outcome_score, diversified:
        - No two returned trajectories should have identical tool_sequence
        - Prefers higher outcome_score
        - Breaks ties by use_count

        Args:
            domain: Domain to filter by. If empty, returns top across all.
            limit: Maximum number of trajectories to return.

        Returns:
            List of LearnedTrajectory objects.
        """
        conn = self._conn()
        try:
            if domain:
                rows = conn.execute(
                    """SELECT * FROM learned_trajectories
                       WHERE domain = ?
                       ORDER BY outcome_score DESC, use_count DESC, last_used_at DESC
                       LIMIT ?""",
                    (domain, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM learned_trajectories
                       ORDER BY outcome_score DESC, use_count DESC, last_used_at DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()

            return [_row_to_learned_trajectory(r) for r in rows]
        finally:
            self._close_conn(conn)

    def get_trajectory_injection(self, user_prompt: str) -> Optional[str]:
        """Generate trajectory-based injection text for the system prompt.

        Unlike get_injection_text() which shows process graph structures,
        this shows diverse successful TOOL SEQUENCES the agent has used before.

        Args:
            user_prompt: The user's query to match against.

        Returns:
            Formatted text block, or None if no relevant trajectories.
        """
        # Classify domain from prompt
        try:
            from ..sdk.domain_knowledge import classify_domain
            domain = classify_domain(user_prompt) or ""
        except Exception:
            domain = ""

        if not domain:
            return None

        trajectories = self.find_relevant_trajectories(domain=domain)

        if not trajectories:
            return None

        lines = [
            "\n## Learned Tool Sequences",
            "For similar queries, these tool sequences have succeeded:\n",
        ]
        for i, t in enumerate(trajectories, 1):
            seq_str = " -> ".join(t.tool_sequence)
            lines.append(
                f"{i}. {seq_str} (score: {t.outcome_score:.2f}, used {t.use_count}x)"
            )

        lines.append("\nChoose whichever approach best fits the current query.")
        return "\n".join(lines)


# ── Helpers ──


def _normalize_graph(obj: Any) -> None:
    """Zero out variable fields in a process graph for structural hashing."""
    if isinstance(obj, dict):
        # Zero out spatial_extent values
        if "spatial_extent" in obj and isinstance(obj["spatial_extent"], dict):
            for k in list(obj["spatial_extent"].keys()):
                if isinstance(obj["spatial_extent"][k], (int, float)):
                    obj["spatial_extent"][k] = 0
        # Zero out temporal_extent values
        if "temporal_extent" in obj and isinstance(obj["temporal_extent"], list):
            obj["temporal_extent"] = ["", ""]
        # Zero out save_result path
        if obj.get("process_id") == "save_result":
            args = obj.get("arguments", {})
            if "options" in args and isinstance(args["options"], dict):
                if "filename" in args["options"]:
                    args["options"]["filename"] = ""
        for v in obj.values():
            _normalize_graph(v)
    elif isinstance(obj, list):
        for item in obj:
            _normalize_graph(item)


def _infer_index_from_bands(args: Dict[str, Any]) -> str:
    """Try to infer spectral index type from normalized_difference band args."""
    x = args.get("x", {})
    y = args.get("y", {})
    # Reason: normalized_difference nodes reference bands via array_element
    # The label arg tells us which bands are used
    bands = set()
    for ref in (x, y):
        if isinstance(ref, dict):
            label = ref.get("arguments", {}).get("label", "")
            if label:
                bands.add(label.upper())

    # Common band combinations → index type
    band_pairs = frozenset(bands)
    INDEX_BAND_MAP = {
        frozenset({"B08", "B04"}): "ndvi",
        frozenset({"B8A", "B04"}): "ndvi",
        frozenset({"NIR", "RED"}): "ndvi",
        frozenset({"B08", "B03"}): "ndwi",
        frozenset({"B03", "B11"}): "mndwi",
        frozenset({"NIR", "GREEN"}): "ndwi",
        frozenset({"B08", "B12"}): "nbr",
        frozenset({"NIR", "SWIR2"}): "nbr",
        frozenset({"B11", "B08"}): "ndbi",
        frozenset({"SWIR1", "NIR"}): "ndbi",
        frozenset({"B03", "B11"}): "ndsi",
        frozenset({"GREEN", "SWIR1"}): "ndsi",
    }
    return INDEX_BAND_MAP.get(band_pairs, "")


def _build_summary(
    index_type: str,
    collection_id: str,
    temporal_strategy: str,
    cloud_masked: bool,
    time_reducer: str,
    source_query: str,
) -> str:
    """Build a 1-line summary for a learned pattern."""
    parts = []
    if index_type:
        parts.append(index_type.upper())
    strategy_label = {
        "single_snapshot": "snapshot",
        "change_detection": "change detection",
        "time_series": "time series",
    }.get(temporal_strategy, temporal_strategy)
    parts.append(strategy_label)
    if cloud_masked:
        parts.append("cloud-masked")
    parts.append(f"{time_reducer} reducer")
    if collection_id:
        parts.append(f"from {collection_id}")
    summary = ", ".join(parts)
    # Truncate source_query for context
    if source_query:
        truncated = source_query[:60] + ("..." if len(source_query) > 60 else "")
        summary += f" — \"{truncated}\""
    return summary


def _detect_index_from_prompt(prompt: str) -> str:
    """Detect spectral index from user prompt keywords."""
    prompt_lower = prompt.lower()
    # Check longer names first to avoid substring collision
    INDEX_KEYWORDS = [
        ("gndvi", "gndvi"), ("mndwi", "mndwi"), ("ndbi", "ndbi"),
        ("ndsi", "ndsi"), ("ndmi", "ndmi"), ("ndwi", "ndwi"),
        ("ndvi", "ndvi"), ("nbr", "nbr"), ("evi", "evi"),
        ("savi", "savi"),
    ]
    for keyword, index in INDEX_KEYWORDS:
        if keyword in prompt_lower:
            return index
    return ""


def _detect_collection_from_prompt(prompt: str) -> str:
    """Detect collection from user prompt keywords."""
    prompt_lower = prompt.lower()
    if "landsat" in prompt_lower:
        return "landsat-c2-l2"
    if "sentinel-2" in prompt_lower or "sentinel 2" in prompt_lower or "s2" in prompt_lower:
        return "sentinel-2-l2a"
    if "sentinel-1" in prompt_lower or "sentinel 1" in prompt_lower or "s1" in prompt_lower:
        return "sentinel-1-grd"
    return ""


def _row_to_learned_trajectory(row: sqlite3.Row) -> LearnedTrajectory:
    """Convert a row to LearnedTrajectory model."""
    return LearnedTrajectory(
        id=row["id"],
        domain=row["domain"],
        tool_sequence=sqlite3.json_loads(row["tool_sequence"], default=[]),
        source_query=row["source_query"],
        outcome_score=row["outcome_score"],
        use_count=row["use_count"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
    )


def _row_to_learned_pattern(row: sqlite3.Row) -> LearnedPattern:
    """Convert a SQLite Row to LearnedPattern model."""
    return LearnedPattern(
        id=row["id"],
        domain=row["domain"],
        index_type=row["index_type"],
        collection_id=row["collection_id"],
        temporal_strategy=row["temporal_strategy"],
        cloud_masked=bool(row["cloud_masked"]),
        time_reducer=row["time_reducer"],
        source_query=row["source_query"],
        summary=row["summary"],
        use_count=row["use_count"],
        success_count=row["success_count"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
    )


# ── Existing Helpers ──

def _substitute_placeholders(obj: Any, values: Dict[str, Any]) -> Any:
    """Recursively substitute {"$parameter": "name"} placeholders."""
    if isinstance(obj, dict):
        if "$parameter" in obj and len(obj) == 1:
            param_name = obj["$parameter"]
            if param_name in values:
                return values[param_name]
            return obj  # Leave unresolved if no value
        return {k: _substitute_placeholders(v, values) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_substitute_placeholders(item, values) for item in obj]
    return obj


def _scan_graph_for_params(
    obj: Any,
    params: List[TemplateParameter],
    seen_names: set,
) -> None:
    """Walk a process graph and detect parameterizable fields."""
    if isinstance(obj, dict):
        args = obj.get("arguments", {})

        # spatial_extent
        if "spatial_extent" in args and isinstance(args["spatial_extent"], dict):
            if "spatial_extent" not in seen_names:
                seen_names.add("spatial_extent")
                params.append(TemplateParameter(
                    name="spatial_extent",
                    type=ParameterType.SPATIAL_EXTENT,
                    description="Geographic bounding box (west, south, east, north)",
                    required=True,
                ))

        # temporal_extent
        if "temporal_extent" in args:
            if "temporal_extent" not in seen_names:
                seen_names.add("temporal_extent")
                params.append(TemplateParameter(
                    name="temporal_extent",
                    type=ParameterType.TEMPORAL_EXTENT,
                    description="Date range [start, end] in ISO format",
                    required=True,
                ))

        # Recurse into all dict values
        for v in obj.values():
            _scan_graph_for_params(v, params, seen_names)

    elif isinstance(obj, list):
        for item in obj:
            _scan_graph_for_params(item, params, seen_names)
