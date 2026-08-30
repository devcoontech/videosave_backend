from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from backend.app.services.download_service import download_manager
from backend.app.core.logging import logger

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/download/{job_id}")
async def websocket_download_progress(websocket: WebSocket, job_id: str):
    """
    WebSocket endpoint broadcasting real-time download progress events for a job.
    """
    await websocket.accept()
    await download_manager.register_websocket(job_id, websocket)
    try:
        while True:
            # Keep connection alive until client disconnects
            await websocket.receive_text()
    except WebSocketDisconnect:
        download_manager.unregister_websocket(job_id, websocket)
    except Exception as e:
        logger.warning(f"WebSocket closed with error for job {job_id}: {e}")
        download_manager.unregister_websocket(job_id, websocket)
