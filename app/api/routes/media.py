from fastapi import APIRouter, HTTPException, status
from backend.app.models.media import MediaInfoRequest, MediaInfoResponse
from backend.app.services.extractor import extractor_service

router = APIRouter(prefix="/media", tags=["Media"])


@router.post("/info", response_model=MediaInfoResponse, summary="Extract Media Information")
async def extract_media_info(request: MediaInfoRequest):
    """
    Extract media title, duration, thumbnail, uploader, and available video/audio formats.
    """
    return await extractor_service.get_media_info(request.url)
