import os
import sys
import time
import uuid
import asyncio
from typing import Dict, Optional, Set
from fastapi import WebSocket

from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.models.jobs import DownloadJob, JobStatus
from backend.app.utils.urls import detect_platform
from backend.app.utils.filenames import sanitize_filename
from backend.app.services.ffmpeg_service import ffmpeg_service
import yt_dlp

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOWNLOADS_PATH = os.path.join(BASE_DIR, settings.DOWNLOAD_DIR)
TEMP_PATH = os.path.join(BASE_DIR, settings.TEMP_DIR)
os.makedirs(DOWNLOADS_PATH, exist_ok=True)
os.makedirs(TEMP_PATH, exist_ok=True)


class DownloadManager:
    def __init__(self):
        self.jobs: Dict[str, DownloadJob] = {}
        self.websocket_listeners: Dict[str, Set[WebSocket]] = {}
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)

    def get_job(self, job_id: str) -> Optional[DownloadJob]:
        return self.jobs.get(job_id)

    def cancel_job(self, job_id: str):
        job = self.get_job(job_id)
        if job and job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
            job.status = JobStatus.CANCELLED
            job.error = "Download cancelled by user."
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.broadcast_job_update(job), loop)
            except Exception:
                pass

    async def register_websocket(self, job_id: str, websocket: WebSocket):
        if job_id not in self.websocket_listeners:
            self.websocket_listeners[job_id] = set()
        self.websocket_listeners[job_id].add(websocket)
        job = self.get_job(job_id)
        if job:
            await self.broadcast_job_update(job)

    def unregister_websocket(self, job_id: str, websocket: WebSocket):
        if job_id in self.websocket_listeners:
            self.websocket_listeners[job_id].discard(websocket)
            if not self.websocket_listeners[job_id]:
                del self.websocket_listeners[job_id]

    async def broadcast_job_update(self, job: DownloadJob):
        listeners = self.websocket_listeners.get(job.id, set())
        if not listeners:
            return
        payload = {
            "job_id": job.id,
            "status": job.status.value,
            "progress": job.progress,
            "speed": job.speed,
            "eta": job.eta,
            "downloaded_bytes": job.downloaded_bytes,
            "total_bytes": job.total_bytes,
            "filename": job.filename,
            "error": job.error,
            "queue_position": job.queue_position,
        }
        to_remove = set()
        for ws in listeners:
            try:
                await ws.send_json(payload)
            except Exception:
                to_remove.add(ws)
        for ws in to_remove:
            listeners.discard(ws)

    def create_job(self, url: str, format_id: str = "best") -> DownloadJob:
        # Enforce maximum active + queued jobs limit to prevent memory/queue overflow
        active_count = sum(
            1 for j in self.jobs.values()
            if j.status in (JobStatus.QUEUED, JobStatus.EXTRACTING, JobStatus.DOWNLOADING, JobStatus.PROCESSING)
        )
        if active_count >= 25:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=429,
                detail={"code": "QUEUE_FULL", "message": "Server download queue is currently full. Please try again in a few minutes."}
            )

        platform = detect_platform(url)
        job_id = str(uuid.uuid4())

        queued_jobs = [j for j in self.jobs.values() if j.status == JobStatus.QUEUED]
        q_pos = len(queued_jobs) + 1 if active_count >= settings.MAX_CONCURRENT_DOWNLOADS else None

        job = DownloadJob(
            id=job_id,
            url=url,
            platform=platform,
            status=JobStatus.QUEUED,
            progress=0.0,
            queue_position=q_pos,
            created_at=time.time(),
        )
        self.jobs[job_id] = job
        return job

    async def start_job(self, job_id: str, format_id: str = "best", custom_title: Optional[str] = None):
        job = self.get_job(job_id)
        if not job or job.status == JobStatus.CANCELLED:
            return

        async with self.semaphore:
            if job.status == JobStatus.CANCELLED:
                return
            job.queue_position = None
            await self._run_download_task(job, format_id, custom_title)

    async def _run_download_task(self, job: DownloadJob, format_id: str, custom_title: Optional[str] = None):
        loop = asyncio.get_running_loop()
        job.status = JobStatus.EXTRACTING
        await self.broadcast_job_update(job)

        output_dir = str(DOWNLOADS_PATH)

        # Progress callback hook for yt-dlp
        def progress_hook(d):
            if job.status == JobStatus.CANCELLED:
                raise Exception("DOWNLOAD_CANCELLED")

            if d["status"] == "downloading":
                job.status = JobStatus.DOWNLOADING
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                job.downloaded_bytes = downloaded
                job.total_bytes = total

                if total and total > 0:
                    job.progress = round((downloaded / total) * 100, 1)

                speed_bytes = d.get("speed")
                if speed_bytes:
                    job.speed = f"{speed_bytes / (1024 * 1024):.2f} MB/s"

                eta_sec = d.get("eta")
                if eta_sec is not None:
                    m, s = divmod(int(eta_sec), 60)
                    h, m = divmod(m, 60)
                    job.eta = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

                asyncio.run_coroutine_threadsafe(self.broadcast_job_update(job), loop)

            elif d["status"] == "finished":
                job.status = JobStatus.PROCESSING
                job.progress = 99.0
                asyncio.run_coroutine_threadsafe(self.broadcast_job_update(job), loop)

        # Build strict format selector prioritizing exact requested quality
        if not format_id or format_id == "best":
            fmt_str = "bestvideo+bestaudio/best"
            fmt_sort = ["res", "fps", "codec:h264", "size"]
        else:
            clean_h = format_id.rstrip("p")
            if clean_h.isdigit():
                h_val = int(clean_h)
                fmt_str = (
                    f"bestvideo[height<={h_val}]+bestaudio/"
                    f"best[height<={h_val}]/"
                    f"bestvideo+bestaudio/best"
                )
                fmt_sort = [f"res:{h_val}", "fps", "size"]
            else:
                fmt_str = (
                    f"{format_id}+bestaudio/"
                    f"bestvideo[format_id={format_id}]+bestaudio/"
                    f"{format_id}/"
                    f"bestvideo+bestaudio/best"
                )
                fmt_sort = ["res", "fps", "codec:h264", "size"]

        from backend.app.services.extractor import get_platform_headers

        ydl_opts = {
            "format": fmt_str,
            "format_sort": fmt_sort,
            "outtmpl": os.path.join(output_dir, f"%(title)s [{job.id[:8]}].%(ext)s"),
            "merge_output_format": "mp4",
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "socket_timeout": 60,
            "overwrites": True,
            "continuedl": False,
            "noplaylist": True,
            "http_headers": get_platform_headers(job.url),
            "extractor_args": {
                "tiktok": {
                    "app_version": "33.0.0",
                    "manifest_app_version": "33000",
                },
            },
        }




        cookie_file = os.path.join(BASE_DIR, "cookies.txt")
        if os.path.exists(cookie_file):
            ydl_opts["cookiefile"] = cookie_file

        ffmpeg_loc = ffmpeg_service.get_ffmpeg_location()
        if ffmpeg_loc:
            ydl_opts["ffmpeg_location"] = ffmpeg_loc

        try:
            def _execute_ydl():
                if job.status == JobStatus.CANCELLED:
                    raise Exception("DOWNLOAD_CANCELLED")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(job.url, download=True)
                    filename = ydl.prepare_filename(info)
                    if not os.path.exists(filename):
                        base, _ = os.path.splitext(filename)
                        if os.path.exists(base + ".mp4"):
                            filename = base + ".mp4"
                    return info, filename

            # Execute with a 10-minute maximum hard timeout per download job
            info, final_filepath = await asyncio.wait_for(
                asyncio.to_thread(_execute_ydl),
                timeout=600
            )

            if job.status == JobStatus.CANCELLED:
                raise Exception("DOWNLOAD_CANCELLED")

            if not os.path.exists(final_filepath):
                title = info.get("title", "")
                safe_t = sanitize_filename(title)
                for f in os.listdir(output_dir):
                    if safe_t.lower() in f.lower() or job.id in f:
                        final_filepath = os.path.join(output_dir, f)
                        break

            job.status = JobStatus.COMPLETED
            job.progress = 100.0
            title_clean = sanitize_filename(info.get("title", "Downloaded Video"))
            ext_clean = final_filepath.split(".")[-1] if "." in final_filepath else "mp4"
            height = info.get("height")
            quality_tag = f" ({height}p)" if height else (f" ({format_id})" if format_id and format_id != "best" else "")
            job.title = info.get("title", "Downloaded Video")
            job.filepath = final_filepath
            job.filename = f"{title_clean}{quality_tag}.{ext_clean}"
            job.completed_at = time.time()
            logger.info(f"Job {job.id} completed successfully: {job.filename}")
            await self.broadcast_job_update(job)

        except Exception as e:
            # Clean up any partial files immediately on failure or cancellation
            self._cleanup_job_files(job.id, output_dir)
            if "DOWNLOAD_CANCELLED" in str(e) or job.status == JobStatus.CANCELLED:
                job.status = JobStatus.CANCELLED
                job.error = "Download stopped by user."
                logger.info(f"Job {job.id} stopped cleanly by user.")
            elif isinstance(e, asyncio.TimeoutError):
                logger.error(f"Download timed out for job {job.id}")
                job.status = JobStatus.FAILED
                job.error = "Download request timed out (exceeded 10 minutes limit)."
            else:
                logger.error(f"Download failed for job {job.id}: {e}")
                job.status = JobStatus.FAILED
                job.error = str(e)
            await self.broadcast_job_update(job)


download_manager = DownloadManager()
