def format_bytes(bytes_count: float) -> str:
    """Format bytes count into human-readable string (e.g. 12.5 MB)."""
    if bytes_count is None or bytes_count <= 0:
        return "Unknown size"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while bytes_count >= 1024 and i < len(units) - 1:
        bytes_count /= 1024
        i += 1
    return f"{bytes_count:.1f} {units[i]}"


def format_duration(seconds: float) -> str:
    """Format duration in seconds to MM:SS or HH:MM:SS format."""
    if not seconds or seconds <= 0:
        return "00:00"
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    sec = total_sec % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"
