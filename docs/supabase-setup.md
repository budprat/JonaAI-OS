# Setting up Supabase

JonaAI uses Supabase for two things: **sign-in** (Supabase Auth issues the bearer tokens the app verifies) and **Postgres** (all durable data, managed with Alembic). Every deployment brings its own Supabase project; none is shared or bundled with this repository.

You can run the tests and a local single-user setup without Supabase: with no Postgres settings, the app falls back to SQLite for local and test paths. You need a project for sign-in, multiple users and the Earth Intelligence monitoring spine.

## 1. Create the project

1. Create a project at https://supabase.com. Pick the region closest to where the backend runs.
2. Save the database password somewhere safe. You'll put it in `.env`; it never goes in the repository.

## 2. Copy the settings into `.env`

Copy `.env.example` to `.env` (git ignores it) and fill these in from the Supabase dashboard:

| `.env` setting | Where to find it |
| --- | --- |
| `SUPABASE_URL`, `VITE_SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_ANON_KEY`, `VITE_SUPABASE_ANON_KEY` | Project Settings → API → the publishable (anon) key. It's public by design and is built into the frontend |
| `SUPABASE_SERVICE_ROLE_KEY` | Project Settings → API → the secret (service role) key. Backend only: never expose it to the frontend |
| `SUPABASE_JWT_SECRET` | Project Settings → API → JWT secret. Needed only for projects that still sign tokens with HS256; newer projects use asymmetric keys, which the app fetches from the project's JWKS endpoint |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRESQL_HOST`, `POSTGRESQL_PORT`, `POSTGRES_DB` | Connect → Connection pooler (transaction mode, port 6543). The user looks like `postgres.<project-ref>` |

Write the settings as `export KEY=value` lines for local development. Under systemd, use plain `KEY=value` lines; see `docs/deployment.md`.

## 3. Create the tables

From the repository root, with `.env` loaded:

```bash
set -a; source .env; set +a
cd openeo_app/psql
../../.venv/bin/alembic upgrade head
```

The migrations create every table and turn on row-level security for all of them.
- RLS is on with **no policies** for tables only the backend uses, so Supabase's public API roles (`anon`, `authenticated`) can read nothing from them.
- The backend connects as the table owner (`postgres`), which bypasses RLS, so the app itself is unaffected.
- The backend enforces per-user access in its own code (`openeo_ai/storage/result_access.py` and the repositories). Database RLS is a second layer, not the only one.

## 4. Lock down sign-in

In the Supabase dashboard:

1. **Authentication → Providers:** enable only the sign-in methods you want.
2. **Authentication → Sign In / Up:** turn off new sign-ups if only invited users should get in.
3. **Authentication → URL Configuration:** set the site URL and redirect URLs to your frontend's address.
4. **Leaked password protection** (where your plan offers it): turn it on.

In `.env`:
- `JONA_ALLOWED_EMAILS`: a comma-separated list of the accounts this deployment accepts. Without it, anyone who can sign up can use the app.
- `JONA_REQUIRE_TOOL_USER=true`: refuse agent tool calls without a signed-in user.

## 5. Check it

1. Open **Advisors → Security Advisor** in the dashboard. A fresh project should show no table without RLS. "RLS enabled, no policy" notices are expected for backend-only tables.
2. Start the app (`bash start-local.sh`), sign in, and run a query.
3. If sign-in fails, see "Supabase JWTs fail" under Troubleshooting in the README.

## Keeping it safe

- Never commit `.env`, the service role key, the JWT secret or the database password. Anyone holding the service role key bypasses every RLS rule.
- When you add a table, enable RLS on it in the same migration. `test_every_migrated_table_has_row_level_security` fails until you do.
- RLS can't protect a view or a materialized view. Revoke the `anon` and `authenticated` grants on any you add, as phase 21 does for `monthly_usage`.
- If a key leaks, rotate it in the dashboard at once, then update `.env` and restart.
