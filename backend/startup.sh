#!/usr/bin/env bash
# Production startup for Azure App Service (Linux). Set as the Web App's
# "Startup Command": bash startup.sh. Local dev does NOT use this — it runs
# uvicorn via docker compose.
set -euo pipefail

# Resolve everything relative to THIS script's directory. Depending on Azure
# settings the deployed files run either directly from /home/site/wwwroot or,
# when Oryx build-during-deploy is on, from an extracted /tmp/<id> directory —
# computing paths from the script location works in both, so we never depend on
# a hardcoded absolute path or on the runtime working directory.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Dependencies are installed at BUILD time by .github/workflows/deploy.yml into
# pydeps/ (inside a Debian bullseye container that matches the App Service
# image's glibc) and shipped inside the package — so the app runs with NO
# server-side build and NO glibc mismatch. Put the app package and vendored deps on
# the import path; force it here so a stale FRONTEND_DIST/PYTHONPATH in the
# portal cannot break the boot.
export PYTHONPATH="$HERE:$HERE/pydeps:${PYTHONPATH:-}"

# Serve the built frontend from next to this script (single-origin). Set here so
# it is correct whether we run from wwwroot or Oryx's /tmp extract dir.
export FRONTEND_DIST="$HERE/frontend_dist"

# Bring the database to the latest schema before serving. Safe on every boot:
# alembic is a no-op once already at head. If a migration fails the app does not
# start — better than serving against a half-migrated database. Invoked as a
# module so it resolves from PYTHONPATH without needing a bin/ dir on PATH.
python -m alembic upgrade head

# Gunicorn with Uvicorn workers: 2 workers on the B1 single vCPU. App Service
# terminates TLS ahead of us, so trust its forwarded headers — that keeps
# request.scheme = https (Secure cookies, no http downgrades on redirects).
# Bind to $PORT when App Service provides it, else the local default 8000.
exec python -m gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 2 \
  --bind "0.0.0.0:${PORT:-8000}" \
  --forwarded-allow-ips '*' \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
