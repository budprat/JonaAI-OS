"""Phase 21: Row-level security on every remaining table, and no Data API access to usage

Revision ID: phase21_rls_remaining_tables
Revises: phase20_result_copies
Create Date: 2026-11-10

Every table here was created by an earlier migration (or, for plan_trajectories, only at
runtime by SQLAlchemy) without row-level security. Same pattern as phase20: RLS on with no
policies, so Supabase's anon and authenticated roles read nothing through the Data API, while
the app, which connects as the table owner, is unaffected (owners bypass RLS unless FORCE is
set). Every statement is idempotent, so a database that already has RLS on these tables is
unchanged.

On Supabase it also closes two gaps RLS can't cover:
- monthly_usage is a materialized view, which RLS doesn't apply to, so the Data API roles
  lose their grants on it;
- trigger functions in the public schema can't be called through /rest/v1/rpc.

These steps run only where the Supabase roles exist, so plain Postgres skips them.

Before applying it to an existing database, confirm the app's role owns these tables
(\\dt+ ... Owner) or has BYPASSRLS. Otherwise the app would see zero rows.

Rollback: downgrade() disables RLS again and leaves plan_trajectories in place.
"""
from alembic import op

revision = "phase21_rls_remaining_tables"
down_revision = "phase20_result_copies"
branch_labels = None
depends_on = None

# openeo-fastapi core (9ad7e6b87a94), add_ai_tables, phase6, phase17, phase18.
TABLES = (
    "users",
    "jobs",
    "udps",
    "tags",
    "learned_patterns",
    "learned_trajectories",
    "catalog_items",
    "batch_runs",
    "plan_trajectories",
)

_SUPABASE_API_LOCKDOWN = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    IF to_regclass('public.monthly_usage') IS NOT NULL THEN
      REVOKE ALL ON public.monthly_usage FROM anon, authenticated;
    END IF;
    IF to_regprocedure('public.handle_new_user()') IS NOT NULL THEN
      REVOKE EXECUTE ON FUNCTION public.handle_new_user() FROM anon, authenticated, PUBLIC;
    END IF;
  END IF;
END
$$;
"""


def upgrade() -> None:
    # Never migrated so far: create it as the model defines it, if a runtime create_all hasn't.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_trajectories (
          id VARCHAR(36) PRIMARY KEY,
          user_id VARCHAR(255) NOT NULL,
          query_text TEXT NOT NULL,
          tier VARCHAR(20) NOT NULL,
          plan_json JSON,
          tool_sequence JSON,
          step_count INTEGER NOT NULL DEFAULT 0,
          succeeded BOOLEAN NOT NULL DEFAULT TRUE,
          source VARCHAR(40) NOT NULL DEFAULT 'execute_workflow',
          created_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_plan_trajectories_user_id ON plan_trajectories(user_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_plan_trajectories_user_created "
        "ON plan_trajectories(user_id, created_at)"
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(_SUPABASE_API_LOCKDOWN)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
