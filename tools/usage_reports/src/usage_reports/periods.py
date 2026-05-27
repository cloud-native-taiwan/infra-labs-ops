"""Period math for monthly reports.

CloudKitty expects UTC timestamps, but the cluster operates on
Asia/Taipei time. A "month" in user-facing terms means the calendar
month boundaries in the operator's local timezone, expressed as UTC for
API queries.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from usage_reports.models import ReportPeriod


def parse_month(value: str) -> tuple[int, int]:
    """Parse a YYYY-MM string into (year, month). Raises ValueError on bad input."""
    parts = value.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"month must be in YYYY-MM format, got: {value!r}")
    year_s, month_s = parts
    if len(year_s) != 4 or len(month_s) != 2:
        raise ValueError(f"month must be in YYYY-MM format, got: {value!r}")
    year = int(year_s)
    month = int(month_s)
    if month < 1 or month > 12:
        raise ValueError(f"month component must be 1-12, got: {month}")
    return year, month


def previous_month(reference: datetime) -> tuple[int, int]:
    """Return (year, month) of the calendar month before `reference`."""
    first_of_this_month = reference.replace(day=1)
    last_of_prev = first_of_this_month - timedelta(days=1)
    return last_of_prev.year, last_of_prev.month


def build_period(year: int, month: int, timezone: str) -> ReportPeriod:
    """Build a ReportPeriod with begin/end in UTC, anchored on calendar
    boundaries in the given timezone."""
    if month < 1 or month > 12:
        raise ValueError(f"month must be 1-12, got: {month}")
    tz = ZoneInfo(timezone)
    begin_local = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=tz)

    begin_utc = begin_local.astimezone(ZoneInfo("UTC"))
    end_utc = end_local.astimezone(ZoneInfo("UTC"))
    return ReportPeriod(year=year, month=month, begin_utc=begin_utc, end_utc=end_utc)
