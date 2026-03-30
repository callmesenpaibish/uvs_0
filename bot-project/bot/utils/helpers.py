import time
from datetime import datetime


def humanbytes(size: int) -> str:
    if not size:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def time_formatter(seconds: int) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def readable_date(dt: datetime) -> str:
    return dt.strftime("%d %b %Y, %I:%M %p UTC")


def format_time_left(expiry_ts: float | int | None) -> str:
    if not expiry_ts:
        return "Unknown"
    if isinstance(expiry_ts, datetime):
        remaining = int((expiry_ts - datetime.utcnow()).total_seconds())
    else:
        remaining = int(float(expiry_ts) - time.time())
    if remaining <= 0:
        return "Expired"
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    minutes = (remaining % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "< 1m"
