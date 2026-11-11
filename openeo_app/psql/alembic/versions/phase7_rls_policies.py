"""Phase 7: Row-Level Security for user isolation

Revision ID: phase7_rls_policies
Revises: phase6_sqlite_to_pg
Create Date: 2026-04-09

Enables Row-Level Security (RLS) on all user-scoped tables and creates
policies that restrict reads/writes to rows where user_id matches the
authenticated JWT's sub claim.

Schema changes first:
  - saved_results: add nullable user_id column (was missing)
  - index on saved_results.user_id for RLS query performance

RLS policies (per supabase-postgres-best-practices skill):
  - Use `(select auth.uid())::text` pattern — the SELECT wrapper caches
    the function call per-query, avoiding per-row evaluation. 100x+
    faster on large tables.
  - For tables without a direct user_id (batch_items, alert_history),
    use subquery policies that join to the parent's user_id.
  - Public process graphs visible to everyone via an OR clause.
  - learned_patterns and learned_trajectories are intentionally global
    (no RLS) — they represent shared learned knowledge, not user data.

Not FORCED — the backend connects as the `postgres` role (via Supabase
pooler), which bypasses RLS by default. This is intentional:
  - Backend filters by user_id at the application layer
  - Frontend using Supabase client directly (future) WILL be subject to RLS
  - Protects against potential future data access from the `authenticated`
    role without requiring backend code rewrites today

If in a later phase we route backend queries through the `authenticated`
role with SET LOCAL jwt context, we can ALTER TABLE ... FORCE RLS then.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "phase7_rls_policies"
down_revision = "phase6_sqlite_to_pg"
branch_labels = None
depends_on = None


# Tables with a direct user_id column (varchar/text, matches JWT sub as text)
DIRECT_USER_TABLES = [
    "ai_sessions",
    "saved_process_graphs",
    "execution_history",
    "projects",
    "saved_results",
    "workflow_templates",
    "batch_jobs",
    "schedules",
    "alert_rules",
]


def upgrade() -> None:
    # ── Schema fix: add missing user_id on saved_results ──
    op.execute(
        """
        ALTER TABLE saved_results
        ADD COLUMN IF NOT EXISTS user_id TEXT
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_saved_results_user_id
        ON saved_results(user_id)
        """
    )

    # ── Direct user_id tables: enable RLS + policy ──
    for table in DIRECT_USER_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # Drop any existing policy with the same name (idempotent re-runs)
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY user_isolation ON {table}
              FOR ALL
              TO authenticated
              USING (user_id = (select auth.uid())::text)
              WITH CHECK (user_id = (select auth.uid())::text)
            """
        )

    # ── saved_process_graphs: extra policy for public templates ──
    # Users can READ (not write) any public process graph regardless of ownership.
    op.execute("DROP POLICY IF EXISTS public_graphs_readable ON saved_process_graphs")
    op.execute(
        """
        CREATE POLICY public_graphs_readable ON saved_process_graphs
          FOR SELECT
          TO authenticated
          USING (is_public = true)
        """
    )

    # ── workflow_templates: extra policy for public templates (marketplace) ──
    # is_public is INTEGER 0/1 in this table (legacy batch code), not BOOLEAN
    op.execute("DROP POLICY IF EXISTS public_templates_readable ON workflow_templates")
    op.execute(
        """
        CREATE POLICY public_templates_readable ON workflow_templates
          FOR SELECT
          TO authenticated
          USING (is_public = 1)
        """
    )

    # ── batch_items: indirect user_id via batch_jobs parent ──
    op.execute("ALTER TABLE batch_items ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS user_isolation ON batch_items")
    op.execute(
        """
        CREATE POLICY user_isolation ON batch_items
          FOR ALL
          TO authenticated
          USING (
            batch_job_id IN (
              SELECT id FROM batch_jobs
              WHERE user_id = (select auth.uid())::text
            )
          )
          WITH CHECK (
            batch_job_id IN (
              SELECT id FROM batch_jobs
              WHERE user_id = (select auth.uid())::text
            )
          )
        """
    )

    # ── alert_history: indirect user_id via alert_rules parent ──
    op.execute("ALTER TABLE alert_history ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS user_isolation ON alert_history")
    op.execute(
        """
        CREATE POLICY user_isolation ON alert_history
          FOR ALL
          TO authenticated
          USING (
            alert_rule_id IN (
              SELECT id FROM alert_rules
              WHERE user_id = (select auth.uid())::text
            )
          )
          WITH CHECK (
            alert_rule_id IN (
              SELECT id FROM alert_rules
              WHERE user_id = (select auth.uid())::text
            )
          )
        """
    )

    # ── process_graph_tags: junction table — readable when parent graph is readable ──
    # Tags are global (no user_id); the association row inherits the graph's visibility.
    op.execute("ALTER TABLE process_graph_tags ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS graph_tags_via_parent ON process_graph_tags")
    op.execute(
        """
        CREATE POLICY graph_tags_via_parent ON process_graph_tags
          FOR ALL
          TO authenticated
          USING (
            process_graph_id IN (
              SELECT id FROM saved_process_graphs
              WHERE user_id = (select auth.uid())::text OR is_public = true
            )
          )
        """
    )

    # Note: learned_patterns and learned_trajectories are intentionally NOT
    # RLS-enabled. They represent globally shared learned knowledge (ML
    # pattern library), not per-user data. All authenticated users can read
    # and write these tables.

    # Note: the `jobs`, `users`, and `udps` tables belong to openeo-fastapi.
    # They use a UUID user_id tied to the OpenEO User record, not the
    # Supabase auth.users UUID. Enabling RLS on them would require a mapping
    # between Supabase auth.uid() and the OpenEO User.user_id — out of scope
    # for this phase.


def downgrade() -> None:
    # Drop all policies first, then disable RLS, then drop the added column

    # Junction + indirect
    op.execute("DROP POLICY IF EXISTS graph_tags_via_parent ON process_graph_tags")
    op.execute("ALTER TABLE process_graph_tags DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS user_isolation ON alert_history")
    op.execute("ALTER TABLE alert_history DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS user_isolation ON batch_items")
    op.execute("ALTER TABLE batch_items DISABLE ROW LEVEL SECURITY")

    # Extra public-read policies
    op.execute("DROP POLICY IF EXISTS public_templates_readable ON workflow_templates")
    op.execute("DROP POLICY IF EXISTS public_graphs_readable ON saved_process_graphs")

    # Direct user_id tables
    for table in DIRECT_USER_TABLES:
        op.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop the added column + index
    op.execute("DROP INDEX IF EXISTS ix_saved_results_user_id")
    op.execute("ALTER TABLE saved_results DROP COLUMN IF EXISTS user_id")
