import os
from typing import Dict, List, Optional

from backend.app.core.config import BASE_DIR
from backend.app.services.ffmpeg_service import ffmpeg_service
from backend.app.utils.urls import detect_platform

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

YOUTUBE_PLAYER_CLIENTS = ["android", "ios", "tv", "mweb"]


def cookies_file() -> Optional[str]:
    candidates = [
        BASE_DIR / "cookies.txt",
        BASE_DIR.parent / "cookies.txt",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def platform_headers(url: str) -> dict:
    url_lower = url.lower()
    if "tiktok.com" in url_lower:
        referer = "https://www.tiktok.com/"
    elif "instagram.com" in url_lower:
        referer = "https://www.instagram.com/"
    elif "facebook.com" in url_lower or "fb.watch" in url_lower:
        referer = "https://www.facebook.com/"
    else:
        referer = "https://www.youtube.com/"
    return {
        "User-Agent": CHROME_UA,
        "Referer": referer,
        "Accept-Language": "en-US,en;q=0.9",
    }


def extractor_args_for(url: str) -> dict:
    args = {
        "youtube": {
            "player_client": list(YOUTUBE_PLAYER_CLIENTS),
            "player_skip": ["webpage", "configs"],
        },
        "tiktok": {
            "app_version": ["33.0.0"],
            "manifest_app_version": ["33000"],
        },
    }
    return args


def format_selector(url: str, format_id: str = "best") -> str:
    """Facebook/Instagram/TikTok usually have progressive files, not split A/V."""
    platform = detect_platform(url)
    is_mp3 = (format_id or "").lower() in ("mp3", "audio", "bestaudio")
    progressive = platform in ("facebook", "instagram", "tiktok")

    if is_mp3:
        return "bestaudio/best"

    height = None
    clean = (format_id or "best").rstrip("p")
    if clean.isdigit():
        height = int(clean)

    if progressive:
        if height:
            return (
                f"best[height<={height}][ext=mp4]/"
                f"best[height<={height}]/"
                f"best[ext=mp4]/best"
            )
        return "best[ext=mp4]/best"

    if not format_id or format_id == "best":
        return "bestvideo+bestaudio/best[ext=mp4]/best"
    if height:
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/"
            f"bestvideo+bestaudio/best"
        )
    return f"{format_id}+bestaudio/{format_id}/bestvideo+bestaudio/best"


def base_ydl_opts(url: str, extra: Optional[Dict] = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "socket_timeout": 60,
        "noplaylist": True,
        "cachedir": False,
        "http_headers": platform_headers(url),
        "extractor_args": extractor_args_for(url),
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "windowsfilenames": True,
    }
    cookies = cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
    ffmpeg_loc = ffmpeg_service.get_ffmpeg_location()
    if ffmpeg_loc:
        opts["ffmpeg_location"] = ffmpeg_loc
    if extra:
        opts.update(extra)
    return opts


def is_bot_challenge(message: str) -> bool:
    text = (message or "").lower()
    return any(
        token in text
        for token in (
            "sign in to confirm",
            "not a bot",
            "confirm you're not",
            "confirm you’re not",
            "please sign in",
            "use --cookies-from-browser",
        )
    )
