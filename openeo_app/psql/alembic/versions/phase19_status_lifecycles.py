"""Phase 19: Status lifecycles enforced in the database

Revision ID: phase19_status_lifecycles
Revises: phase18_batch_runs
Create Date: 2026-11-08

Each status column may only hold the states its lifecycle declares
(openeo_ai/lifecycle.py). The values are written out here so this migration
stays fixed if the lifecycles change later; test_lifecycles checks they match.

Deliveries gain claimed_at, so an abandoned send is reclaimed by when it was
claimed rather than by its age, and next_attempt_at, so retries back off.

Safe to re-run: columns use IF NOT EXISTS and constraints are skipped if present.
"""
from alembic import op

revision = "phase19_status_lifecycles"
down_revision = "phase18_batch_runs"
branch_labels = None
depends_on = None

STATUS_CHECKS = {
    "observations": ("cancelled", "completed", "failed", "queued", "running"),
    "signals": ("acknowledged", "open", "resolved"),
    "deliveries": ("failed", "pending", "sending", "sent"),
    "watches": ("active", "archived", "paused"),
    "batch_items": ("cancelled", "completed", "failed", "pending", "queued", "running"),
    "batch_jobs": ("cancelled", "completed", "failed", "partially_completed", "pending", "queued", "running"),
    "schedules": ("active", "cancelled", "expired", "paused"),
}


def _constraint_name(table: str) -> str:
    return f"ck_{table}_status"


def upgrade() -> None:
    op.execute("ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP")
    op.execute("ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deliveries_status_next_attempt "
        "ON deliveries(status, next_attempt_at)"
    )
    for table, states in STATUS_CHECKS.items():
        values = ", ".join(f"'{state}'" for state in states)
        name = _constraint_name(table)
        op.execute(
            f"""
            DO $$
            BEGIN
              IF to_regclass('public.{table}') IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{name}'
              ) THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} CHECK (status IN ({values}));
              END IF;
            END $$;
            """
        )


def downgrade() -> None:
    for table in STATUS_CHECKS:
        op.execute(f"ALTER TABLE IF EXISTS {table} DROP CONSTRAINT IF EXISTS {_constraint_name(table)}")
    op.execute("DROP INDEX IF EXISTS ix_deliveries_status_next_attempt")
    op.execute("ALTER TABLE deliveries DROP COLUMN IF EXISTS next_attempt_at")
    op.execute("ALTER TABLE deliveries DROP COLUMN IF EXISTS claimed_at")
