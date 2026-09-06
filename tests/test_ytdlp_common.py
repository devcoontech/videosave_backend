import os
import pytest
from backend.app.services.ytdlp_common import (
    build_youtube_try_plans,
    extractor_args_for,
    should_use_youtube_cookies,
    cookies_diagnostics,
)


def test_anonymous_youtube_try_plans_prioritizes_web_and_mweb(monkeypatch):
    # Ensure no logged-in cookies are detected
    monkeypatch.setattr(
        "backend.app.services.ytdlp_common.has_logged_in_cookies", lambda: False
    )
    plans = build_youtube_try_plans()
    assert len(plans) > 0
    # First plan should use 'web' without account cookies
    first_clients, use_cookies = plans[0]
    assert first_clients == ["web"]
    assert use_cookies is False

    # Second plan should use 'web', 'mweb'
    second_clients, _ = plans[1]
    assert second_clients == ["web", "mweb"]


def test_extractor_args_includes_bgutil_options_and_does_not_skip_webpage(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.ytdlp_common.pot_provider_ready", lambda: True
    )
    args = extractor_args_for("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    youtube_args = args.get("youtube", {})
    # player_skip webpage should NOT be set so visitorData can be extracted
    assert "player_skip" not in youtube_args
    assert "player_client" in youtube_args


def test_should_use_youtube_cookies_returns_false_without_login_info(monkeypatch):
    # Mock cookies diagnostics for guest cookies (no LOGIN_INFO, no SID)
    monkeypatch.setattr(
        "backend.app.services.ytdlp_common.cookies_diagnostics",
        lambda: {
            "configured": True,
            "path": "/app/cookies.txt",
            "youtube_entries": 5,
            "has_login_info": False,
            "has_sid": False,
            "instagram_entries": 0,
            "facebook_entries": 0,
            "tiktok_entries": 0,
        },
    )
    assert should_use_youtube_cookies() is False
