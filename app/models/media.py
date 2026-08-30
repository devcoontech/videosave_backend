from typing import List, Optional
from pydantic import BaseModel, HttpUrl, Field


class MediaInfoRequest(BaseModel):
    url: str = Field(..., description="Media URL from supported platform")


class MediaFormat(BaseModel):
    format_id: str
    quality: str
    height: Optional[int] = None
    width: Optional[int] = None
    fps: Optional[int] = None
    ext: str
    has_video: bool
    has_audio: bool
    filesize: Optional[int] = None
    filesize_approx: Optional[int] = None


class MediaInfoResponse(BaseModel):
    success: bool = True
    platform: str
    type: str  # video, reel, playlist
    id: str
    title: str
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    uploader: Optional[str] = None
    webpage_url: str
    formats: List[MediaFormat] = []


class ErrorResponse(BaseModel):
    success: bool = False
    error: dict
