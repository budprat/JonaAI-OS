"""Phase 10: User profiles + auto-create trigger

Revision ID: phase10_profiles
Revises: phase9_api_keys
Create Date: 2026-04-09

Creates a `profiles` table linked to Supabase's auth.users and a trigger
that auto-creates a profile row whenever a new user signs up. This is
the Supabase-recommended pattern for per-user extensible metadata.

Schema:
  profiles(
    id                    UUID PK (references auth.users.id)
    username              TEXT UNIQUE
    display_name          TEXT
    avatar_url            TEXT
    bio                   TEXT
    organization          TEXT
    expertise             TEXT[] (array of domain tags)
    is_public             BOOLEAN (default true)
    analysis_count        INTEGER (cached counter)
    shared_count          INTEGER (cached counter)
    onboarding_completed  BOOLEAN (Phase 13)
    onboarding_step       INTEGER (Phase 13)
    onboarding_data       JSONB   (Phase 13)
    created_at            TIMESTAMPTZ
    updated_at            TIMESTAMPTZ
  )
"""
from alembic import op

revision = "phase10_profiles"
down_revision = "phase9_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE profiles (
          id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
          username TEXT UNIQUE NOT NULL,
          display_name TEXT,
          avatar_url TEXT,
          bio TEXT,
          organization TEXT,
          expertise TEXT[] NOT NULL DEFAULT '{}',
          is_public BOOLEAN NOT NULL DEFAULT true,
          analysis_count INTEGER NOT NULL DEFAULT 0,
          shared_count INTEGER NOT NULL DEFAULT 0,
          onboarding_completed BOOLEAN NOT NULL DEFAULT false,
          onboarding_step INTEGER NOT NULL DEFAULT 0,
          onboarding_data JSONB NOT NULL DEFAULT '{}',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_profiles_username ON profiles(username)")
    op.execute("CREATE INDEX ix_profiles_is_public ON profiles(is_public) WHERE is_public = true")

    # Auto-create profile on user signup
    op.execute(
        """
        CREATE OR REPLACE FUNCTION handle_new_user()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = ''
        AS $$
        BEGIN
          INSERT INTO public.profiles (id, username, display_name)
          VALUES (
            NEW.id,
            COALESCE(
              NEW.raw_user_meta_data->>'username',
              split_part(NEW.email, '@', 1) || '_' || substr(NEW.id::text, 1, 8)
            ),
            COALESCE(
              NEW.raw_user_meta_data->>'full_name',
              split_part(NEW.email, '@', 1)
            )
          )
          ON CONFLICT (id) DO NOTHING;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
        CREATE TRIGGER on_auth_user_created
          AFTER INSERT ON auth.users
          FOR EACH ROW EXECUTE FUNCTION handle_new_user()
        """
    )

    # RLS: everyone can read public profiles, users can update their own
    op.execute("ALTER TABLE profiles ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY public_profiles_readable ON profiles
          FOR SELECT
          TO authenticated
          USING (is_public = true OR id = (select auth.uid()))
        """
    )
    op.execute(
        """
        CREATE POLICY own_profile_editable ON profiles
          FOR UPDATE
          TO authenticated
          USING (id = (select auth.uid()))
          WITH CHECK (id = (select auth.uid()))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS own_profile_editable ON profiles")
    op.execute("DROP POLICY IF EXISTS public_profiles_readable ON profiles")
    op.execute("ALTER TABLE profiles DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users")
    op.execute("DROP FUNCTION IF EXISTS handle_new_user()")
    op.execute("DROP INDEX IF EXISTS ix_profiles_is_public")
    op.execute("DROP INDEX IF EXISTS ix_profiles_username")
    op.execute("DROP TABLE profiles")
