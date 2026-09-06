import asyncio
import uuid
import time
from typing import List, Dict, Any, Optional
from fastapi import HTTPException, status
from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.core.security import validate_and_normalize_url
from backend.app.models.playlist import PlaylistItem, PlaylistInfoResponse
from backend.app.models.jobs import JobStatus
from backend.app.services.download_service import download_manager
from backend.app.services.ytdlp_common import extract_info_with_fallback, normalize_youtube_watch_url


class PlaylistService:
    def __init__(self):
        self.playlist_jobs: Dict[str, dict] = {}

    def _sync_extract_playlist(self, url: str) -> Dict[str, Any]:
        try:
            info, _opts = extract_info_with_fallback(
                url,
                download=False,
                extra_opts={
                    "extract_flat": True,
                    "skip_download": True,
                    "ignoreerrors": True,
                },
            )
            if not info:
                raise RuntimeError("Unable to extract playlist information.")
            return info
        except Exception as e:
            logger.error(f"Playlist extraction error: {e}")
            raise RuntimeError(str(e)) from e

    async def get_playlist_info(self, url: str) -> PlaylistInfoResponse:
        try:
            info = await asyncio.to_thread(self._sync_extract_playlist, url)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PLAYLIST_EXTRACTION_FAILED", "message": f"Failed to extract playlist: {str(e)}"},
            )
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
            raw_url = entry.get("url") or entry.get("webpage_url") or ""
            v_url = normalize_youtube_watch_url(str(raw_url), str(v_id) if v_id else None)
            try:
                v_url = validate_and_normalize_url(v_url)
            except HTTPException:
                if not v_id:
                    continue
                v_url = validate_and_normalize_url(f"https://www.youtube.com/watch?v={v_id}")
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
        normalized_urls: List[str] = []
        for raw in video_urls:
            url = normalize_youtube_watch_url(raw)
            normalized_urls.append(validate_and_normalize_url(url))

        if settings.MAX_PLAYLIST_ITEMS > 0 and len(normalized_urls) > settings.MAX_PLAYLIST_ITEMS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "PLAYLIST_LIMIT_EXCEEDED",
                    "message": f"Playlist size exceeds maximum limit of {settings.MAX_PLAYLIST_ITEMS} videos.",
                },
            )

        if existing_job_id and existing_job_id in self.playlist_jobs:
            pj = self.playlist_jobs[existing_job_id]
            pj["status"] = "in_progress"
            asyncio.create_task(self._process_playlist_batch(existing_job_id, format_id))
            return existing_job_id

        playlist_job_id = str(uuid.uuid4())
        video_jobs = []

        for url in normalized_urls:
            job = download_manager.create_job(url, format_id=format_id)
            video_jobs.append({"job_id": job.id, "url": url, "status": "queued", "error": None})

        self.playlist_jobs[playlist_job_id] = {
            "id": playlist_job_id,
            "video_jobs": video_jobs,
            "total": len(video_jobs),
            "completed": 0,
            "failed": 0,
            "status": "in_progress",
            "current_job_id": video_jobs[0]["job_id"] if video_jobs else None,
        }

        asyncio.create_task(self._process_playlist_batch(playlist_job_id, format_id))
        return playlist_job_id

    async def _process_playlist_batch(self, playlist_job_id: str, format_id: str):
        pj = self.playlist_jobs.get(playlist_job_id)
        if not pj:
            return

        pj["completed"] = 0
        pj["failed"] = 0

        for item in pj["video_jobs"]:
            if pj.get("status") == "cancelled":
                logger.info(f"Playlist job {playlist_job_id} cancelled by user.")
                break

            if item.get("status") == "completed":
                pj["completed"] += 1
                continue

            job_id = item["job_id"]
            existing_job = download_manager.get_job(job_id)

            if existing_job and existing_job.status == JobStatus.CANCELLED:
                new_job = download_manager.create_job(item["url"], format_id=format_id)
                item["job_id"] = new_job.id
                job_id = new_job.id

            pj["current_job_id"] = job_id
            item["status"] = "downloading"
            await download_manager.start_job(job_id, format_id=format_id)

            if pj.get("status") == "cancelled":
                logger.info(f"Playlist job {playlist_job_id} cancelled during processing.")
                break

            job = download_manager.get_job(job_id)
            if job and job.status.value == "completed":
                pj["completed"] += 1
                item["status"] = "completed"
                item["error"] = None
            elif job and job.status.value == "cancelled":
                item["status"] = "cancelled"
                item["error"] = job.error
                pj["status"] = "cancelled"
                break
            else:
                pj["failed"] += 1
                item["status"] = "failed"
                item["error"] = job.error if job else "Download failed."

        pj["current_job_id"] = None
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
        completed = 0
        failed = 0
        for item in pj["video_jobs"]:
            job = download_manager.get_job(item.get("job_id", ""))
            if item.get("status") == "completed" or (job and job.status.value == "completed"):
                completed += 1
                item["status"] = "completed"
            elif item.get("status") == "failed" or (job and job.status.value == "failed"):
                failed += 1
                item["status"] = "failed"
                if job and job.error:
                    item["error"] = job.error

        pj["completed"] = completed
        pj["failed"] = failed

        in_progress = max(0, total - completed - failed)
        progress = round(((completed + failed * 0.5) / total) * 100, 1) if total > 0 else 0.0
        if completed == total:
            progress = 100.0

        current_job_id = pj.get("current_job_id")
        current_progress = None
        if current_job_id:
            job = download_manager.get_job(current_job_id)
            if job:
                current_progress = job.progress

        return {
            "success": True,
            "playlist_job_id": playlist_job_id,
            "total_videos": total,
            "completed_videos": completed,
            "failed_videos": failed,
            "in_progress_videos": in_progress,
            "overall_progress": progress,
            "current_job_id": current_job_id,
            "current_video_progress": current_progress,
            "status": pj.get("status", "in_progress"),
            "video_jobs": pj["video_jobs"],
        }


playlist_service = PlaylistService()
