"""Relative date tokens (@today, @today-1d, @last_month_start, ...) resolved in the user's time zone
(ADR-0004). The resolution is reported back in the answer so the reader sees the actual dates."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .spec import MONTH_TOKEN_RE, RELATIVE_DATE_RE


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28, 31, 30, 31, 30,
            31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def _month_end(year: int, month: int) -> date:
    return _add_months(date(year, month, 1), 1) - timedelta(days=1)


def resolve_month_token(token: str) -> str:
    m = MONTH_TOKEN_RE.match(token)
    if not m:
        raise ValueError(f"not a month token: {token!r}")
    kind, year, month = m.group(1), int(m.group(2)), int(m.group(3))
    first, last = date(year, month, 1), _month_end(year, month)
    if kind == "month_start":
        return first.isoformat()
    if kind == "month_end":
        return last.isoformat()
    sunday = last - timedelta(days=(last.weekday() + 1) % 7)  # last Sunday inside the month
    if kind == "last_full_week_end":
        return sunday.isoformat()
    return (sunday - timedelta(days=6)).isoformat()


def resolve_relative(token: str, now: datetime) -> str:
    if MONTH_TOKEN_RE.match(token):
        return resolve_month_token(token)
    m = RELATIVE_DATE_RE.match(token)
    if not m:
        raise ValueError(f"not a relative date token: {token!r}")
    base_name, offset = m.group(1), m.group(2)
    today = now.date()
    base = {
        "today": today,
        "this_week_start": today - timedelta(days=today.weekday()),
        "this_month_start": today.replace(day=1),
        "last_month_start": _add_months(today.replace(day=1), -1),
        "this_year_start": today.replace(month=1, day=1),
    }[base_name]
    if offset:
        sign = -1 if offset[0] == "-" else 1
        n, unit = int(offset[1:-1]) * sign, offset[-1]
        if unit == "d":
            base = base + timedelta(days=n)
        elif unit == "w":
            base = base + timedelta(weeks=n)
        else:
            base = _add_months(base, n)
    return base.isoformat()
