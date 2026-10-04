# Water Billing System — Test Environment

A standalone, fully isolated copy of [water-billing-system](https://github.com/Bernard-Otieno/water-billing-system),
used for manual and automated testing without touching production data or infrastructure.

**This repo is intentionally disconnected from the production repo** — separate git history,
separate GitHub repo, separate Supabase project, separate Render service. Nothing here can
affect the live system serving real customer accounts.

## What's different from production

| | Production | This repo |
|---|---|---|
| Supabase project | Live, ~180 real accounts | `water-billing-test` — fake seeded data only |
| Render service | Auto-deploys from `main` on push | Deploys only via manual trigger of the Render Deploy Hook, after CI passes |
| Session secret | Render env var (prod) | Separate value, generated independently |
| Users | Real admin/field-worker accounts | `create_test_users.py` — not committed, run locally to create test logins |

## Local setup

1. **Create `.env`** at the project root (never committed — see `.gitignore`):
   ```
   SUPABASE_URL=<test project's URL>
   SUPABASE_KEY=<test project's service_role key — NOT anon, since app code doesn't go through RLS>
   SESSION_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
   ```

2. **Load the schema** into an empty Supabase project via `db/schema.sql`, either through the SQL Editor
   or:
   ```powershell
   psql "postgresql://postgres:<password>@db.<test-ref>.supabase.co:5432/postgres" -f db/schema.sql
   ```

3. **Create test users** (not committed — recreate locally from this shape):
   ```python
   # create_test_users.py
   from app.database import supabase
   from app.auth import hash_password

   users_to_create = [
       {"username": "admin", "password": "<your test password>", "role": "owner"},
       {"username": "casper", "password": "<your test password>", "role": "meter_reader"},
   ]
   for u in users_to_create:
       supabase.table("users").insert({
           "username": u["username"],
           "password_hash": hash_password(u["password"]),
           "role": u["role"]
       }).execute()
   ```
   ```powershell
   docker compose run --rm app python create_test_users.py
   ```

4. **Seed fake accounts**:
   ```powershell
   docker compose run --rm app python seed_test_data.py
   ```

5. **Run the app**:
   ```powershell
   docker compose up --build
   ```
   Visit `http://127.0.0.1:8000`.

## Deployment

- Hosted separately on Render as its own service, built from this repo's `Dockerfile` directly (`docker-compose.yml` is local-dev only — it's not used in the Render build).
- Deploys are triggered by the `RENDER_DEPLOY_HOOK_URL` secret, called from CI (`.github/workflows/`) only after lint/security/Docker checks pass — not on every raw push. Render's own Auto-Deploy is turned **off** for this service, so this hook is the only thing that triggers a build.

## Known gaps / things to double-check after any fresh schema pull

The Supabase dashboard's schema export tool only captures `CREATE TABLE` statements — it silently
omits views, functions, and sequences. `pg_dump --schema-only` captures all of them and is the more
reliable method if redoing this from scratch.

Currently known required objects beyond plain tables:
- `public.latest_bills` (view) — one row per account, latest bill by `billing_year`/`billing_month`. Used by the dashboard route.

If a fresh schema pull ever breaks a page again, check for missing views first:
```sql
select viewname from pg_views where schemaname = 'public';
```

## Repo relationship

This is a clone-at-a-point-in-time of production, not a tracked fork — the two repos share no
git history and won't stay in sync automatically. Pull changes over manually if production code changes.