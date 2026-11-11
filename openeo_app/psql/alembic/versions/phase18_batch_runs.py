"""Phase 18: Durable public-API batch runs

Revision ID: phase18_batch_runs
Revises: phase17_earth_intelligence
Create Date: 2026-07-07

Adds the batch_runs table so public-API batch state (batch_id -> per-location
jobs) survives backend restarts, replacing the in-memory _batches dict in
openeo_ai/api/router.py. Per-location job execution/status is a separate,
still-transient system and is not migrated here.
"""
from alembic import op

revision = "phase18_batch_runs"
down_revision = "phase17_earth_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS batch_runs (
          id VARCHAR(36) PRIMARY KEY,
          client_id VARCHAR(255) NOT NULL,
          user_id VARCHAR(255),
          webhook_url TEXT,
          location_jobs JSONB NOT NULL DEFAULT '[]'::jsonb,
          webhook_fired BOOLEAN NOT NULL DEFAULT false,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_batch_runs_client ON batch_runs(client_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_batch_runs_user_id ON batch_runs(user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS batch_runs")
