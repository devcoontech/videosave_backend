from backend.app.services.extractor import extractor_service
from backend.app.models.media import MediaInfoResponse


class YoutubeService:
    async def get_info(self, url: str) -> MediaInfoResponse:
        return await extractor_service.get_media_info(url)


youtube_service = YoutubeService()
