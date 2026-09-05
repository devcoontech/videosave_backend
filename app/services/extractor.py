import os
import asyncio
from typing import Dict, Any, List
import yt_dlp
from fastapi import HTTPException, status
from backend.app.core.logging import logger
from backend.app.core.security import validate_and_normalize_url
from backend.app.utils.urls import detect_platform
from backend.app.models.media import MediaFormat, MediaInfoResponse
from backend.app.services.ffmpeg_service import ffmpeg_service

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")


def get_platform_headers(url: str) -> dict:
    url_lower = url.lower()
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    if "tiktok.com" in url_lower:
        return {
            "User-Agent": ua,
            "Referer": "https://www.tiktok.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    elif "instagram.com" in url_lower:
        return {
            "User-Agent": ua,
            "Referer": "https://www.instagram.com/",
        }
    elif "facebook.com" in url_lower or "fb.watch" in url_lower:
        return {
            "User-Agent": ua,
            "Referer": "https://www.facebook.com/",
        }
    else:
        return {
            "User-Agent": ua,
            "Referer": "https://www.youtube.com/",
        }


class ExtractorFailure(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class MediaExtractor:
    def __init__(self):
        self.base_options = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "socket_timeout": 60,
            "continuedl": True,
            "noplaylist": True,
            "extractor_args": {
                "tiktok": {
                    "app_version": "33.0.0",
                    "manifest_app_version": "33000",
                },
            },

        }

        ffmpeg_loc = ffmpeg_service.get_ffmpeg_location()
        if ffmpeg_loc:
            self.base_options["ffmpeg_location"] = ffmpeg_loc

    def _format_height_quality(self, height: int) -> str:
        if height >= 2160:
            return "2160p (4K)"
        elif height >= 1440:
            return "1440p (2K)"
        elif height >= 1080:
            return "1080p"
        elif height >= 720:
            return "720p"
        elif height >= 480:
            return "480p"
        elif height >= 360:
            return "360p"
        elif height >= 240:
            return "240p"
        elif height >= 144:
            return "144p"
        return f"{height}p"

    def _sync_extract_info(self, url: str) -> Dict[str, Any]:
        options = {
            **self.base_options,
            "extract_flat": False,
            "skip_download": True,
            "http_headers": get_platform_headers(url),
        }

        # Attach optional cookies.txt if provided
        if os.path.exists(COOKIES_FILE):
            options["cookiefile"] = COOKIES_FILE

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise Exception("No information returned by extractor.")
                return info
        except yt_dlp.utils.DownloadError as de:

            err_msg = str(de).lower()
            if "private video" in err_msg or "login" in err_msg:
                raise ExtractorFailure(
                    status.HTTP_403_FORBIDDEN,
                    "PRIVATE_VIDEO",
                    "This video is private or requires login to view.",
                )
            elif "not available" in err_msg or "removed" in err_msg or "deleted" in err_msg:
                raise ExtractorFailure(
                    status.HTTP_404_NOT_FOUND,
                    "VIDEO_UNAVAILABLE",
                    "This video is unavailable or has been removed.",
                )
            elif "geo" in err_msg or "region" in err_msg:
                raise ExtractorFailure(
                    status.HTTP_403_FORBIDDEN,
                    "GEO_RESTRICTED",
                    "This content is geo-restricted in your area.",
                )
            elif "bot" in err_msg or "sign in to confirm" in err_msg:
                raise ExtractorFailure(
                    status.HTTP_403_FORBIDDEN,
                    "BOT_VERIFICATION_REQUIRED",
                    "YouTube bot verification triggered. Please try again or check the URL.",
                )
            elif "age" in err_msg:
                raise ExtractorFailure(
                    status.HTTP_403_FORBIDDEN,
                    "LOGIN_REQUIRED",
                    "Age-restricted video requiring authentication.",
                )
            else:
                logger.error(f"yt-dlp extraction error for {url}: {de}")
                raise ExtractorFailure(
                    status.HTTP_400_BAD_REQUEST,
                    "EXTRACTION_FAILED",
                    "Could not extract media info from the provided URL.",
                )
        except ExtractorFailure:
            raise
        except Exception as e:
            logger.error(f"Unexpected error extracting media info for {url}: {e}")
            raise ExtractorFailure(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "UNKNOWN_ERROR",
                f"Extraction failed: {str(e)}",
            )

    async def get_media_info(self, url: str) -> MediaInfoResponse:
        normalized_url = validate_and_normalize_url(url)
        platform = detect_platform(normalized_url)

        try:
            info = await asyncio.to_thread(self._sync_extract_info, normalized_url)
        except ExtractorFailure as e:
            raise HTTPException(
                status_code=e.status_code,
                detail={"code": e.code, "message": e.message},
            )

        # Check if URL returned a playlist object instead of a single video
        if info.get("_type") == "playlist" or (info.get("entries") is not None and not info.get("formats")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "PLAYLIST_URL_DETECTED",
                    "message": "This link is a YouTube playlist. Please switch to the Playlist Downloader.",
                },
            )

        title = info.get("title", "Untitled Media")
        thumbnail = info.get("thumbnail") or (info.get("thumbnails", [{}])[-1].get("url") if info.get("thumbnails") else None)
        raw_dur = info.get("duration")
        duration = round(float(raw_dur)) if raw_dur is not None else None
        uploader = info.get("uploader") or info.get("creator") or info.get("channel") or "Unknown Creator"
        webpage_url = info.get("webpage_url", normalized_url)
        media_id = info.get("id", "media_123")

        formats_raw = info.get("formats", [])
        video_formats_by_height: Dict[int, MediaFormat] = {}

        for fmt in formats_raw:
            vcodec = fmt.get("vcodec", "none")
            height = fmt.get("height")

            # REQUIRE video codec (ignore audio-only language tracks)
            if vcodec == "none" or not height or height <= 0:
                continue

            quality_label = self._format_height_quality(height)
            width = fmt.get("width")
            raw_fps = fmt.get("fps")
            fps = round(float(raw_fps)) if raw_fps is not None else None
            ext = fmt.get("ext", "mp4")
            raw_fs = fmt.get("filesize") or fmt.get("filesize_approx")
            filesize = int(float(raw_fs)) if raw_fs is not None else None

            candidate = MediaFormat(
                format_id=f"{height}p",
                quality=quality_label,
                height=height,
                width=width,
                fps=fps,
                ext="mp4",  # Backend merges into mp4 output
                has_video=True,
                has_audio=True,
                filesize=filesize,
            )


            # Prefer MP4 container or formats with larger/known filesize
            if height not in video_formats_by_height:
                video_formats_by_height[height] = candidate
            else:
                existing = video_formats_by_height[height]
                if ext == "mp4" and existing.ext != "mp4":
                    video_formats_by_height[height] = candidate
                elif filesize and (not existing.filesize or filesize > existing.filesize):
                    video_formats_by_height[height] = candidate

        # Sort heights in descending order (e.g. 2160, 1440, 1080, 720, 480, 360, 240)
        sorted_heights = sorted(video_formats_by_height.keys(), reverse=True)
        distinct_formats: List[MediaFormat] = [video_formats_by_height[h] for h in sorted_heights]

        # Always include Best Available Quality at top
        result_formats = [
            MediaFormat(
                format_id="best",
                quality="Best Available Quality",
                ext="mp4",
                has_video=True,
                has_audio=True,
            )
        ] + distinct_formats

        return MediaInfoResponse(
            success=True,
            platform=platform,
            type="video" if platform != "youtube_playlist" else "playlist",
            id=media_id,
            title=title,
            thumbnail=thumbnail,
            duration=duration,
            uploader=uploader,
            webpage_url=webpage_url,
            formats=result_formats,
        )


extractor_service = MediaExtractor()
