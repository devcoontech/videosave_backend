import os
import shutil
import subprocess
from backend.app.core.config import settings
from backend.app.core.logging import logger


class FFmpegService:
    def __init__(self):
        self._ffmpeg_bin = self._resolve_ffmpeg()

    def _resolve_ffmpeg(self) -> str | None:
        """Locate ffmpeg binary on system PATH or via FFMPEG_PATH config."""
        # 1. System PATH check
        ffmpeg_in_path = shutil.which("ffmpeg")
        if ffmpeg_in_path:
            logger.info(f"FFmpeg found on system PATH: {ffmpeg_in_path}")
            return ffmpeg_in_path

        # 2. Config FFMPEG_PATH check
        if settings.FFMPEG_PATH:
            path = settings.FFMPEG_PATH.strip()
            # If path points directly to directory or executable
            if os.path.isdir(path):
                exe_path = os.path.join(path, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
                if os.path.exists(exe_path):
                    logger.info(f"FFmpeg found at configured FFMPEG_PATH directory: {exe_path}")
                    return exe_path
            elif os.path.isfile(path):
                logger.info(f"FFmpeg found at configured FFMPEG_PATH file: {path}")
                return path

        logger.warning("FFmpeg not explicitly found in PATH or config settings.")
        return None

    def get_ffmpeg_location(self) -> str | None:
        if self._ffmpeg_bin:
            return os.path.dirname(self._ffmpeg_bin) if os.path.isfile(self._ffmpeg_bin) else self._ffmpeg_bin
        return settings.FFMPEG_PATH or None

    def is_available(self) -> bool:
        if not self._ffmpeg_bin:
            return False
        try:
            res = subprocess.run(
                [self._ffmpeg_bin, "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=8,
            )
            return res.returncode == 0
        except Exception as e:
            logger.error(f"Failed to execute FFmpeg check: {e}")
            return False


ffmpeg_service = FFmpegService()
