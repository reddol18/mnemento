"""Time handling per ADR-0004.

- Instants (`at`, `recorded_at`, `format: date-time` fields) are ISO 8601 *with* a UTC offset.
  Naive values are rejected. Ordering/comparison uses the UTC-normalized form.
- Calendar dates (`format: date` fields) are `YYYY-MM-DD` in the user's time zone.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .errors import InvalidTimeError

DEFAULT_TZ = "Asia/Seoul"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Require a date and a time part; the offset check happens after parsing.
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def parse_instant(value: str | datetime) -> datetime:
    """Parse an offset-aware ISO 8601 instant. Raises InvalidTimeError for naive or bad values."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        if not _DATETIME_RE.match(value):
            raise InvalidTimeError(f"not an ISO 8601 date-time: {value!r}")
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise InvalidTimeError(f"not an ISO 8601 date-time: {value!r}") from exc
    else:
        raise InvalidTimeError(f"expected ISO 8601 string or datetime, got {type(value).__name__}")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise InvalidTimeError(f"date-time must include a UTC offset (e.g. +09:00): {value!r}")
    return dt


def is_instant(value: object) -> bool:
    try:
        parse_instant(value)  # type: ignore[arg-type]
    except InvalidTimeError:
        return False
    return True


def is_calendar_date(value: object) -> bool:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def format_instant(dt: datetime) -> str:
    """Canonical storage form: ISO 8601 keeping the original offset."""
    return dt.isoformat()


def utc_sort_key(dt: datetime) -> str:
    """Fixed-width UTC string; lexical order equals chronological order."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now(tz: str = DEFAULT_TZ) -> datetime:
    return datetime.now(ZoneInfo(tz))
