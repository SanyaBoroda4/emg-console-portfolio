# EMG Ops Console — Deploy stage build plan (Azure Linux B1 + managed Postgres)

> Two actors: Claude Code (app + CI changes, sections 1–2) and the OWNER
> (Azure portal runbook, section 3). Read CLAUDE.md + PROJECT_STATE.md first.
> Local development is UNCHANGED: docker compose remains the dev environment.
> This stage adds a production home. No feature changes ride along — deploy
> the app exactly as it exists (through Stage 3 + camera; the push pilot
> builds AFTER this, directly against the prod URL).

## 0. Decisions (recorded)

- Hosting: NEW Azure App Service plan, **Linux B1** (~$12.41/mo), region
  **West US 3** (same as the bridge). One Web App runs BOTH tiers: FastAPI
  serves the built React bundle (single origin — CORS and cookie config
  collapse to same-origin).
- Database: **Azure Database for PostgreSQL Flexible Server, Burstable B1ms,
  32 GiB, PostgreSQL 16, HA disabled, West US 3** (free-tier eligible year 1).
- Deploy method: **code deploy via GitHub Actions** (no container registry —
  avoids ACR cost/complexity). Push to main = deploy.
- Photos: App Service persistent storage under **/home/data/uploads**
  (WEBSITES_ENABLE_APP_SERVICE_STORAGE=true). Blob storage is a later
  refinement, not now.
- Prod data starts fresh: run the mirror against the prod DB after deploy.
  Local test rows do not migrate (they are tests).

## 1. App changes (Claude Code)

1. **Single-origin serving**: FastAPI mounts the built frontend
   (frontend/dist) as static files with an SPA fallback (any non-/api route →
   index.html). Path configurable via env `FRONTEND_DIST` (empty in local dev
   where Vite serves; set in prod). API routes keep the /api prefix — no
   frontend code changes needed except: remove any hardcoded origin
   assumptions; asset paths relative.
2. **Config**: new env vars with sane local defaults —
   `UPLOADS_DIR` (default /data/uploads; prod /home/data/uploads),
   `ENVIRONMENT` already exists (prod value "production"),
   `FRONTEND_DIST` (default empty). DATABASE_URL in prod will carry
   `?sslmode=require` — verify psycopg3 accepts it (it does; test).
3. **Proxy correctness**: App Service terminates TLS in front of us. Run
   uvicorn/gunicorn with proxy-headers enabled so request.scheme is https
   (cookies already Secure; redirects and any absolute URLs must not
   downgrade to http).
4. **startup.sh** (repo root or backend/): `alembic upgrade head` then
   `gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000
   app.main:app` (2 workers on 1 vCPU; SQLAlchemy pool sized modestly, e.g.
   pool_size=5 per worker — B1ms allows ~35 connections, stay well under).
5. **CORS**: production same-origin → restrict allowed origins to localhost
   dev values only when ENVIRONMENT != production; in production no
   cross-origin needed at all.
6. **GitHub Actions** workflow `.github/workflows/deploy.yml`:
   on push to main → setup Node, `npm ci && npm run build` in frontend →
   copy dist into the deploy artifact → setup Python deps for the backend →
   deploy via azure/webapps-deploy@v3 using secret AZURE_WEBAPP_PUBLISH_PROFILE
   → job summary prints the prod URL. Keep pytest as a required job before
   deploy (tests gate the release).
7. **Health**: /api/health already exists — used as the smoke check.
8. Docs: README gains a "production" section (how deploy works, where logs
   live: App Service → Log stream).

## 2. Repo/secrets prep (Claude Code writes instructions; owner executes)

GitHub repo → Settings → Secrets and variables → Actions → new secret
`AZURE_WEBAPP_PUBLISH_PROFILE` (contents come from the portal in step 3.4).

## 3. OWNER'S PORTAL RUNBOOK (in order; ~45 minutes)

3.1 **Postgres** — Create resource → Azure Database for PostgreSQL Flexible
Server: name e.g. `emg-console-db` · West US 3 · PostgreSQL **16** · Workload
Dev/Test · Compute+storage: Burstable **B1ms**, **32 GiB**, HA **disabled**
(the free-tier banner should show) · auth: PostgreSQL authentication, admin
user `emgadmin`, strong generated password (goes ONLY into App settings later)
· Networking: **Public access**, check **"Allow public access from any Azure
service"**, + add your current home/office IP (for psql from your PC) ·
create. After deploy: note the full server hostname
(…postgres.database.azure.com).

3.2 **Web App** — Create resource → Web App: name `emg-console` (or nearest
free) · Publish **Code** · Runtime **Python 3.12** · OS **Linux** · West US 3
· App Service plan: **Create new**, name `ASP-EMGConsole-Linux`, size
**Basic B1** · create.

3.3 **Web App configuration** (Settings → Environment variables): add every
production value —
DATABASE_URL=postgresql+psycopg://emgadmin:<pwd>@<host>:5432/postgres?sslmode=require
(or create a dedicated `emg_console` DB via psql first — preferred; document
which), AIRTABLE_TOKEN, AIRTABLE_WRITE_TOKEN, AIRTABLE_BASE_ID,
AIRTABLE_PAYMENTS_TABLE, GOOGLE_CLIENT_ID, SESSION_SECRET (generate a NEW one
for prod — do not reuse dev), ENVIRONMENT=production,
UPLOADS_DIR=/home/data/uploads,
WEBSITES_ENABLE_APP_SERVICE_STORAGE=true.
NOTE: FRONTEND_DIST and PYTHONPATH do NOT need to be set — startup.sh computes
them from its own location (works whether the app runs from /home/site/wwwroot
or Oryx's /tmp extract dir). Deps are vendored into pydeps/ at BUILD time by the
Action as portable manylinux2014 wheels, so `SCM_DO_BUILD_DURING_DEPLOYMENT`
does not matter (the app boots with or without Oryx's build); false is fine.
VITE_GOOGLE_CLIENT_ID is a GitHub Actions SECRET (build-time), not an app
setting — see §2.
Settings → Configuration → General: Startup Command = `bash startup.sh`.
Always On = On. NOTE: the Web App name must equal `emg-console` or the
AZURE_WEBAPP_NAME env in deploy.yml must be changed to match.

3.4 **Publish profile** — Web App → Overview → Download publish profile →
paste entire XML into the GitHub secret AZURE_WEBAPP_PUBLISH_PROFILE.

3.5 **Google OAuth** — Cloud Console → your client → Authorized JavaScript
origins → add `https://<your-app-name>.azurewebsites.net`. (Keep the
localhost + nip.io entries for dev.)

3.6 **Budget alert** — Cost Management → Budgets → monthly budget $40,
email alert at 80%.

## 4. First deploy + data

1. Merge the deploy branch → push to main → watch Actions run green →
   open https://<app>.azurewebsites.net → login page on a REAL certificate.
2. Sign in as alex@ (origin added in 3.5). Boards empty — correct.
3. Populate: Web App → SSH (or Kudu console) →
   `python -m app.scripts.mirror_airtable --table payments` → refresh: real
   payments on the internet, behind login.
4. Phone test WITHOUT nip.io or cert warnings: login, camera capture, submit
   → photo persists (restart the app in the portal; photo still served —
   proves /home persistence).

## 5. Verification checklist

1. Actions: tests job green, deploy job green, URL in summary.
2. /api/health via the public URL → ok/ok.
3. Login works for alex@ AND one non-admin (role gating intact in prod).
4. Mirror populated counts match Airtable; a Stage-3 amount edit on a
   mirrored row from PROD writes through to Airtable and back (the drill,
   now on real infra — this finally clears the Part-0 debt).
5. Camera submit from a phone on cellular data (not office wifi) — proves
   true public access; photo visible; lightbox works.
6. Restart Web App → session cookie still valid (stateless sessions),
   photos persist, app self-migrates on boot (log stream shows alembic).
7. Local docker compose still runs untouched (dev/prod parity held).
8. Budget alert exists. PROJECT_STATE.md updated with the prod section.

## Out of scope

Custom domain (later, optional), the push pilot itself (next stage, now
single-phase against this URL), bridge migration (Stage 8), Blob storage,
autoscaling, staging slots.
