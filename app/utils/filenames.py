import re
import os


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """
    Sanitize string for use as OS filename.
    Strips invalid characters (\\ / : * ? " < > |) and normalizes whitespace.
    Prevents path traversal attacks.
    """
    if not name:
        return "downloaded_media"

    # Get basename to prevent path traversal
    basename = os.path.basename(name.replace("\\", "/"))

    # Strip invalid OS characters
    sanitized = re.sub(r'[\\/*?:"<>|]', "", basename)

    # Strip leading/trailing dots and normalize spaces
    sanitized = re.sub(r"^\.+", "", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()

    # Fallback if empty after sanitizing
    if not sanitized:
        sanitized = "downloaded_media"

    # Truncate if filename is excessively long
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip()

    return sanitized


def format_playlist_filename(index: int, title: str, ext: str = "mp4") -> str:
    """Format safe filename for playlist items: '001 - Video Title.mp4'."""
    safe_title = sanitize_filename(title)
    return f"{index:03d} - {safe_title}.{ext}"
