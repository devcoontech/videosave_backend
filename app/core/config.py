import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "StreamVault"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    FFMPEG_PATH: str = "ffmpeg"
    DOWNLOAD_DIR: str = "downloads"
    TEMP_DIR: str = "temp"

    FILE_RETENTION_MINUTES: int = 30
    MAX_CONCURRENT_DOWNLOADS: int = 2
    MAX_QUEUED_JOBS: int = 25
    DOWNLOAD_TIMEOUT_SECONDS: int = 3600
    MAX_PLAYLIST_ITEMS: int = 100
    MAX_DOWNLOAD_SIZE_GB: float = 10.0
    MIN_FREE_DISK_GB: float = 5.0

    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = ""

    # Optional path to Netscape cookies.txt (YouTube/Facebook on VPS IPs)
    COOKIES_FILE: str = ""
    # Optional YouTube PO token, e.g. "android.gvs+TOKEN" (see yt-dlp PO Token Guide)
    YOUTUBE_PO_TOKEN: str = ""
    # Optional bgutil PO token HTTP server, e.g. http://127.0.0.1:4416
    BGUTIL_POT_BASE_URL: str = ""
    # On VPS, home-exported cookies often cause IP-mismatch blocks — keep False (anonymous clients first).
    YOUTUBE_COOKIES_FIRST: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# Resolve from the real package path so Docker symlinks (/app/backend/app -> /app/app)
# do not create a second downloads folder that the file route then rejects.
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _resolve_data_dir(value: str) -> Path:
    raw = Path(value)
    path = raw if raw.is_absolute() else (BASE_DIR / raw)
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


DOWNLOADS_PATH = _resolve_data_dir(settings.DOWNLOAD_DIR)
TEMP_PATH = _resolve_data_dir(settings.TEMP_DIR)
