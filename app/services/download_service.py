import os
import time
import uuid
import asyncio
from typing import Dict, Optional, Set
from fastapi import WebSocket
import yt_dlp
import shutil

from backend.app.core.config import settings, DOWNLOADS_PATH
from backend.app.core.logging import logger
from backend.app.models.jobs import DownloadJob, JobStatus
from backend.app.utils.urls import detect_platform
from backend.app.utils.filenames import sanitize_filename
from backend.app.services.ytdlp_common import (
    base_ydl_opts,
    format_selector,
    is_bot_challenge,
)


class DownloadManager:
    def __init__(self):
        self.jobs: Dict[str, DownloadJob] = {}
        self.websocket_listeners: Dict[str, Set[WebSocket]] = {}
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)

    def _prune_stale_jobs(self):
        """Remove completed/failed/cancelled jobs older than 2 hours to prevent memory leak."""
        now = time.time()
        stale_ids = [
            j_id for j_id, j in self.jobs.items()
            if j.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
            and (now - j.created_at > 7200)
        ]
        for j_id in stale_ids:
            self.jobs.pop(j_id, None)

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
        self._prune_stale_jobs()
        # Enforce free disk space threshold before creating job
        try:
            free_bytes = shutil.disk_usage(str(DOWNLOADS_PATH)).free
            min_bytes = int(settings.MIN_FREE_DISK_GB * 1024 * 1024 * 1024)
            if free_bytes < min_bytes:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=507,
                    detail={"code": "INSUFFICIENT_STORAGE", "message": "Server disk space is low. Please try again later."}
                )
        except HTTPException:
            raise
        except Exception:
            pass

        # Enforce maximum active + queued jobs limit
        active_count = sum(
            1 for j in self.jobs.values()
            if j.status in (JobStatus.QUEUED, JobStatus.EXTRACTING, JobStatus.DOWNLOADING, JobStatus.PROCESSING)
        )
        if active_count >= settings.MAX_QUEUED_JOBS:
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

        is_mp3 = (format_id or "").lower() in ("mp3", "audio", "bestaudio")
        platform = detect_platform(job.url)
        fmt_str = format_selector(job.url, format_id)
        quality_slug = sanitize_filename("MP3" if is_mp3 else (format_id if format_id and format_id != "best" else "best"))

        ydl_opts = base_ydl_opts(
            job.url,
            {
                "format": fmt_str,
                "outtmpl": os.path.join(output_dir, f"%(title)s - {quality_slug} [{job.id[:8]}].%(ext)s"),
                "progress_hooks": [progress_hook],
                "overwrites": True,
                "continuedl": False,
            },
        )

        if is_mp3:
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        elif platform not in ("facebook", "instagram", "tiktok"):
            ydl_opts["merge_output_format"] = "mp4"

        try:
            def _execute_ydl(player_clients: Optional[list] = None):
                if job.status == JobStatus.CANCELLED:
                    raise Exception("DOWNLOAD_CANCELLED")
                opts = dict(ydl_opts)
                if player_clients:
                    youtube_args = dict(opts.get("extractor_args", {}).get("youtube", {}))
                    youtube_args["player_client"] = player_clients
                    opts.setdefault("extractor_args", {})["youtube"] = youtube_args
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(job.url, download=True)
                    filename = ydl.prepare_filename(info)
                    if not os.path.exists(filename):
                        base, _ = os.path.splitext(filename)
                        for ext in (".mp3", ".mp4", ".m4a", ".webm"):
                            if os.path.exists(base + ext):
                                filename = base + ext
                                break
                    return info, filename

            def _download_with_retry():
                try:
                    return _execute_ydl()
                except yt_dlp.utils.DownloadError as err:
                    if is_bot_challenge(str(err)) and detect_platform(job.url) == "youtube":
                        logger.warning(f"YouTube bot challenge on download, retrying with android client: {job.id}")
                        return _execute_ydl(player_clients=["android", "ios"])
                    raise

            info, final_filepath = await asyncio.wait_for(
                asyncio.to_thread(_download_with_retry),
                timeout=settings.DOWNLOAD_TIMEOUT_SECONDS,
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

            if is_mp3:
                base, _ = os.path.splitext(final_filepath)
                if os.path.exists(base + ".mp3"):
                    final_filepath = base + ".mp3"

            job.status = JobStatus.COMPLETED
            job.progress = 100.0
            title_clean = sanitize_filename(info.get("title", "Downloaded Video"))
            if is_mp3:
                quality_label = "MP3"
                ext_clean = "mp3"
            else:
                ext_clean = final_filepath.split(".")[-1] if "." in final_filepath else "mp4"
                height = info.get("height")
                if format_id and format_id != "best":
                    quality_label = format_id if str(format_id).lower().endswith("p") else f"{format_id}p"
                elif height:
                    quality_label = f"{int(height)}p"
                else:
                    quality_label = "best"
            job.title = info.get("title", "Downloaded Video")
            job.filepath = final_filepath
            job.filename = f"{title_clean} - {quality_label}.{ext_clean}"
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
            elif isinstance(e, yt_dlp.utils.DownloadError) and is_bot_challenge(str(e)):
                job.status = JobStatus.FAILED
                job.error = "YouTube blocked this download. Try again later or add cookies.txt on the server."
            else:
                logger.error(f"Download failed for job {job.id}: {e}")
                job.status = JobStatus.FAILED
                err_text = str(e)
                if len(err_text) > 200:
                    err_text = err_text[:200] + "..."
                job.error = err_text or "Download failed."
            await self.broadcast_job_update(job)

    def _cleanup_job_files(self, job_id: str, output_dir: str):
        marker = job_id[:8]
        try:
            for name in os.listdir(output_dir):
                if marker in name:
                    try:
                        os.remove(os.path.join(output_dir, name))
                    except OSError:
                        pass
        except OSError:
            pass


download_manager = DownloadManager()
