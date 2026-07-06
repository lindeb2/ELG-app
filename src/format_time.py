"""Format elapsed seconds as MM:SS or HH:MM:SS."""


def format_time(seconds: int | float) -> str:
    """Converts seconds to MM:SS or HH:MM:SS."""
    total = int(float(seconds or 0))
    if total <= 0:
        return "00:00"
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours >= 24:
        return f"{hours} hours"
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
