"""Phase 11: Sharing & public workflows

Revision ID: phase11_sharing
Revises: phase10_profiles
Create Date: 2026-04-09

Creates the `shared_items` table that models cross-user sharing of
analyses, workflows, and projects. Supports both public (share_link
only) and private (shared_with_id) shares.
"""
from alembic import op

revision = "phase11_sharing"
down_revision = "phase10_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE shared_items (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          item_type TEXT NOT NULL,          -- analysis | workflow | project
          item_id TEXT NOT NULL,            -- save_id, template_id, or project_id
          owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
          shared_with_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
          permission TEXT NOT NULL DEFAULT 'view',   -- view | duplicate | edit
          share_link TEXT UNIQUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT valid_item_type CHECK (item_type IN ('analysis', 'workflow', 'project')),
          CONSTRAINT valid_permission CHECK (permission IN ('view', 'duplicate', 'edit'))
        )
        """
    )
    op.execute("CREATE INDEX ix_shared_items_owner ON shared_items(owner_id)")
    op.execute("CREATE INDEX ix_shared_items_shared_with ON shared_items(shared_with_id)")
    op.execute("CREATE INDEX ix_shared_items_item ON shared_items(item_type, item_id)")
    op.execute("CREATE INDEX ix_shared_items_link ON shared_items(share_link) WHERE share_link IS NOT NULL")

    # RLS: owners manage their shares; recipients can read shares to them;
    # public shares (shared_with_id IS NULL) are readable by anyone authenticated.
    op.execute("ALTER TABLE shared_items ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY owner_manages_shares ON shared_items
          FOR ALL
          TO authenticated
          USING (owner_id = (select auth.uid()))
          WITH CHECK (owner_id = (select auth.uid()))
        """
    )
    op.execute(
        """
        CREATE POLICY recipient_reads_shares ON shared_items
          FOR SELECT
          TO authenticated
          USING (shared_with_id = (select auth.uid()) OR shared_with_id IS NULL)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS recipient_reads_shares ON shared_items")
    op.execute("DROP POLICY IF EXISTS owner_manages_shares ON shared_items")
    op.execute("ALTER TABLE shared_items DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE shared_items")
