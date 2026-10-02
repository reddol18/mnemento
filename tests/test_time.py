from datetime import datetime

import pytest

from mnemento.errors import InvalidTimeError
from mnemento.timeutil import is_calendar_date, parse_instant, utc_sort_key


@pytest.mark.parametrize(
    "value",
    ["2026-10-02T17:40:00+09:00", "2026-10-02T17:40+09:00", "2026-10-02T08:40:00Z",
     "2026-10-02T08:40:00.123456+00:00"],
)
def test_instant_with_offset_accepted(value):
    assert parse_instant(value).utcoffset() is not None


@pytest.mark.parametrize(
    "value", ["2026-10-02T17:40:00", "2026-10-02 17:40", "2026-10-02", "yesterday", "", 1700000000]
)
def test_instant_without_offset_rejected(value):
    with pytest.raises(InvalidTimeError):
        parse_instant(value)


def test_naive_datetime_object_rejected():
    with pytest.raises(InvalidTimeError):
        parse_instant(datetime(2026, 10, 2, 17, 40))


def test_utc_sort_key_orders_across_offsets():
    # 09:00 KST is 00:00 UTC, which is earlier than 01:00 UTC
    kst = parse_instant("2026-10-02T09:00:00+09:00")
    utc = parse_instant("2026-10-02T01:00:00+00:00")
    assert utc_sort_key(kst) < utc_sort_key(utc)


@pytest.mark.parametrize(
    "value,ok",
    [("2026-10-02", True), ("2026-02-30", False), ("2026-10-2", False),
     ("2026-10-02T00:00:00+09:00", False), (20261002, False)],
)
def test_calendar_date(value, ok):
    assert is_calendar_date(value) is ok
