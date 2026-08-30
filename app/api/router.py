from fastapi import APIRouter
from backend.app.api.routes import media, downloads, playlist, youtube, instagram, facebook, websocket

api_router = APIRouter(prefix="/api")

api_router.include_router(media.router)
api_router.include_router(downloads.router)
api_router.include_router(playlist.router)
api_router.include_router(youtube.router)
api_router.include_router(instagram.router)
api_router.include_router(facebook.router)
api_router.include_router(websocket.router)
