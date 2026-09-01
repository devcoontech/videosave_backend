import os
import asyncio
from fastapi import APIRouter, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse
from backend.app.models.jobs import DownloadRequest, DownloadJobResponse, DownloadJob
from backend.app.services.download_service import download_manager
from backend.app.core.security import validate_and_normalize_url

from backend.app.core.config import DOWNLOADS_PATH

router = APIRouter(prefix="/download", tags=["Downloads"])


@router.post("", response_model=DownloadJobResponse, summary="Create Download Job")
async def create_download_job(request: DownloadRequest, background_tasks: BackgroundTasks):
    """
    Enqueue a background download job for the specified media URL and format.
    """
    valid_url = validate_and_normalize_url(request.url)
    job = download_manager.create_job(valid_url, format_id=request.format_id or "best")

    # Start download in background worker task
    background_tasks.add_task(
        download_manager.start_job, job.id, format_id=request.format_id or "best"
    )

    return DownloadJobResponse(
        success=True,
        job_id=job.id,
        message="Download job created and queued successfully.",
    )


@router.get("/{job_id}", response_model=DownloadJob, summary="Get Job Status")
async def get_download_job_status(job_id: str):
    """
    Retrieve real-time status and metadata for a download job.
    """
    job = download_manager.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FILE_NOT_FOUND", "message": f"Job with ID '{job_id}' was not found."},
        )
    return job


@router.get("/{job_id}/file", summary="Download Processed Media File")
async def download_job_file(job_id: str):
    """
    Stream or download the completed file to the user's browser.
    """
    job = download_manager.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FILE_NOT_FOUND", "message": f"Job with ID '{job_id}' was not found."},
        )

    if job.status.value != "completed" or not job.filepath or not os.path.exists(job.filepath):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "FILE_NOT_READY", "message": "File is not ready or has not finished downloading."},
        )

    # Path traversal protection check
    real_filepath = os.path.realpath(job.filepath)
    real_downloads_dir = os.path.realpath(str(DOWNLOADS_PATH))
    if not real_filepath.startswith(real_downloads_dir):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ACCESS_DENIED", "message": "Access to requested file path is forbidden."},
        )

    filename = job.filename or os.path.basename(job.filepath)
    return FileResponse(
        path=job.filepath,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.delete("/{job_id}", summary="Delete Download Job")
async def delete_download_job(job_id: str):
    """
    Cancel or remove job metadata and delete its local temporary file.
    """
    job = download_manager.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FILE_NOT_FOUND", "message": f"Job with ID '{job_id}' was not found."},
        )

    if job.filepath and os.path.exists(job.filepath):
        try:
            os.remove(job.filepath)
        except Exception:
            pass

    del download_manager.jobs[job_id]
    return {"success": True, "message": "Job deleted."}
