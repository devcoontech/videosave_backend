import asyncio
import time
import os
from backend.app.core.config import settings, DOWNLOADS_PATH, TEMP_PATH
from backend.app.core.logging import logger


class CleanupWorker:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self.retention_seconds = settings.FILE_RETENTION_MINUTES * 60

    def start(self):
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._run_loop())
            logger.info("Cleanup background worker started.")

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Cleanup background worker stopped.")

    async def _run_loop(self):
        while True:
            try:
                await asyncio.sleep(300)  # Run cleanup every 5 minutes
                self.clean_expired_files()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during file cleanup cycle: {e}")

    def clean_expired_files(self):
        now = time.time()
        cleaned_count = 0

        for target_dir in [DOWNLOADS_PATH, TEMP_PATH]:
            if not target_dir.exists():
                continue
            for item in target_dir.iterdir():
                if item.is_file():
                    try:
                        file_age = now - item.stat().st_mtime
                        if file_age > self.retention_seconds:
                            item.unlink()
                            cleaned_count += 1
                    except Exception as e:
                        logger.error(f"Could not delete expired file {item}: {e}")

        if cleaned_count > 0:
            logger.info(f"Cleanup worker deleted {cleaned_count} expired temporary file(s).")


cleanup_worker = CleanupWorker()
