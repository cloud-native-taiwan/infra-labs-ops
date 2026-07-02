from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from infra_labs_common.periods import month_bounds


def test_bounds_within_year() -> None:
    tz = ZoneInfo("Asia/Taipei")
    start, end = month_bounds(2026, 5, tz)
    assert start == datetime(2026, 5, 1, tzinfo=tz)
    assert end == datetime(2026, 6, 1, tzinfo=tz)


def test_december_rolls_year() -> None:
    tz = ZoneInfo("Asia/Taipei")
    start, end = month_bounds(2026, 12, tz)
    assert start == datetime(2026, 12, 1, tzinfo=tz)
    assert end == datetime(2027, 1, 1, tzinfo=tz)


def test_bounds_are_tzaware_in_requested_zone() -> None:
    tz = ZoneInfo("UTC")
    start, end = month_bounds(2026, 1, tz)
    assert start.tzinfo == tz
    assert end.tzinfo == tz


def test_dst_zone_bounds_use_local_offset() -> None:
    # March 2026 in New York starts EST (-05:00) and ends EDT (-04:00).
    tz = ZoneInfo("America/New_York")
    start, end = month_bounds(2026, 3, tz)
    assert start.utcoffset() != end.utcoffset()
