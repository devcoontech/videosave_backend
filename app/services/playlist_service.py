import asyncio
import uuid
import time
from typing import List, Dict, Any, Optional
import yt_dlp
from fastapi import HTTPException, status
from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.models.playlist import PlaylistItem, PlaylistInfoResponse
from backend.app.models.jobs import JobStatus
from backend.app.services.download_service import download_manager
from backend.app.services.ffmpeg_service import ffmpeg_service


class PlaylistService:
    def __init__(self):
        self.playlist_jobs: Dict[str, dict] = {}

    def _sync_extract_playlist(self, url: str) -> Dict[str, Any]:
        ydl_opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
        }
        ffmpeg_loc = ffmpeg_service.get_ffmpeg_location()
        if ffmpeg_loc:
            ydl_opts["ffmpeg_location"] = ffmpeg_loc

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise Exception("Unable to extract playlist information.")
                return info
        except Exception as e:
            logger.error(f"Playlist extraction error: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PLAYLIST_EXTRACTION_FAILED", "message": f"Failed to extract playlist: {str(e)}"},
            )

    async def get_playlist_info(self, url: str) -> PlaylistInfoResponse:
        info = await asyncio.to_thread(self._sync_extract_playlist, url)
        playlist_id = info.get("id", "playlist")
        title = info.get("title", "YouTube Playlist")
        uploader = info.get("uploader") or info.get("channel") or "Unknown Channel"

        entries = info.get("entries", [])
        if settings.MAX_PLAYLIST_ITEMS > 0 and len(entries) > settings.MAX_PLAYLIST_ITEMS:
            entries = entries[:settings.MAX_PLAYLIST_ITEMS]

        videos: List[PlaylistItem] = []

        index = 1
        for entry in entries:
            if not entry:
                continue
            v_id = entry.get("id")
            v_title = entry.get("title", f"Video {index}")
            v_url = entry.get("url") or f"https://www.youtube.com/watch?v={v_id}"
            raw_dur = entry.get("duration")
            duration = round(float(raw_dur)) if raw_dur is not None else None
            thumb = entry.get("thumbnail") or (entry.get("thumbnails", [{}])[-1].get("url") if entry.get("thumbnails") else None)

            videos.append(
                PlaylistItem(
                    id=v_id or str(index),
                    url=v_url,
                    title=v_title,
                    thumbnail=thumb,
                    duration=duration,
                    index=index,
                )
            )
            index += 1

        return PlaylistInfoResponse(
            success=True,
            playlist_id=playlist_id,
            title=title,
            uploader=uploader,
            total_videos=len(videos),
            videos=videos,
        )

    async def create_playlist_job(
        self, video_urls: List[str], format_id: str = "best", existing_job_id: Optional[str] = None
    ) -> str:
        if settings.MAX_PLAYLIST_ITEMS > 0 and len(video_urls) > settings.MAX_PLAYLIST_ITEMS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "PLAYLIST_LIMIT_EXCEEDED",
                    "message": f"Playlist size exceeds maximum limit of {settings.MAX_PLAYLIST_ITEMS} videos.",
                },
            )

        # Resume existing playlist job if available
        if existing_job_id and existing_job_id in self.playlist_jobs:
            pj = self.playlist_jobs[existing_job_id]
            pj["status"] = "in_progress"
            asyncio.create_task(self._process_playlist_batch(existing_job_id, format_id))
            return existing_job_id

        playlist_job_id = str(uuid.uuid4())
        video_jobs = []

        for url in video_urls:
            job = download_manager.create_job(url, format_id=format_id)
            video_jobs.append({"job_id": job.id, "url": url, "status": "queued"})

        self.playlist_jobs[playlist_job_id] = {
            "id": playlist_job_id,
            "video_jobs": video_jobs,
            "total": len(video_jobs),
            "completed": 0,
            "failed": 0,
            "status": "in_progress",
        }

        # Start batch processing asynchronously
        asyncio.create_task(self._process_playlist_batch(playlist_job_id, format_id))
        return playlist_job_id

    async def _process_playlist_batch(self, playlist_job_id: str, format_id: str):
        pj = self.playlist_jobs.get(playlist_job_id)
        if not pj:
            return

        for item in pj["video_jobs"]:
            if pj.get("status") == "cancelled":
                logger.info(f"Playlist job {playlist_job_id} cancelled by user.")
                break

            # SKIP videos that have ALREADY been downloaded and marked completed
            if item.get("status") == "completed":
                continue

            job_id = item["job_id"]
            existing_job = download_manager.get_job(job_id)

            # If job was cancelled in previous attempt, recreate clean job
            if existing_job and existing_job.status == JobStatus.CANCELLED:
                new_job = download_manager.create_job(item["url"], format_id=format_id)
                item["job_id"] = new_job.id
                job_id = new_job.id

            await download_manager.start_job(job_id, format_id=format_id)

            if pj.get("status") == "cancelled":
                logger.info(f"Playlist job {playlist_job_id} cancelled during processing.")
                break

            job = download_manager.get_job(job_id)
            if job and job.status.value == "completed":
                pj["completed"] += 1
                item["status"] = "completed"
            elif job and job.status.value == "cancelled":
                item["status"] = "cancelled"
                pj["status"] = "cancelled"
                break
            else:
                pj["failed"] += 1
                item["status"] = "failed"

        if pj.get("status") != "cancelled":
            pj["status"] = "completed"

    def cancel_playlist_job(self, playlist_job_id: str) -> dict:
        pj = self.playlist_jobs.get(playlist_job_id)
        if not pj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "Playlist job not found."},
            )
        pj["status"] = "cancelled"

        # Actively cancel all child video jobs in DownloadManager
        for item in pj.get("video_jobs", []):
            job_id = item.get("job_id")
            if job_id:
                download_manager.cancel_job(job_id)

        return {"success": True, "message": "Playlist download stopped."}

    def get_playlist_job_status(self, playlist_job_id: str) -> dict:
        pj = self.playlist_jobs.get(playlist_job_id)
        if not pj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "Playlist job not found."},
            )

        total = pj["total"]
        completed = pj["completed"]
        progress = round((completed / total) * 100, 1) if total > 0 else 0.0

        return {
            "success": True,
            "playlist_job_id": playlist_job_id,
            "total_videos": total,
            "completed_videos": completed,
            "failed_videos": pj["failed"],
            "overall_progress": progress,
            "status": pj.get("status", "in_progress"),
            "video_jobs": pj["video_jobs"],
        }


playlist_service = PlaylistService()
