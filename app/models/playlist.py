from typing import List, Optional
from pydantic import BaseModel


class PlaylistItem(BaseModel):
    id: str
    url: str
    title: str
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    index: int


class PlaylistInfoRequest(BaseModel):
    url: str


class PlaylistInfoResponse(BaseModel):
    success: bool = True
    playlist_id: str
    title: str
    uploader: Optional[str] = None
    total_videos: int
    videos: List[PlaylistItem]


class PlaylistDownloadRequest(BaseModel):
    playlist_url: str
    video_urls: List[str]
    quality: Optional[str] = "best"
    format_id: Optional[str] = "best"
    playlist_job_id: Optional[str] = None


class PlaylistJobStatusResponse(BaseModel):
    success: bool = True
    playlist_job_id: str
    total_videos: int
    completed_videos: int
    failed_videos: int
    overall_progress: float
    current_job_id: Optional[str] = None
    video_jobs: List[dict]
