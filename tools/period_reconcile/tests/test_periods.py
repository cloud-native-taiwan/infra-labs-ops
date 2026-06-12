from __future__ import annotations

from datetime import UTC, datetime

import pytest

from period_reconcile.periods import (
    enumerate_missed_periods,
    monthly_period_containing,
    previous_monthly_period,
)

TZ = "Asia/Taipei"


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_monthly_period_containing_taipei_boundary() -> None:
    # 2026-05-31 16:00 UTC == 2026-06-01 00:00 +08:00 -> June period.
    p = monthly_period_containing(_utc(2026, 5, 31, 16, 0), TZ)
    assert p.label == "2026-06"
    # One second earlier is still May in Taipei.
    p2 = monthly_period_containing(_utc(2026, 5, 31, 15, 59, 59), TZ)
    assert p2.label == "2026-05"


def test_monthly_period_bounds_are_calendar_month() -> None:
    p = monthly_period_containing(_utc(2026, 5, 15), TZ)
    assert p.label == "2026-05"
    assert p.start == datetime(2026, 5, 1, tzinfo=p.start.tzinfo)
    assert p.end == datetime(2026, 6, 1, tzinfo=p.end.tzinfo)


def test_monthly_period_requires_tzaware() -> None:
    with pytest.raises(ValueError):
        monthly_period_containing(datetime(2026, 5, 15), TZ)  # noqa: DTZ001


def test_previous_monthly_period_within_year() -> None:
    p = monthly_period_containing(_utc(2026, 5, 15), TZ)
    prev = previous_monthly_period(p, TZ)
    assert prev.label == "2026-04"


def test_previous_monthly_period_rolls_year() -> None:
    jan = monthly_period_containing(_utc(2026, 1, 15), TZ)
    prev = previous_monthly_period(jan, TZ)
    assert prev.label == "2025-12"


def test_is_closed_at() -> None:
    may = monthly_period_containing(_utc(2026, 5, 15), TZ)
    # June 1 00:00 +08:00 == May 31 16:00 UTC: May is now closed.
    assert may.is_closed_at(_utc(2026, 5, 31, 16, 0)) is True
    assert may.is_closed_at(_utc(2026, 5, 31, 15, 0)) is False


def test_enumerate_no_watermark_returns_single_latest_closed() -> None:
    # Mid-June: the newest closed period is May.
    missed = enumerate_missed_periods(
        last_success_label=None,
        now=_utc(2026, 6, 12),
        timezone=TZ,
        max_backfill=6,
    )
    assert tuple(p.label for p in missed) == ("2026-05",)


def test_enumerate_up_to_date_returns_empty() -> None:
    missed = enumerate_missed_periods(
        last_success_label="2026-05",
        now=_utc(2026, 6, 12),
        timezone=TZ,
        max_backfill=6,
    )
    assert missed == ()


def test_enumerate_watermark_ahead_returns_empty() -> None:
    missed = enumerate_missed_periods(
        last_success_label="2026-07",
        now=_utc(2026, 6, 12),
        timezone=TZ,
        max_backfill=6,
    )
    assert missed == ()


def test_enumerate_multiple_missed_oldest_first() -> None:
    # Watermark at Feb, now mid-June -> Mar, Apr, May missed (June still open).
    missed = enumerate_missed_periods(
        last_success_label="2026-02",
        now=_utc(2026, 6, 12),
        timezone=TZ,
        max_backfill=6,
    )
    assert tuple(p.label for p in missed) == ("2026-03", "2026-04", "2026-05")


def test_enumerate_crosses_year_boundary() -> None:
    missed = enumerate_missed_periods(
        last_success_label="2025-11",
        now=_utc(2026, 2, 15),
        timezone=TZ,
        max_backfill=6,
    )
    assert tuple(p.label for p in missed) == ("2025-12", "2026-01")


def test_enumerate_respects_max_backfill_oldest_first() -> None:
    missed = enumerate_missed_periods(
        last_success_label="2026-01",
        now=_utc(2026, 6, 12),
        timezone=TZ,
        max_backfill=2,
    )
    # Feb..May missed, capped to oldest 2.
    assert tuple(p.label for p in missed) == ("2026-02", "2026-03")


def test_dst_zone_month_containment() -> None:
    """Month boundaries in a DST-observing zone follow the local offset.

    New York is EDT (-04:00) at the April boundary: 2026-04-01 00:00 local
    is 04:00 UTC.
    """
    assert monthly_period_containing(_utc(2026, 4, 1, 3, 59), "America/New_York").label == "2026-03"
    assert monthly_period_containing(_utc(2026, 4, 1, 4, 0), "America/New_York").label == "2026-04"


def test_dst_zone_closed_at_spans_offset_change() -> None:
    """March 2026 in New York starts EST (-05:00) and ends EDT (-04:00);
    is_closed_at must honour the end-of-month offset, not the start's."""
    march = monthly_period_containing(_utc(2026, 3, 15), "America/New_York")
    assert march.start == datetime(2026, 3, 1, tzinfo=march.start.tzinfo)
    assert march.is_closed_at(_utc(2026, 4, 1, 4, 0)) is True
    assert march.is_closed_at(_utc(2026, 4, 1, 3, 59)) is False


def test_dst_zone_previous_month_rollover() -> None:
    april = monthly_period_containing(_utc(2026, 4, 15), "America/New_York")
    prev = previous_monthly_period(april, "America/New_York")
    assert prev.label == "2026-03"
    assert prev.end == april.start


def test_enumerate_rejects_bad_max_backfill() -> None:
    with pytest.raises(ValueError):
        enumerate_missed_periods(
            last_success_label=None,
            now=_utc(2026, 6, 12),
            timezone=TZ,
            max_backfill=0,
        )
