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
from backend.app.services.ytdlp_common import (
    bgutil_script_available,
    bgutil_script_home,
    bgutil_script_runnable,
    cookies_diagnostics,
    cookies_file,
    has_impersonate,
    pot_provider_ready,
)


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

    cookies_path = cookies_file()
    cookie_info = cookies_diagnostics()
    if cookies_path:
        logger.info(f"YouTube/Facebook cookies loaded from: {cookies_path}")
        if cookie_info.get("youtube_entries", 0) == 0:
            logger.warning("cookies.txt has no .youtube.com entries.")
    else:
        logger.warning(
            "No cookies.txt found. YouTube downloads will likely fail on VPS/datacenter IPs. "
            "Mount cookies.txt at /app/cookies.txt (see cookies.txt.example)."
        )
    if has_impersonate():
        logger.info("curl-cffi browser impersonation is available (Facebook).")

    if cookie_info.get("has_login_info") or cookie_info.get("has_sid"):
        logger.info("YouTube logged-in cookies detected (age-restricted videos supported).")
    elif cookies_path:
        logger.warning("cookies.txt present but missing YouTube SID/LOGIN_INFO.")

    bgutil_url = settings.BGUTIL_POT_BASE_URL or ""
    if bgutil_url:
        import urllib.request
        try:
            with urllib.request.urlopen(bgutil_url.rstrip("/") + "/ping", timeout=3) as resp:
                if resp.status == 200:
                    logger.info(f"bgutil PO token server reachable at {bgutil_url}")
                else:
                    logger.warning(f"bgutil PO token server returned HTTP {resp.status}")
        except Exception as exc:
            if bgutil_script_available():
                logger.warning(
                    f"bgutil HTTP server not reachable at {bgutil_url} ({exc}); "
                    f"using script fallback at {bgutil_script_home()}"
                )
            else:
                logger.warning(
                    f"bgutil PO token server not reachable at {bgutil_url} ({exc}). "
                    "YouTube downloads will likely fail on datacenter IPs."
                )
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
    import urllib.request
    import urllib.error
    import yt_dlp

    cookies_path = cookies_file()
    cookie_info = cookies_diagnostics()

    bgutil_url = settings.BGUTIL_POT_BASE_URL or ""
    bgutil_reachable = False
    if bgutil_url:
        ping_url = bgutil_url.rstrip("/") + "/ping"
        try:
            with urllib.request.urlopen(ping_url, timeout=2) as resp:
                bgutil_reachable = resp.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            bgutil_reachable = False

    script_home = bgutil_script_home()
    script_available = bgutil_script_available()
    script_runnable = bgutil_script_runnable() if script_available else False

    youtube_ready = (
        bgutil_reachable
        or script_runnable
        or bool(cookie_info["has_login_info"] or cookie_info["has_sid"])
        or not bgutil_url
    )

    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "ffmpeg_available": ffmpeg_service.is_available(),
        "yt_dlp_version": yt_dlp.version.__version__,
        "cookies_configured": cookie_info["configured"],
        "cookies_path": cookies_path,
        "cookies_youtube_entries": cookie_info["youtube_entries"],
        "cookies_has_login": cookie_info["has_login_info"] or cookie_info["has_sid"],
        "cookies_instagram_entries": cookie_info["instagram_entries"],
        "cookies_facebook_entries": cookie_info["facebook_entries"],
        "cookies_tiktok_entries": cookie_info["tiktok_entries"],
        "impersonate_available": has_impersonate(),
        "bgutil_pot_url": bgutil_url or None,
        "bgutil_reachable": bgutil_reachable,
        "bgutil_script_home": script_home,
        "bgutil_script_available": script_available,
        "bgutil_script_runnable": script_runnable,
        "pot_provider_ready": pot_provider_ready(),
        "youtube_ready": youtube_ready,
    }
