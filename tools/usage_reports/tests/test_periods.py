from datetime import datetime, timezone

import pytest

from usage_reports.periods import build_period, parse_month, previous_month


def test_parse_month_valid() -> None:
    assert parse_month("2026-05") == (2026, 5)
    assert parse_month("2026-12") == (2026, 12)


@pytest.mark.parametrize("bad", ["2026-13", "2026-00", "bad", "26-5", "2026/05", ""])
def test_parse_month_invalid_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_month(bad)


def test_previous_month_within_year() -> None:
    assert previous_month(datetime(2026, 5, 27)) == (2026, 4)


def test_previous_month_january_rolls_year() -> None:
    assert previous_month(datetime(2026, 1, 15)) == (2025, 12)


def test_build_period_asia_taipei_to_utc() -> None:
    p = build_period(2026, 5, "Asia/Taipei")
    # 2026-05-01 00:00 +08:00 == 2026-04-30 16:00 UTC
    assert p.begin_utc == datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc)
    # 2026-06-01 00:00 +08:00 == 2026-05-31 16:00 UTC
    assert p.end_utc == datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc)
    assert p.label == "2026-05"


def test_build_period_december_rolls_year() -> None:
    p = build_period(2026, 12, "Asia/Taipei")
    assert p.end_utc == datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc)


def test_build_period_utc() -> None:
    p = build_period(2026, 5, "UTC")
    assert p.begin_utc == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    assert p.end_utc == datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


def test_build_period_invalid_month() -> None:
    with pytest.raises(ValueError):
        build_period(2026, 13, "UTC")
