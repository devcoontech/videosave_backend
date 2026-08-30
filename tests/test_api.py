import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.utils.urls import detect_platform
from backend.app.utils.filenames import sanitize_filename, format_playlist_filename
from backend.app.core.security import is_allowed_domain

client = TestClient(app)


def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "ffmpeg_available" in data


def test_platform_detection():
    assert detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
    assert detect_platform("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert detect_platform("https://www.youtube.com/playlist?list=PL123456") == "youtube_playlist"
    assert detect_platform("https://www.instagram.com/reel/C12345678/") == "instagram"
    assert detect_platform("https://www.facebook.com/reel/123456789/") == "facebook"
    assert detect_platform("https://invalidwebsite.com/video") == "unsupported"


def test_domain_whitelist_security():
    assert is_allowed_domain("https://www.youtube.com/watch?v=123") is True
    assert is_allowed_domain("https://instagram.com/reel/123") is True
    assert is_allowed_domain("https://m.facebook.com/watch/123") is True
    assert is_allowed_domain("http://malicious-site.com") is False
    assert is_allowed_domain("http://localhost:8000") is False


def test_filename_sanitization():
    unsafe_name = "Video: Title - With * Invalid <Chars>|?.mp4"
    cleaned = sanitize_filename(unsafe_name)
    assert ":" not in cleaned
    assert "/" not in cleaned
    assert "*" not in cleaned
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert "|" not in cleaned
    assert "?" not in cleaned
    assert cleaned.startswith("Video Title - With Invalid Chars")

    # Path traversal protection
    path_traversal = "../../etc/passwd"
    assert sanitize_filename(path_traversal) == "passwd"


def test_format_playlist_filename():
    formatted = format_playlist_filename(3, "Awesome Song!", "mp4")
    assert formatted == "003 - Awesome Song!.mp4"


def test_invalid_url_media_info():
    res = client.post("/api/media/info", json={"url": "https://unknownwebsite.org/video"})
    assert res.status_code == 400
    data = res.json()
    assert "UNSUPPORTED_PLATFORM" in str(data)
