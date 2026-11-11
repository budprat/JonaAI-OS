"""Phase 12: Community features (challenges, feed)

Revision ID: phase12_community
Revises: phase11_sharing
Create Date: 2026-04-09

Creates three community tables:
  - challenges: community-hosted analysis challenges
  - challenge_submissions: user entries per challenge
  - activity_feed: public event stream (analysis saved, workflow shared, etc.)

Replaces the mock data in frontend/src/lib/geo/mock-community.ts with
real persistence backed by user IDs (foreign keys into auth.users).
"""
from alembic import op

revision = "phase12_community"
down_revision = "phase11_sharing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── challenges ──
    op.execute(
        """
        CREATE TABLE challenges (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          title TEXT NOT NULL,
          description TEXT NOT NULL,
          category TEXT NOT NULL,             -- deforestation, agriculture, urban, climate
          status TEXT NOT NULL DEFAULT 'upcoming',  -- upcoming, active, completed
          deadline TIMESTAMPTZ,
          prize TEXT,
          created_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT valid_status CHECK (status IN ('upcoming', 'active', 'completed'))
        )
        """
    )
    op.execute("CREATE INDEX ix_challenges_status ON challenges(status)")
    op.execute("CREATE INDEX ix_challenges_category ON challenges(category)")
    op.execute("CREATE INDEX ix_challenges_created_by ON challenges(created_by)")

    # ── challenge_submissions ──
    op.execute(
        """
        CREATE TABLE challenge_submissions (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          challenge_id UUID NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
          analysis_id TEXT,                   -- saved_results.save_id
          score DOUBLE PRECISION,
          feedback TEXT,
          status TEXT NOT NULL DEFAULT 'pending',  -- pending, scored, rejected
          submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(challenge_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_submissions_challenge ON challenge_submissions(challenge_id)")
    op.execute("CREATE INDEX ix_submissions_user ON challenge_submissions(user_id)")
    op.execute("CREATE INDEX ix_submissions_score ON challenge_submissions(challenge_id, score DESC)")

    # ── activity_feed ──
    op.execute(
        """
        CREATE TABLE activity_feed (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
          event_type TEXT NOT NULL,
          entity_type TEXT,
          entity_id TEXT,
          metadata JSONB NOT NULL DEFAULT '{}',
          is_public BOOLEAN NOT NULL DEFAULT true,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_activity_feed_user ON activity_feed(user_id)")
    op.execute("CREATE INDEX ix_activity_feed_public ON activity_feed(created_at DESC) WHERE is_public = true")

    # ── RLS ──
    op.execute("ALTER TABLE challenges ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY challenges_readable ON challenges
          FOR SELECT TO authenticated USING (true)
        """
    )
    op.execute(
        """
        CREATE POLICY challenges_creator_writable ON challenges
          FOR INSERT TO authenticated WITH CHECK (created_by = (select auth.uid()))
        """
    )

    op.execute("ALTER TABLE challenge_submissions ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY submissions_readable ON challenge_submissions
          FOR SELECT TO authenticated USING (true)
        """
    )
    op.execute(
        """
        CREATE POLICY own_submissions_writable ON challenge_submissions
          FOR ALL TO authenticated
          USING (user_id = (select auth.uid()))
          WITH CHECK (user_id = (select auth.uid()))
        """
    )

    op.execute("ALTER TABLE activity_feed ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY public_activity_readable ON activity_feed
          FOR SELECT TO authenticated
          USING (is_public = true OR user_id = (select auth.uid()))
        """
    )
    op.execute(
        """
        CREATE POLICY own_activity_writable ON activity_feed
          FOR INSERT TO authenticated
          WITH CHECK (user_id = (select auth.uid()))
        """
    )


def downgrade() -> None:
    for policy, table in [
        ("own_activity_writable", "activity_feed"),
        ("public_activity_readable", "activity_feed"),
        ("own_submissions_writable", "challenge_submissions"),
        ("submissions_readable", "challenge_submissions"),
        ("challenges_creator_writable", "challenges"),
        ("challenges_readable", "challenges"),
    ]:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    for t in ("activity_feed", "challenge_submissions", "challenges"):
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
        op.execute(f"DROP TABLE {t}")
