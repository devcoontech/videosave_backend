from fastapi import APIRouter
from backend.app.models.media import MediaInfoRequest, MediaInfoResponse
from backend.app.services.facebook_service import facebook_service

router = APIRouter(prefix="/facebook", tags=["Facebook"])


@router.post("/info", response_model=MediaInfoResponse, summary="Facebook Reel Info")
async def get_facebook_info(request: MediaInfoRequest):
    return await facebook_service.get_info(request.url)
