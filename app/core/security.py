import re
from urllib.parse import urlparse
from fastapi import HTTPException, status

ALLOWED_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "instagram.com",
    "www.instagram.com",
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "fb.watch",
    "tiktok.com",
    "www.tiktok.com",
    "vt.tiktok.com",
    "vm.tiktok.com",
    "m.tiktok.com",
}


def is_allowed_domain(url: str) -> bool:
    """Validate host against anti-SSRF allowed domain whitelist."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        hostname = hostname.lower()

        # Check if domain matches whitelist or subdomain of allowed
        for allowed in ALLOWED_DOMAINS:
            if hostname == allowed or hostname.endswith("." + allowed):
                return True
        return False
    except Exception:
        return False


def validate_and_normalize_url(url: str) -> str:
    """Validate media URL structure and domain."""
    cleaned_url = url.strip()
    if not cleaned_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_URL", "message": "URL cannot be empty."},
        )

    if not is_allowed_domain(cleaned_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "UNSUPPORTED_PLATFORM",
                "message": "Domain is not supported. Please use YouTube, Instagram, Facebook, or TikTok URLs.",
            },
        )

    return cleaned_url
