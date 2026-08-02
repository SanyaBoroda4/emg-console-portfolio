"""FastAPI app factory: CORS, routers, auth error shapes, prod static serving."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.routers import (
    audit,
    auth,
    checks,
    decisions,
    deliveries,
    health,
    hooks,
    jobs,
    materials,
    push,
    review_items,
    scans,
    slab_hooks,
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="EMG Ops Console")

    # Local dev serves the frontend from the Vite dev server on a different
    # origin, so the browser needs CORS. In production FastAPI serves the built
    # bundle from the SAME origin (see _mount_frontend), so no cross-origin is
    # allowed at all — nothing legitimate calls this API from another site.
    if settings.environment != "production":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://localhost:5173"],
            # POST: uploads/auth · DELETE: rows · PATCH: Stage 3 edits.
            allow_methods=["GET", "POST", "DELETE", "PATCH"],
            allow_headers=["*"],
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Auth errors carry dict details ({"error": ..., "message": ...}) that
        # the plan specifies as TOP-LEVEL response bodies; plain-string details
        # keep FastAPI's default {"detail": ...} shape.
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(review_items.router)
    app.include_router(checks.router)
    app.include_router(audit.router)
    app.include_router(push.router)
    app.include_router(hooks.router)
    app.include_router(decisions.router)
    app.include_router(jobs.router)
    app.include_router(deliveries.router)
    app.include_router(slab_hooks.router)
    app.include_router(scans.router)
    app.include_router(materials.router)

    # Jobs directory sync thread (no-op without BRIDGE_CONSOLE_KEY).
    from app.jobs_sync import start_jobs_sync

    start_jobs_sync()

    # Registered LAST so the SPA catch-all never shadows an /api route.
    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """Serve the built React bundle from this same app (production single-origin).

    FRONTEND_DIST points at the Vite build output. Empty in local dev, where
    Vite serves the frontend itself — so this is a no-op and the app stays
    API-only. In production: hashed assets are served directly, and every other
    non-/api path falls back to index.html so react-router can handle deep
    links (e.g. /payments/audit) on a hard refresh.
    """
    if not settings.frontend_dist:
        return

    dist = Path(settings.frontend_dist).resolve()
    index_file = dist / "index.html"
    if not index_file.is_file():
        # Fail loud at startup rather than silently 404 the entire UI.
        raise RuntimeError(
            f"FRONTEND_DIST={settings.frontend_dist!r} has no index.html "
            "— check the deploy build layout."
        )

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # /api/* belongs to the routers above; a miss there is a genuine 404,
        # not a client-side route — keep it JSON, not the HTML shell.
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        # Serve real files that live at the dist root (favicon, vite.svg, …),
        # guarding against path traversal escaping the dist directory.
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist):
                return FileResponse(candidate)
        return FileResponse(index_file)


app = create_app()
