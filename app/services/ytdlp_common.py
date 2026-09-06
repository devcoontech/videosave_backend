import os
import re
import time
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

from backend.app.core.config import BASE_DIR, settings
from backend.app.core.logging import logger
from backend.app.services.ffmpeg_service import ffmpeg_service
from backend.app.utils.urls import detect_platform

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

def has_logged_in_cookies() -> bool:
    diag = cookies_diagnostics()
    return bool(diag["configured"] and (diag["has_login_info"] or diag["has_sid"]))


def should_use_youtube_cookies() -> bool:
    """Only use cookies for YouTube when a real logged-in session is present."""
    return has_logged_in_cookies()


def build_youtube_try_plans() -> List[Tuple[List[str], bool]]:
    """Anonymous clients first on VPS — guest/home cookies on datacenter IP trigger bot blocks."""
    cookie_plans: List[Tuple[List[str], bool]] = [
        (["web"], True),
        (["web", "web_safari"], True),
        (["tv"], True),
        (["web_creator"], True),
    ]
    anonymous_plans: List[Tuple[List[str], bool]] = [
        (["web_embedded"], False),
        (["android"], False),
        (["mweb"], False),
        (["ios"], False),
        (["tv_embedded"], False),
        (["web_safari"], False),
        (["tv", "web_safari"], False),
        (["android_vr"], False),
    ]
    has_login = has_logged_in_cookies()

    if settings.YOUTUBE_COOKIES_FIRST and has_login:
        return cookie_plans + anonymous_plans

    # Default: PO-token anonymous clients only; never fall back to guest cookies on VPS.
    plans = list(anonymous_plans)
    if has_login:
        plans.extend(cookie_plans)
    return plans


PROGRESSIVE_PLATFORMS = frozenset({"facebook", "instagram", "tiktok"})

FACEBOOK_IMPERSONATE = (
    "chrome-131:android-14",
    "chrome-99:android-12",
    "chrome-133:macos-15",
)

_bgutil_cache: Dict[str, Any] = {"checked_at": 0.0, "reachable": False}
_youtube_script_semaphore = threading.Semaphore(2)

DEFAULT_BGUTIL_SCRIPT_HOME = "/opt/bgutil-app"


def bgutil_script_home() -> Optional[str]:
    explicit = (settings.BGUTIL_SCRIPT_HOME or "").strip()
    if explicit:
        return explicit if Path(explicit).is_dir() else None
    if Path(DEFAULT_BGUTIL_SCRIPT_HOME).is_dir():
        return DEFAULT_BGUTIL_SCRIPT_HOME
    return None


def bgutil_script_available() -> bool:
    home = bgutil_script_home()
    if not home:
        return False
    return (Path(home) / "build" / "generate_once.js").is_file()


def pot_provider_ready() -> bool:
    return bgutil_is_reachable() or bgutil_script_available()


def node_binary() -> Optional[str]:
    for candidate in ("/usr/local/bin/node", "/usr/bin/node"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def bgutil_script_runnable() -> bool:
    home = bgutil_script_home()
    script = Path(home) / "build" / "generate_once.js" if home else None
    node = node_binary()
    if not script or not script.is_file() or not node:
        return False
    try:
        import subprocess

        result = subprocess.run(
            [node, str(script), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=home,
            env={
                **os.environ,
                "HOME": os.environ.get("HOME", "/tmp"),
                "XDG_CACHE_HOME": os.environ.get("XDG_CACHE_HOME", "/tmp/bgutil-cache"),
                "NODE_PATH": str(Path(home) / "node_modules"),
            },
        )
        return result.returncode == 0 and bool((result.stdout or "").strip())
    except OSError:
        return False


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
    platform = detect_platform(url)
    url_lower = url.lower()
    if platform == "youtube":
        # Custom User-Agent breaks bgutil PO tokens — yt-dlp sets per-client UA internally.
        return {"Accept-Language": "en-US,en;q=0.9"}
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
        "player_client": list(player_clients or ["web", "web_safari", "android", "tv"]),
    }
    if settings.YOUTUBE_PO_TOKEN:
        youtube_args["po_token"] = [settings.YOUTUBE_PO_TOKEN]
    if pot_provider_ready():
        # Skip the watch-page fetch that triggers datacenter bot checks; PO tokens handle player API.
        youtube_args["player_skip"] = ["webpage"]

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
    if bgutil_script_available():
        home = bgutil_script_home()
        if home:
            args["youtubepot-bgutilscript"] = {"server_home": [home]}
    bgutil_url = (settings.BGUTIL_POT_BASE_URL or "").strip()
    if bgutil_url:
        args["youtubepot-bgutilhttp"] = {"base_url": [bgutil_url.rstrip("/")]}
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
        return "best[ext=mp4]/bestvideo+bestaudio/best[ext=mp4]/best"
    if height:
        return (
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"bestvideo[height<={height}]+bestaudio/"
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
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
        platform = detect_platform(url)
        if cookies:
            if platform == "youtube" and not should_use_youtube_cookies():
                pass
            else:
                opts["cookiefile"] = cookies
    if detect_platform(url) == "youtube":
        opts["remote_components"] = {"ejs:github"}
        node_bin = node_binary()
        if node_bin and bgutil_script_available():
            opts["js_runtimes"] = {"node": {"path": node_bin}}
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
    pot_ready = pot_provider_ready()
    script_runnable = bgutil_script_runnable()

    if not pot_ready:
        return (
            "YouTube is blocked because PO tokens are not configured. "
            "Rebuild the backend Docker image (see cookies.txt.example) or add a bgutil sidecar service."
        )
    if pot_ready and not bgutil_is_reachable() and bgutil_script_available() and not script_runnable:
        return (
            "YouTube PO token script is installed but Node.js cannot run it. "
            "Rebuild the backend and check deploy logs for bgutil errors."
        )
    if not diag["configured"]:
        if pot_ready and bgutil_is_reachable():
            return (
                "YouTube blocked this datacenter IP even though PO tokens are active. "
                "Open /api/health/youtube-test for the exact yt-dlp error. "
                "Try again in a few minutes — the VPS IP may be temporarily rate-limited."
            )
        if pot_ready:
            return (
                "YouTube blocked this server IP. Open /api/health/youtube-test for the exact error. "
                "Ensure deploy logs show: [start] bgutil PO token server is ready."
            )
        return (
            "YouTube blocked this datacenter IP. "
            "Rebuild the backend so PO tokens are available (see cookies.txt.example)."
        )
    if not diag["has_login_info"] and not diag["has_sid"]:
        if diag["youtube_entries"] > 0:
            return (
                "cookies.txt is mounted but has only guest YouTube cookies (no SID/LOGIN_INFO). "
                "Remove the cookies.txt file mount in Coolify for YouTube on VPS — bgutil PO tokens are used instead. "
                "Guest cookies from your PC on a datacenter IP make downloads fail."
            )
        return (
            "cookies.txt was found but contains no .youtube.com entries. "
            "For YouTube on VPS, remove cookies.txt entirely and use bgutil PO tokens."
        )
    return (
        "YouTube blocked all download methods from this server. "
        "If /api/health shows bgutil_script_available: true, rebuild the latest backend. "
        "Do not use home PC cookies on VPS — remove cookies.txt if present."
    )


def is_facebook_parse_error(message: str) -> bool:
    text = (message or "").lower()
    return "cannot parse data" in text or "[facebook]" in text and "parse" in text


def is_format_unavailable_error(message: str) -> bool:
    text = (message or "").lower()
    return any(
        token in text
        for token in (
            "requested format is not available",
            "no video formats found",
            "format is not available",
        )
    )


def _resolve_downloaded_filepath(ydl: yt_dlp.YoutubeDL, info: dict, opts: dict) -> str:
    for item in info.get("requested_downloads") or []:
        filepath = item.get("filepath")
        if filepath and os.path.exists(filepath):
            return filepath

    filepath = ydl.prepare_filename(info)
    if os.path.exists(filepath):
        return filepath

    base, _ = os.path.splitext(filepath)
    for ext in (".mp3", ".mp4", ".m4a", ".webm", ".mkv", ".opus"):
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate

    outtmpl = str(opts.get("outtmpl") or "")
    output_dir = os.path.dirname(outtmpl) or "."
    marker_match = re.search(r"\[([0-9a-fA-F]{8})\]", outtmpl)
    marker = marker_match.group(1) if marker_match else ""
    if marker and os.path.isdir(output_dir):
        for name in os.listdir(output_dir):
            if marker in name:
                candidate = os.path.join(output_dir, name)
                if os.path.isfile(candidate):
                    return candidate

    raise yt_dlp.utils.DownloadError("Download finished but output file was not found on disk.")


def _run_ydl_with_path(url: str, opts: dict, download: bool) -> Tuple[dict, Optional[str]]:
    platform = detect_platform(url)
    use_script_lock = (
        platform == "youtube"
        and bgutil_script_available()
        and not bgutil_is_reachable()
    )
    lock_ctx = _youtube_script_semaphore if use_script_lock else nullcontext()

    with lock_ctx:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=download)
            if not info:
                raise yt_dlp.utils.DownloadError("No information returned by extractor.")
            filepath = _resolve_downloaded_filepath(ydl, info, opts) if download else None
            return info, filepath


def _run_ydl(url: str, opts: dict, download: bool) -> dict:
    info, _filepath = _run_ydl_with_path(url, opts, download)
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
                    logger.debug(
                        "YouTube client %s (cookies=%s) failed: %s",
                        clients,
                        use_account_cookies,
                        err,
                    )
                    continue
                raise
        if last_error:
            logger.warning(
                "YouTube extraction failed after all client fallbacks (pot_ready=%s): %s",
                pot_provider_ready(),
                last_error,
            )
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


def download_media_with_fallback(
    url: str,
    extra_opts: Optional[Dict] = None,
    *,
    format_id: str = "best",
) -> Tuple[dict, str, dict]:
    """
    Download media with platform-specific retry chains and relaxed format fallbacks.
    Returns (info_dict, output_filepath, ydl_opts_used).
    """
    platform = detect_platform(url)
    extra = dict(extra_opts or {})
    last_error: Optional[Exception] = None

    if platform == "youtube":
        requested_format = extra.get("format") or format_selector(url, format_id)
        format_attempts: List[str] = []
        for candidate in (requested_format, "best[ext=mp4]/best", "best"):
            if candidate and candidate not in format_attempts:
                format_attempts.append(str(candidate))

        plans = build_youtube_try_plans()
        for clients, use_account_cookies in plans:
            for fmt in format_attempts:
                attempt_extra = {**extra, "format": fmt}
                try:
                    opts = base_ydl_opts(
                        url,
                        attempt_extra,
                        player_clients=clients,
                        use_cookies=use_account_cookies,
                    )
                    info, filepath = _run_ydl_with_path(url, opts, download=True)
                    if not filepath:
                        raise yt_dlp.utils.DownloadError(
                            "Download finished but output file was not found on disk."
                        )
                    return info, filepath, opts
                except yt_dlp.utils.DownloadError as err:
                    last_error = err
                    err_text = str(err)
                    if is_retryable_youtube_error(err_text) or is_format_unavailable_error(err_text):
                        continue
                    raise
        if last_error:
            raise last_error

    info, opts = extract_info_with_fallback(url, download=True, extra_opts=extra)
    with yt_dlp.YoutubeDL(opts) as ydl:
        filepath = _resolve_downloaded_filepath(ydl, info, opts)
    return info, filepath, opts


def test_youtube_extract(
    test_url: str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
) -> Dict[str, Any]:
    """Lightweight live probe used by /api/health/youtube-test."""
    result: Dict[str, Any] = {
        "url": test_url,
        "success": False,
        "title": None,
        "error": None,
        "format_count": 0,
        "pot_provider_ready": pot_provider_ready(),
        "bgutil_reachable": bgutil_is_reachable(),
        "bgutil_script_available": bgutil_script_available(),
        "bgutil_script_runnable": bgutil_script_runnable(),
    }
    try:
        info, _opts = extract_info_with_fallback(test_url, download=False)
        result["success"] = True
        result["title"] = info.get("title")
        result["format_count"] = len(info.get("formats") or [])
    except Exception as exc:
        result["error"] = str(exc)[:800]
        logger.warning("YouTube health probe failed: %s", exc)
    return result
