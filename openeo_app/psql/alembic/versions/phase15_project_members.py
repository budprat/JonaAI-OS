"""Phase 15: Project collaboration (project_members)

Revision ID: phase15_project_members
Revises: phase14_usage_metering
Create Date: 2026-04-09

Adds multi-user project collaboration: projects can have additional
members with roles (owner | editor | viewer). The project creator
remains the owner via projects.user_id; project_members tracks
additional collaborators.
"""
from alembic import op

revision = "phase15_project_members"
down_revision = "phase14_usage_metering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE project_members (
          project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
          role TEXT NOT NULL DEFAULT 'viewer',   -- owner | editor | viewer
          invited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          invited_by UUID REFERENCES auth.users(id),
          PRIMARY KEY (project_id, user_id),
          CONSTRAINT valid_role CHECK (role IN ('owner', 'editor', 'viewer'))
        )
        """
    )
    op.execute("CREATE INDEX ix_project_members_user ON project_members(user_id)")
    op.execute("CREATE INDEX ix_project_members_project ON project_members(project_id)")

    op.execute("ALTER TABLE project_members ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY members_readable_by_participants ON project_members
          FOR SELECT TO authenticated
          USING (
            user_id = (select auth.uid())
            OR project_id IN (
              SELECT id FROM projects WHERE user_id = (select auth.uid())::text
            )
          )
        """
    )
    op.execute(
        """
        CREATE POLICY members_writable_by_owner ON project_members
          FOR ALL TO authenticated
          USING (
            project_id IN (
              SELECT id FROM projects WHERE user_id = (select auth.uid())::text
            )
          )
          WITH CHECK (
            project_id IN (
              SELECT id FROM projects WHERE user_id = (select auth.uid())::text
            )
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS members_writable_by_owner ON project_members")
    op.execute("DROP POLICY IF EXISTS members_readable_by_participants ON project_members")
    op.execute("ALTER TABLE project_members DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_project_members_project")
    op.execute("DROP INDEX IF EXISTS ix_project_members_user")
    op.execute("DROP TABLE project_members")
