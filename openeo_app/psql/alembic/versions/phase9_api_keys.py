"""Phase 9: Persist API keys in PostgreSQL

Revision ID: phase9_api_keys
Revises: phase7_rls_policies
Create Date: 2026-04-09

Replaces the in-memory _api_keys dict in api/auth.py with a durable
PostgreSQL table so keys survive restarts and scale across multiple
backend instances. The _admin_tokens and _refresh_tokens dicts in the
legacy auth modules are eliminated entirely — Supabase Auth handles
refresh and session revocation.

Schema:
  api_keys(
    id           TEXT PK                  -- key_xxxxxxxxxxxx
    key_hash     TEXT UNIQUE NOT NULL     -- sha256 of the raw key
    name         TEXT NOT NULL
    user_id      TEXT NOT NULL            -- Supabase user UUID (as text)
    prefix       TEXT NOT NULL            -- first 16 chars, for display
    scopes       JSONB NOT NULL           -- list of scope strings
    tier         TEXT NOT NULL            -- free | pro | enterprise
    is_active    BOOLEAN NOT NULL         -- revocation flag
    created_at   TIMESTAMPTZ NOT NULL
    expires_at   TIMESTAMPTZ              -- optional expiration
    last_used_at TIMESTAMPTZ              -- for auditing
  )

Indexes:
  - key_hash is UNIQUE (used for O(1) lookup on every API request)
  - user_id (for "list my keys" queries)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "phase9_api_keys"
down_revision = "phase7_rls_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default='["analyze","results"]'),
        sa.Column("tier", sa.Text(), nullable=False, server_default="free"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_is_active", "api_keys", ["is_active"])

    # RLS: users can only see/manage their own API keys when connecting
    # via the authenticated role. Backend (postgres) bypasses RLS.
    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY user_isolation ON api_keys
          FOR ALL
          TO authenticated
          USING (user_id = (select auth.uid())::text)
          WITH CHECK (user_id = (select auth.uid())::text)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_isolation ON api_keys")
    op.execute("ALTER TABLE api_keys DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_api_keys_is_active", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
