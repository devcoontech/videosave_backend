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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} FastAPI backend...")

    # Verify FFmpeg status on startup
    if ffmpeg_service.is_available():
        logger.info("FFmpeg integration initialized and ready.")
    else:
        logger.warning(
            "FFmpeg is not available in system PATH or configured FFMPEG_PATH. "
            "Video+Audio merging or complex conversions may fail."
        )

    # Start background temporary file cleanup worker
    cleanup_worker.start()
    yield

    logger.info("Shutting down backend...")
    cleanup_worker.stop()


app = FastAPI(
    title=settings.APP_NAME,
    description="Production-quality multi-platform video downloader API supporting YouTube, Instagram, and Facebook.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS configuration
origins = [
    settings.FRONTEND_URL,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include main API router
app.include_router(api_router)


@app.get("/api/health", tags=["Health"])
async def health_check():
    """Health check endpoint returning system status and FFmpeg availability."""
    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "ffmpeg_available": ffmpeg_service.is_available(),
    }
