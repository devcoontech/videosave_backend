from fastapi import APIRouter
from backend.app.models.media import MediaInfoRequest, MediaInfoResponse
from backend.app.services.youtube_service import youtube_service

router = APIRouter(prefix="/youtube", tags=["YouTube"])


@router.post("/info", response_model=MediaInfoResponse, summary="YouTube Media Info")
async def get_youtube_info(request: MediaInfoRequest):
    return await youtube_service.get_info(request.url)
