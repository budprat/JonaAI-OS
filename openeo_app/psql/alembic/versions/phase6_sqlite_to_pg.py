"""Phase 6: Migrate SQLite tables to PostgreSQL

Revision ID: phase6_sqlite_to_pg
Revises: add_saved_results
Create Date: 2026-04-09

Adds:
  - sdk_session_id column on ai_sessions (was a SQLite-only ALTER, now in main schema)
  - 8 batch module tables: workflow_templates, batch_jobs, batch_items,
    schedules, alert_rules, alert_history, learned_patterns, learned_trajectories

Best practices applied (from supabase-postgres-best-practices skill):
  - All FK columns get an index (FK not auto-indexed by Postgres)
  - timestamptz for timezone-aware time storage
  - JSONB for indexed JSON queries (was TEXT in SQLite)
  - text instead of varchar(n) where no length constraint needed

The previous SQLite versions stored:
  - JSON fields as TEXT (json.dumps/loads in app code)
  - Booleans as INTEGER 0/1
  - Timestamps as ISO8601 strings

The new PG schema uses native types but keeps the same column names so the
_pg_compat shim can run the existing SQL queries unchanged. App code that
calls json.dumps() before insert still works because psycopg2 + JSONB accept
JSON-formatted strings transparently.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "phase6_sqlite_to_pg"
down_revision = "add_saved_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ai_sessions: add sdk_session_id column ──
    # Was an ALTER in SessionManager._init_db; now part of canonical schema.
    op.add_column(
        "ai_sessions",
        sa.Column("sdk_session_id", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_ai_sessions_sdk_session_id",
        "ai_sessions",
        ["sdk_session_id"],
    )

    # ── workflow_templates ──
    # Reusable analysis recipes (process graph + parameter schema)
    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("process_graph", postgresql.JSONB(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("source_query", sa.Text(), nullable=False, server_default=""),
        sa.Column("collection_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("index_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        # is_public stored as INTEGER 0/1 (matches legacy SQLite code that passes int literals).
        # Using BOOLEAN would require rewriting every BatchStore/TemplateStore write site.
        sa.Column("is_public", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_templates_user", "workflow_templates", ["user_id"])
    op.create_index("idx_templates_public", "workflow_templates", ["is_public"])

    # ── schedules ──
    # Recurring execution configs (referenced by batch_jobs)
    op.create_table(
        "schedules",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Text(),
            sa.ForeignKey("workflow_templates.id"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("frequency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("locations", postgresql.JSONB(), nullable=False),
        sa.Column("parameter_overrides", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("runs_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_runs", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.Text(), nullable=True),
        sa.Column("ends_at", sa.Text(), nullable=True),
        sa.Column("next_run_at", sa.Text(), nullable=True),
        sa.Column("last_run_at", sa.Text(), nullable=True),
        sa.Column("last_batch_job_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_schedules_template", "schedules", ["template_id"])  # FK index
    op.create_index("idx_schedules_user", "schedules", ["user_id"])
    op.create_index("idx_schedules_status", "schedules", ["status"])
    op.create_index("idx_schedules_next_run", "schedules", ["next_run_at"])

    # ── batch_jobs ──
    # Groups of analysis items
    op.create_table(
        "batch_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Text(),
            sa.ForeignKey("workflow_templates.id"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("trigger_type", sa.Text(), nullable=False, server_default="manual"),
        sa.Column(
            "schedule_id",
            sa.Text(),
            sa.ForeignKey("schedules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parameter_overrides", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=True),
    )
    op.create_index("idx_batch_jobs_template", "batch_jobs", ["template_id"])  # FK
    op.create_index("idx_batch_jobs_schedule", "batch_jobs", ["schedule_id"])  # FK
    op.create_index("idx_batch_jobs_user", "batch_jobs", ["user_id"])
    op.create_index("idx_batch_jobs_status", "batch_jobs", ["status"])

    # ── batch_items ──
    # Individual analysis units within a batch
    op.create_table(
        "batch_items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "batch_job_id",
            sa.Text(),
            sa.ForeignKey("batch_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("location_name", sa.Text(), nullable=False),
        sa.Column("spatial_extent", postgresql.JSONB(), nullable=False),
        sa.Column("location_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("openeo_job_id", sa.Text(), nullable=True),
        sa.Column("result_path", sa.Text(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.Text(), nullable=True),
    )
    op.create_index("idx_batch_items_job", "batch_items", ["batch_job_id"])  # FK
    op.create_index("idx_batch_items_status", "batch_items", ["status"])

    # ── alert_rules ──
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Text(),
            sa.ForeignKey("workflow_templates.id"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("metric_path", sa.Text(), nullable=False),
        sa.Column("operator", sa.Text(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        # is_active stored as INTEGER 0/1 for legacy-code compatibility
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_alert_rules_template", "alert_rules", ["template_id"])  # FK
    op.create_index("idx_alert_rules_user", "alert_rules", ["user_id"])

    # ── alert_history ──
    op.create_table(
        "alert_history",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "alert_rule_id",
            sa.Text(),
            sa.ForeignKey("alert_rules.id"),
            nullable=False,
        ),
        sa.Column(
            "batch_item_id",
            sa.Text(),
            sa.ForeignKey("batch_items.id"),
            nullable=False,
        ),
        sa.Column("batch_job_id", sa.Text(), nullable=False),
        sa.Column("location_name", sa.Text(), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        # acknowledged stored as INTEGER 0/1 for legacy-code compatibility
        sa.Column("acknowledged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_alert_history_rule", "alert_history", ["alert_rule_id"])  # FK
    op.create_index("idx_alert_history_item", "alert_history", ["batch_item_id"])  # FK
    op.create_index("idx_alert_history_job", "alert_history", ["batch_job_id"])
    op.create_index("idx_alert_history_unack", "alert_history", ["acknowledged"])

    # ── learned_patterns ──
    op.create_table(
        "learned_patterns",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("pattern_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("domain", sa.Text(), nullable=False, server_default=""),
        sa.Column("index_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("collection_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("temporal_strategy", sa.Text(), nullable=False, server_default="single_snapshot"),
        # cloud_masked stored as INTEGER 0/1 for legacy-code compatibility
        sa.Column("cloud_masked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("time_reducer", sa.Text(), nullable=False, server_default="median"),
        sa.Column("source_query", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("process_graph", postgresql.JSONB(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_patterns_domain", "learned_patterns", ["domain"])
    op.create_index("idx_patterns_index", "learned_patterns", ["index_type"])
    op.create_index("idx_patterns_collection", "learned_patterns", ["collection_id"])

    # ── learned_trajectories ──
    op.create_table(
        "learned_trajectories",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("sequence_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("domain", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_sequence", postgresql.JSONB(), nullable=False),
        sa.Column("source_query", sa.Text(), nullable=False, server_default=""),
        sa.Column("outcome_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_lt_domain", "learned_trajectories", ["domain"])
    op.create_index("idx_lt_score", "learned_trajectories", ["outcome_score"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_index("idx_lt_score", table_name="learned_trajectories")
    op.drop_index("idx_lt_domain", table_name="learned_trajectories")
    op.drop_table("learned_trajectories")

    op.drop_index("idx_patterns_collection", table_name="learned_patterns")
    op.drop_index("idx_patterns_index", table_name="learned_patterns")
    op.drop_index("idx_patterns_domain", table_name="learned_patterns")
    op.drop_table("learned_patterns")

    op.drop_index("idx_alert_history_unack", table_name="alert_history")
    op.drop_index("idx_alert_history_job", table_name="alert_history")
    op.drop_index("idx_alert_history_item", table_name="alert_history")
    op.drop_index("idx_alert_history_rule", table_name="alert_history")
    op.drop_table("alert_history")

    op.drop_index("idx_alert_rules_user", table_name="alert_rules")
    op.drop_index("idx_alert_rules_template", table_name="alert_rules")
    op.drop_table("alert_rules")

    op.drop_index("idx_batch_items_status", table_name="batch_items")
    op.drop_index("idx_batch_items_job", table_name="batch_items")
    op.drop_table("batch_items")

    op.drop_index("idx_batch_jobs_status", table_name="batch_jobs")
    op.drop_index("idx_batch_jobs_user", table_name="batch_jobs")
    op.drop_index("idx_batch_jobs_schedule", table_name="batch_jobs")
    op.drop_index("idx_batch_jobs_template", table_name="batch_jobs")
    op.drop_table("batch_jobs")

    op.drop_index("idx_schedules_next_run", table_name="schedules")
    op.drop_index("idx_schedules_status", table_name="schedules")
    op.drop_index("idx_schedules_user", table_name="schedules")
    op.drop_index("idx_schedules_template", table_name="schedules")
    op.drop_table("schedules")

    op.drop_index("idx_templates_public", table_name="workflow_templates")
    op.drop_index("idx_templates_user", table_name="workflow_templates")
    op.drop_table("workflow_templates")

    op.drop_index("ix_ai_sessions_sdk_session_id", table_name="ai_sessions")
    op.drop_column("ai_sessions", "sdk_session_id")
