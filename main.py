"""
Green Recruiters - Application Entrypoint
Wires together the database pool, routers, and static file serving.
Run with: uvicorn main:app --host 0.0.0.0 --port 8000
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db
from applications import router as applications_router
from cms_router import router as cms_router

FRONTEND_DIR = Path(__file__).parent
ASSET_STORAGE_ROOT = Path(os.environ.get("CMS_ASSET_PATH", "/var/greenrecruiters/assets"))
ASSET_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

# Paths that count as meaningful page visits for the traffic dashboard.
TRACKED_PATHS = {"/", "/apply"}

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    yield
    db.close_pool()


app = FastAPI(title="Green Recruiters", lifespan=lifespan)

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

@app.middleware("http")
async def log_page_views(request: Request, call_next):
    """Records a page view for tracked public-facing paths. Best-effort: never blocks the request."""
    response = await call_next(request)
    if request.method == "GET" and request.url.path in TRACKED_PATHS:
        try:
            db.execute(
                "INSERT INTO page_views (path) VALUES (%s)",
                (request.url.path,),
            )
        except Exception:
            pass
    return response


app.include_router(applications_router)
app.include_router(cms_router)

# CMS-uploaded assets (hero image, favicon, email logos, home logo, home company image).
app.mount("/assets", StaticFiles(directory=ASSET_STORAGE_ROOT), name="assets")


# ============================================================
# Frontend Pages
# ============================================================

@app.get("/")
async def serve_home_page():
    """Independent public marketing home page. No authentication."""
    return FileResponse(FRONTEND_DIR / "home.html")


@app.get("/apply")
async def serve_application_page():
    """Public recruitment form. No authentication."""
    return FileResponse(FRONTEND_DIR / "application.html")


@app.get("/admin")
async def serve_login_page():
    """CMS login entry point (email + OTP)."""
    return FileResponse(FRONTEND_DIR / "login.html")


@app.get("/cms")
async def serve_cms_page():
    """
    CMS administration panel. Auth is enforced client-side by
    redirecting to /admin if no valid session token is present,
    and server-side on every /api/cms/* call.
    """
    return FileResponse(FRONTEND_DIR / "cms.html")


@app.get("/analyst")
async def serve_analyst_page():
    """
    Traffic analytics dashboard (visits + application submission stats).
    Auth is enforced client-side by redirecting to /admin if no valid
    session token is present, and server-side on every /api/cms/* call.
    """
    return FileResponse(FRONTEND_DIR / "analyst.html")
