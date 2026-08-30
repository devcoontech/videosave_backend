from fastapi import APIRouter, HTTPException
from backend.app.models.playlist import (
    PlaylistInfoRequest,
    PlaylistInfoResponse,
    PlaylistDownloadRequest,
)
from backend.app.services.playlist_service import playlist_service
from backend.app.core.security import validate_and_normalize_url

router = APIRouter(prefix="/playlist", tags=["Playlists"])


@router.post("/info", response_model=PlaylistInfoResponse, summary="Extract Playlist Information")
async def get_playlist_info(request: PlaylistInfoRequest):
    """
    Extract flat metadata and video list from a YouTube playlist URL.
    """
    url = validate_and_normalize_url(request.url)
    return await playlist_service.get_playlist_info(url)


@router.post("/download", summary="Batch Download Selected Playlist Videos")
async def create_playlist_download(request: PlaylistDownloadRequest):
    """
    Enqueue a playlist batch download job for the selected video URLs.
    """
    if not request.video_urls:
        raise HTTPException(status_code=400, detail="No video URLs selected for download.")

    playlist_job_id = await playlist_service.create_playlist_job(
        request.video_urls,
        format_id=request.format_id or "best",
        existing_job_id=request.playlist_job_id,
    )
    return {"success": True, "playlist_job_id": playlist_job_id}


@router.get("/job/{playlist_job_id}", summary="Get Playlist Job Status")
async def get_playlist_job_status(playlist_job_id: str):
    """
    Retrieve real-time overall progress for a batch playlist download job.
    """
    return playlist_service.get_playlist_job_status(playlist_job_id)


@router.post("/job/{playlist_job_id}/cancel", summary="Cancel Playlist Download Job")
async def cancel_playlist_download(playlist_job_id: str):
    """
    Cancel an active batch playlist download.
    """
    return playlist_service.cancel_playlist_job(playlist_job_id)
