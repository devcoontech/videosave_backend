from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadRequest(BaseModel):
    url: str
    format_id: Optional[str] = "best"
    quality: Optional[str] = None


class DownloadJob(BaseModel):
    id: str
    url: str
    platform: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    speed: Optional[str] = None
    eta: Optional[str] = None
    title: Optional[str] = None
    filename: Optional[str] = None
    filepath: Optional[str] = None
    error: Optional[str] = None
    queue_position: Optional[int] = None
    created_at: float
    completed_at: Optional[float] = None


class DownloadJobResponse(BaseModel):
    success: bool = True
    job_id: str
    message: Optional[str] = None
