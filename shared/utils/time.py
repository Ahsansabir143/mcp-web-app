from datetime import datetime, timezone


def now_ms() -> int:
    """Current UTC time in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def ms_to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def datetime_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
