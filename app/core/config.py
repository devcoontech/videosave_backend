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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# Ensure directories exist
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DOWNLOADS_PATH = BASE_DIR / settings.DOWNLOAD_DIR
TEMP_PATH = BASE_DIR / settings.TEMP_DIR

DOWNLOADS_PATH.mkdir(parents=True, exist_ok=True)
TEMP_PATH.mkdir(parents=True, exist_ok=True)
