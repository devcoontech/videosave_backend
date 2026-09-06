import sys
from pathlib import Path

# Automatically add project root & backend folder to sys.path
backend_dir = Path(__file__).resolve().parent.parent
root_dir = backend_dir.parent
for d in [str(root_dir), str(backend_dir)]:
    if d not in sys.path:
        sys.path.insert(0, d)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.api.router import api_router
from backend.app.services.cleanup_service import cleanup_worker
from backend.app.services.ffmpeg_service import ffmpeg_service


import time
from typing import Dict, List
from fastapi import Request, Response, HTTPException, status, WebSocket
from backend.app.services.download_service import download_manager
from backend.app.api.routes.websocket import websocket_download_progress

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} FastAPI backend...")

    # Resolve FFmpeg without blocking startup on a slow -version probe
    if ffmpeg_service.get_ffmpeg_location():
        logger.info("FFmpeg integration initialized and ready.")
    else:
        logger.warning(
            "FFmpeg is not available in system PATH or configured FFMPEG_PATH. "
            "Video+Audio merging or complex conversions may fail."
        )

    # Start background temporary file cleanup worker
    cleanup_worker.start()
    yield

    logger.info("Shutting down backend cleanly...")
    # Actively cancel running jobs to release file handles and lock slots
    for j_id in list(download_manager.jobs.keys()):
        try:
            download_manager.cancel_job(j_id)
        except Exception:
            pass
    cleanup_worker.stop()


app = FastAPI(
    title=settings.APP_NAME,
    description="Production-quality multi-platform video downloader API supporting YouTube, Instagram, and Facebook.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Lightweight Production IP Rate Limiting Middleware (30 req / min per IP)
CLIENT_REQUEST_LOGS: Dict[str, List[float]] = {}

def get_client_ip(request: Request) -> str:
    """Extract real client IP behind trusted reverse proxies (Coolify / Nginx)."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"

def is_progress_poll(request: Request) -> bool:
    """Status polling must not count toward the API rate limit."""
    if request.method != "GET":
        return False
    parts = request.url.path.strip("/").split("/")
    if parts[:2] == ["api", "download"] and len(parts) == 3:
        return True
    if parts[:3] == ["api", "playlist", "job"] and len(parts) == 4:
        return True
    return False


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only rate-limit API action endpoints
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/health") and not is_progress_poll(request):
        client_ip = get_client_ip(request)
        now = time.time()
        
        # Clean request timestamps older than 60s
        timestamps = [t for t in CLIENT_REQUEST_LOGS.get(client_ip, []) if now - t < 60]
        if len(timestamps) >= 30:  # Max 30 API requests per minute per IP
            return Response(
                content='{"detail":{"code":"RATE_LIMIT_EXCEEDED","message":"Too many requests. Please slow down and try again in a minute."}}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
            )
        timestamps.append(now)
        CLIENT_REQUEST_LOGS[client_ip] = timestamps

        # Periodic memory cleanup of stale IP logs
        if len(CLIENT_REQUEST_LOGS) > 1000:
            stale_ips = [ip for ip, ts in CLIENT_REQUEST_LOGS.items() if not ts or (now - ts[-1] > 3600)]
            for ip in stale_ips:
                CLIENT_REQUEST_LOGS.pop(ip, None)

    response = await call_next(request)
    return response

# Dynamic CORS configuration
allowed_origins: List[str] = []

# Populate configured CORS origins
if settings.CORS_ORIGINS:
    for origin in settings.CORS_ORIGINS.split(","):
        o = origin.strip().rstrip("/")
        if o and o not in allowed_origins:
            allowed_origins.append(o)

if settings.FRONTEND_URL:
    clean_frontend_url = settings.FRONTEND_URL.rstrip("/")
    if clean_frontend_url not in allowed_origins:
        allowed_origins.append(clean_frontend_url)
    if not clean_frontend_url.startswith("http://") and not clean_frontend_url.startswith("https://"):
        allowed_origins.append(f"https://{clean_frontend_url}")

# Default development fallbacks if no origins specified
if not allowed_origins or settings.ENVIRONMENT.lower() == "development":
    dev_defaults = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    for d in dev_defaults:
        if d not in allowed_origins:
            allowed_origins.append(d)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include main API router
app.include_router(api_router)


@app.websocket("/ws/download/{job_id}")
async def websocket_download_progress_root(websocket: WebSocket, job_id: str):
    """Browser clients connect to ws://host:8000/ws/download/{job_id}."""
    await websocket_download_progress(websocket, job_id)


@app.get("/api/health", tags=["Health"])
async def health_check():
    """Health check endpoint returning system status and FFmpeg availability."""
    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "ffmpeg_available": ffmpeg_service.is_available(),
    }
