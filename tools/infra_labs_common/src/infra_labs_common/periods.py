"""Calendar-month boundary math shared by the reporting tools.

A "month" is anchored on calendar boundaries in a given timezone (the cluster
runs Asia/Taipei). ``month_bounds`` returns the half-open interval
``[start, end)`` for a month as timezone-aware instants; callers convert to UTC
or wrap it in their own period type as needed.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def month_bounds(year: int, month: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Return ``(start, end)`` for the calendar month in ``tz``.

    ``start`` is the first instant of ``month``; ``end`` is the first instant of
    the following month (exclusive), rolling the year over at December.
    """
    start = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end = datetime(year, month + 1, 1, tzinfo=tz)
    return start, end
