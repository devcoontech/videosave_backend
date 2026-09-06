import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

from backend.app.core.config import BASE_DIR, settings
from backend.app.services.ffmpeg_service import ffmpeg_service
from backend.app.utils.urls import detect_platform

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

def has_logged_in_cookies() -> bool:
    diag = cookies_diagnostics()
    return bool(diag["configured"] and (diag["has_login_info"] or diag["has_sid"]))


def build_youtube_try_plans() -> List[Tuple[List[str], bool]]:
    """Anonymous clients first on VPS — home cookies + datacenter IP triggers bot blocks."""
    cookie_plans: List[Tuple[List[str], bool]] = [
        (["web"], True),
        (["web", "web_safari"], True),
        (["tv"], True),
        (["web_creator"], True),
    ]
    anonymous_plans: List[Tuple[List[str], bool]] = [
        (["android_vr"], False),
        (["tv_embedded"], False),
        (["tv", "web_safari"], False),
        (["web_safari"], False),
        (["android"], False),
        (["mweb"], False),
        (["ios"], False),
        (["android", "ios"], False),
    ]
    has_cookies = bool(cookies_file())
    has_login = has_logged_in_cookies()

    if settings.YOUTUBE_COOKIES_FIRST and has_login:
        return cookie_plans + anonymous_plans
    if settings.YOUTUBE_COOKIES_FIRST and has_cookies:
        return cookie_plans + anonymous_plans

    # Default: no-cookie clients first (works on most VPS IPs with bgutil or android_vr)
    plans = list(anonymous_plans)
    if has_login or has_cookies:
        plans.extend(cookie_plans)
    return plans


PROGRESSIVE_PLATFORMS = frozenset({"facebook", "instagram", "tiktok"})

FACEBOOK_IMPERSONATE = (
    "chrome-131:android-14",
    "chrome-99:android-12",
    "chrome-133:macos-15",
)

_bgutil_cache: Dict[str, Any] = {"checked_at": 0.0, "reachable": False}


def bgutil_is_reachable() -> bool:
    url = (settings.BGUTIL_POT_BASE_URL or "").strip()
    if not url:
        return False
    now = time.time()
    if now - _bgutil_cache["checked_at"] < 30:
        return bool(_bgutil_cache["reachable"])
    reachable = False
    try:
        import urllib.request

        with urllib.request.urlopen(url.rstrip("/") + "/ping", timeout=2) as resp:
            reachable = resp.status == 200
    except OSError:
        reachable = False
    _bgutil_cache["checked_at"] = now
    _bgutil_cache["reachable"] = reachable
    return reachable


def normalize_youtube_watch_url(value: str, video_id: Optional[str] = None) -> str:
    raw = (value or "").strip()
    vid = (video_id or "").strip()
    if not raw and vid:
        return f"https://www.youtube.com/watch?v={vid}"
    if not raw:
        return raw
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return f"https://www.youtube.com{raw}"
    if len(raw) == 11 and raw.replace("-", "").replace("_", "").isalnum():
        return f"https://www.youtube.com/watch?v={raw}"
    if "watch" in raw or "youtu.be" in raw:
        return f"https://www.youtube.com/{raw.lstrip('/')}"
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return raw


def has_impersonate() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def resolve_impersonate(value: Optional[Union[str, ImpersonateTarget]]) -> Optional[ImpersonateTarget]:
    if not has_impersonate() or value is None:
        return None
    if isinstance(value, ImpersonateTarget):
        return value
    if value == "auto":
        for candidate in FACEBOOK_IMPERSONATE:
            try:
                return ImpersonateTarget.from_str(candidate)
            except ValueError:
                continue
        return ImpersonateTarget()
    if value == "":
        return ImpersonateTarget()
    try:
        return ImpersonateTarget.from_str(value)
    except ValueError:
        return ImpersonateTarget()


def cookies_file() -> Optional[str]:
    if settings.COOKIES_FILE:
        path = os.path.abspath(settings.COOKIES_FILE)
        if os.path.isfile(path):
            return path
    candidates = [
        BASE_DIR / "cookies.txt",
        BASE_DIR.parent / "cookies.txt",
        Path("/app/cookies.txt"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def cookies_diagnostics() -> Dict[str, Any]:
    path = cookies_file()
    if not path:
        return {
            "configured": False,
            "path": None,
            "youtube_entries": 0,
            "has_login_info": False,
            "has_sid": False,
            "instagram_entries": 0,
            "facebook_entries": 0,
            "tiktok_entries": 0,
        }
    youtube_entries = 0
    instagram_entries = 0
    facebook_entries = 0
    tiktok_entries = 0
    has_login_info = False
    has_sid = False
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 6:
                    continue
                domain, _flag, _path, _secure, _expires, name = parts[:6]
                if "youtube.com" in domain:
                    youtube_entries += 1
                    if name == "LOGIN_INFO":
                        has_login_info = True
                    if name == "SID":
                        has_sid = True
                elif "instagram.com" in domain:
                    instagram_entries += 1
                elif "facebook.com" in domain:
                    facebook_entries += 1
                elif "tiktok.com" in domain:
                    tiktok_entries += 1
    except OSError:
        pass
    return {
        "configured": True,
        "path": path,
        "youtube_entries": youtube_entries,
        "has_login_info": has_login_info,
        "has_sid": has_sid,
        "instagram_entries": instagram_entries,
        "facebook_entries": facebook_entries,
        "tiktok_entries": tiktok_entries,
    }


def platform_headers(url: str) -> dict:
    url_lower = url.lower()
    if "tiktok.com" in url_lower:
        referer = "https://www.tiktok.com/"
    elif "instagram.com" in url_lower:
        referer = "https://www.instagram.com/"
    elif "facebook.com" in url_lower or "fb.watch" in url_lower or "fb.me" in url_lower:
        referer = "https://www.facebook.com/"
    else:
        referer = "https://www.youtube.com/"
    return {
        "User-Agent": CHROME_UA,
        "Referer": referer,
        "Accept-Language": "en-US,en;q=0.9",
    }


def extractor_args_for(url: str, player_clients: Optional[List[str]] = None) -> dict:
    youtube_args: Dict[str, Any] = {
        "player_client": list(player_clients or ["web", "web_safari", "android_vr", "tv"]),
    }
    if settings.YOUTUBE_PO_TOKEN:
        youtube_args["po_token"] = [settings.YOUTUBE_PO_TOKEN]

    args: Dict[str, Any] = {
        "youtube": youtube_args,
        "instagram": {
            "api": ["web"],
        },
        "tiktok": {
            "app_version": ["33.0.0"],
            "manifest_app_version": ["33000"],
            "api_hostname": ["api22-normal-c-useast1a.tiktokv.com"],
        },
    }
    if settings.BGUTIL_POT_BASE_URL and bgutil_is_reachable():
        args["youtubepot-bgutilhttp"] = {"base_url": [settings.BGUTIL_POT_BASE_URL]}
    return args


def format_selector(url: str, format_id: str = "best") -> str:
    """Facebook/Instagram/TikTok use progressive files (often hd/sd, not split A/V)."""
    platform = detect_platform(url)
    is_mp3 = (format_id or "").lower() in ("mp3", "audio", "bestaudio")
    progressive = platform in PROGRESSIVE_PLATFORMS
    fid = (format_id or "best").lower()

    if is_mp3:
        return "bestaudio/best"

    if progressive:
        if fid in ("hd", "sd"):
            return f"{fid}/best"
        height = None
        clean = fid.rstrip("p")
        if clean.isdigit():
            height = int(clean)
        if height:
            return "hd/best" if height >= 720 else "sd/best"
        return "best"

    height = None
    clean = (format_id or "best").rstrip("p")
    if clean.isdigit():
        height = int(clean)

    if not format_id or format_id == "best":
        return "bestvideo+bestaudio/best[ext=mp4]/best"
    if height:
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/"
            f"bestvideo+bestaudio/best"
        )
    return f"{format_id}+bestaudio/{format_id}/bestvideo+bestaudio/best"


def base_ydl_opts(
    url: str,
    extra: Optional[Dict] = None,
    *,
    player_clients: Optional[List[str]] = None,
    use_cookies: bool = True,
    impersonate: Optional[Union[str, ImpersonateTarget]] = None,
    skip_impersonate: bool = False,
) -> dict:
    opts: Dict[str, Any] = {
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
        "extractor_args": extractor_args_for(url, player_clients),
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "windowsfilenames": True,
        "restrictfilenames": True,
    }
    if use_cookies:
        cookies = cookies_file()
        if cookies:
            opts["cookiefile"] = cookies
    ffmpeg_loc = ffmpeg_service.get_ffmpeg_location()
    if ffmpeg_loc:
        opts["ffmpeg_location"] = ffmpeg_loc
    resolved = resolve_impersonate(impersonate)
    if not skip_impersonate:
        if resolved is not None:
            opts["impersonate"] = resolved
        elif detect_platform(url) == "facebook" and has_impersonate():
            opts["impersonate"] = resolve_impersonate("auto")
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
            "bot verification",
        )
    )


def is_retryable_youtube_error(message: str) -> bool:
    text = (message or "").lower()
    if is_bot_challenge(text):
        return True
    return any(
        token in text
        for token in (
            "the page needs to be reloaded",
            "http error 403",
            "requested format is not available",
            "unable to extract uploader id",
            "unable to extract",
            "playability status",
            "confirm your age",
            "age-restricted",
            "age restricted",
            "inappropriate for some users",
            "login required",
            "members only",
            "private video",
        )
    )


def is_age_or_login_error(message: str) -> bool:
    text = (message or "").lower()
    return any(
        token in text
        for token in (
            "confirm your age",
            "age-restricted",
            "age restricted",
            "inappropriate for some users",
            "login required",
            "members only",
            "private video",
            "sign in",
        )
    )


def youtube_bot_user_message() -> str:
    diag = cookies_diagnostics()
    bgutil_url = (settings.BGUTIL_POT_BASE_URL or "").strip()
    if bgutil_url and not bgutil_is_reachable():
        return (
            "YouTube is blocked because the PO token server (bgutil) is not running. "
            "In Coolify: Rebuild the backend and check logs for 'bgutil PO token server is ready', "
            "OR add a second service with image brainicism/bgutil-ytdlp-pot-provider:1.3.2-node "
            "and set BGUTIL_POT_BASE_URL=http://<bgutil-service>:4416. "
            "Check /api/health → bgutil_reachable must be true."
        )
    if not diag["configured"]:
        return (
            "YouTube blocked this datacenter IP. Do NOT use home PC cookies on VPS — they make it worse. "
            "You need bgutil PO tokens: rebuild backend or add bgutil sidecar (see cookies.txt.example). "
            "Verify /api/health shows bgutil_reachable: true."
        )
    if diag["youtube_entries"] == 0:
        return (
            "cookies.txt was found but contains no .youtube.com entries. "
            "Re-export from Firefox while on youtube.com and re-upload to /app/cookies.txt."
        )
    if not diag["has_login_info"] and not diag["has_sid"]:
        return (
            "cookies.txt is missing a logged-in YouTube session (no SID/LOGIN_INFO). "
            "Log into YouTube in Firefox, export cookies for the current site, and re-upload."
        )
    return (
        "YouTube blocked all download methods from this server. "
        "Rebuild the backend so bgutil PO tokens start (check /api/health → bgutil_reachable: true). "
        "Home PC cookies usually do not work on VPS IPs — remove cookies.txt if problems persist."
    )


def is_facebook_parse_error(message: str) -> bool:
    text = (message or "").lower()
    return "cannot parse data" in text or "[facebook]" in text and "parse" in text


def _run_ydl(url: str, opts: dict, download: bool) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=download)
        if not info:
            raise yt_dlp.utils.DownloadError("No information returned by extractor.")
        return info


def extract_info_with_fallback(
    url: str,
    download: bool = False,
    extra_opts: Optional[Dict] = None,
) -> Tuple[dict, dict]:
    """
    Run yt-dlp with platform-specific retry chains.
    Returns (info_dict, ydl_opts_used).
    """
    platform = detect_platform(url)
    extra = dict(extra_opts or {})
    last_error: Optional[Exception] = None

    if platform == "youtube":
        plans = build_youtube_try_plans()
        for clients, use_account_cookies in plans:
            try:
                opts = base_ydl_opts(
                    url,
                    extra,
                    player_clients=clients,
                    use_cookies=use_account_cookies,
                )
                return _run_ydl(url, opts, download), opts
            except yt_dlp.utils.DownloadError as err:
                last_error = err
                if is_retryable_youtube_error(str(err)):
                    continue
                raise
        if last_error:
            raise last_error

    if platform in PROGRESSIVE_PLATFORMS:
        attempts: List[Dict[str, Any]] = []
        # Cookies first for private/age-gated content on social platforms
        if has_impersonate() and platform == "facebook":
            for target in [*FACEBOOK_IMPERSONATE, ""]:
                attempts.append({"impersonate": target, "use_cookies": True, "skip_impersonate": False})
            for target in [*FACEBOOK_IMPERSONATE, ""]:
                attempts.append({"impersonate": target, "use_cookies": False, "skip_impersonate": False})
            attempts.extend([
                {"impersonate": None, "use_cookies": True, "skip_impersonate": True},
                {"impersonate": None, "use_cookies": False, "skip_impersonate": True},
            ])
        else:
            attempts.extend([
                {"impersonate": None, "use_cookies": True, "skip_impersonate": True},
                {"impersonate": None, "use_cookies": False, "skip_impersonate": True},
            ])
        for attempt in attempts:
            try:
                opts = base_ydl_opts(
                    url,
                    extra,
                    use_cookies=attempt["use_cookies"],
                    impersonate=attempt.get("impersonate"),
                    skip_impersonate=attempt["skip_impersonate"],
                )
                return _run_ydl(url, opts, download), opts
            except Exception as err:
                last_error = err
                if is_age_or_login_error(str(err)) or is_facebook_parse_error(str(err)):
                    continue
                continue
        if last_error:
            raise last_error

    opts = base_ydl_opts(url, extra)
    return _run_ydl(url, opts, download), opts
