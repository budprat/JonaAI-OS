"""Phase 14: Usage metering & tier enforcement

Revision ID: phase14_usage_metering
Revises: phase12_community
Create Date: 2026-04-09

Creates usage_events (append-only event log) + monthly_usage materialized
view for fast per-user/per-month quota checks. Replaces the in-memory
_rate_counters dict in api/auth.py with database-backed persistence.

Tier limits live in code (api/auth.py RATE_LIMIT_TIERS). The monthly
view is refreshed on-demand by the api/usage.py endpoint or a cron.
"""
from alembic import op

revision = "phase14_usage_metering"
down_revision = "phase12_community"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE usage_events (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
          event_type TEXT NOT NULL,   -- analysis, api_call, storage_bytes
          quantity BIGINT NOT NULL DEFAULT 1,
          metadata JSONB NOT NULL DEFAULT '{}',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_usage_user_created ON usage_events(user_id, created_at)")
    op.execute("CREATE INDEX ix_usage_type ON usage_events(event_type)")

    op.execute(
        """
        CREATE MATERIALIZED VIEW monthly_usage AS
        SELECT
          user_id,
          date_trunc('month', created_at) AS month,
          event_type,
          SUM(quantity) AS total
        FROM usage_events
        GROUP BY user_id, date_trunc('month', created_at), event_type
        """
    )
    op.execute("CREATE UNIQUE INDEX ix_monthly_usage_unique ON monthly_usage(user_id, month, event_type)")

    op.execute("ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY own_usage_readable ON usage_events
          FOR SELECT TO authenticated
          USING (user_id = (select auth.uid()))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS own_usage_readable ON usage_events")
    op.execute("ALTER TABLE usage_events DISABLE ROW LEVEL SECURITY")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS monthly_usage")
    op.execute("DROP TABLE IF EXISTS usage_events")
