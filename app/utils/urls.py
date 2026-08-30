from urllib.parse import urlparse, parse_qs


def detect_platform(url: str) -> str:
    """
    Detect platform type from media URL.
    Returns: youtube, youtube_playlist, instagram, facebook, or unsupported
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        query = parse_qs(parsed.query)

        # YouTube check
        if any(h in hostname for h in ["youtube.com", "youtu.be"]):
            # Check playlist
            if "list" in query or "/playlist" in path:
                return "youtube_playlist"
            if "/watch" in path or "/shorts" in path or hostname == "youtu.be" or "/v/" in path:
                return "youtube"
            return "youtube"

        # Instagram check
        if "instagram.com" in hostname:
            if "/reel/" in path or "/reels/" in path or "/p/" in path or "/tv/" in path:
                return "instagram"
            return "instagram"

        # Facebook check
        if any(h in hostname for h in ["facebook.com", "fb.watch"]):
            if "/reel/" in path or "/watch/" in path or "/videos/" in path or hostname == "fb.watch":
                return "facebook"
            return "facebook"

        # TikTok check
        if any(h in hostname for h in ["tiktok.com", "vt.tiktok.com", "vm.tiktok.com"]):
            return "tiktok"

        return "unsupported"
    except Exception:
        return "unsupported"
