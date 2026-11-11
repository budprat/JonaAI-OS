"""Phase 20: Offsite copies of saved results

Revision ID: phase20_result_copies
Revises: phase19_status_lifecycles
Create Date: 2026-11-09

One row per saved result that has an offsite copy, or is waiting for one
(openeo_ai/storage/result_copies.py). saved_results itself is unchanged, so the
app runs without this table while copies are off.

The table is internal. Row-level security is on with no policies, so Supabase's
anon and authenticated roles can't read it; the app connects as the table owner.

The status values are written out so this migration stays fixed if the lifecycle
changes later; test_result_copies checks they match RESULT_COPY.
"""
from alembic import op

revision = "phase20_result_copies"
down_revision = "phase19_status_lifecycles"
branch_labels = None
depends_on = None

STATUSES = ("failed", "pending", "refused", "synced")


def upgrade() -> None:
    values = ", ".join(f"'{status}'" for status in STATUSES)
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS result_copies (
          save_id VARCHAR(8) PRIMARY KEY REFERENCES saved_results(save_id) ON DELETE CASCADE,
          user_id VARCHAR(255),
          status VARCHAR(16) NOT NULL DEFAULT 'pending',
          bytes BIGINT NOT NULL DEFAULT 0,
          downsample INTEGER NOT NULL DEFAULT 1,
          attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_at TIMESTAMP,
          last_error TEXT,
          synced_at TIMESTAMP,
          created_at TIMESTAMP NOT NULL DEFAULT now(),
          updated_at TIMESTAMP NOT NULL DEFAULT now(),
          version INTEGER NOT NULL DEFAULT 1,
          CONSTRAINT ck_result_copies_status CHECK (status IN ({values}))
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_result_copies_user_id ON result_copies(user_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_result_copies_status_next_attempt "
        "ON result_copies(status, next_attempt_at)"
    )
    op.execute("ALTER TABLE result_copies ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS result_copies")
