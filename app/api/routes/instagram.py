from fastapi import APIRouter
from backend.app.models.media import MediaInfoRequest, MediaInfoResponse
from backend.app.services.instagram_service import instagram_service

router = APIRouter(prefix="/instagram", tags=["Instagram"])


@router.post("/info", response_model=MediaInfoResponse, summary="Instagram Reel Info")
async def get_instagram_info(request: MediaInfoRequest):
    return await instagram_service.get_info(request.url)
