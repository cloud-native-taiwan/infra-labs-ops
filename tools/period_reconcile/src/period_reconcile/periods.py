"""Period math: closed-period identification and missed-period enumeration.

Monthly is the only cadence implemented today, but the model is deliberately
factored so other cadences (weekly, daily) can be added without touching the
reconcile loop: a ``Period`` is just a labelled half-open interval, and the
cadence supplies "the period containing instant T" plus "the period after P".

Periods are anchored on calendar boundaries in a configured timezone (the
cluster runs Asia/Taipei) and a period is "closed" once wall-clock time has
passed its exclusive end. Only closed periods are ever back-filled: an open
(still-accruing) period must not be reported because its data is incomplete.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Period:
    """A labelled half-open interval ``[start, end)``.

    ``label`` is the value handed to the wrapped job (e.g. ``2026-05`` for the
    monthly cadence, passed as ``--month 2026-05``). ``start``/``end`` are
    timezone-aware instants; ``end`` is exclusive.
    """

    label: str
    start: datetime
    end: datetime

    def is_closed_at(self, now: datetime) -> bool:
        """True once ``now`` has reached or passed this period's exclusive end."""
        return now >= self.end


# Label format must stay in sync with the YYYY-MM months consumed by
# tools/usage_reports/src/usage_reports/periods.py.
def _monthly_period(year: int, month: int, tz: ZoneInfo) -> Period:
    start = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end = datetime(year, month + 1, 1, tzinfo=tz)
    return Period(label=f"{year:04d}-{month:02d}", start=start, end=end)


def monthly_period_containing(instant: datetime, timezone: str) -> Period:
    """Return the calendar-month ``Period`` that contains ``instant``.

    ``instant`` must be timezone-aware; it is converted into ``timezone``
    before the calendar month is taken so DST/offset boundaries are correct.
    """
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    tz = ZoneInfo(timezone)
    local = instant.astimezone(tz)
    return _monthly_period(local.year, local.month, tz)


def previous_monthly_period(period: Period, timezone: str) -> Period:
    """Return the calendar month immediately before ``period``."""
    tz = ZoneInfo(timezone)
    # period.start is the first instant of its month; step back one day to
    # land in the prior month, then take that month's bounds.
    prior_local = (period.start.astimezone(tz) - timedelta(days=1))
    return _monthly_period(prior_local.year, prior_local.month, tz)


def enumerate_missed_periods(
    *,
    last_success_label: str | None,
    now: datetime,
    timezone: str,
    max_backfill: int,
) -> tuple[Period, ...]:
    """Closed periods that still need a successful run, oldest first.

    A period is "missed" if it is closed at ``now`` and strictly newer than
    ``last_success_label``. When there is no watermark yet, only the single
    most-recent closed period is returned -- never the entire history -- so a
    fresh deploy does not attempt to back-fill years of reports it has no data
    for. ``max_backfill`` caps the number of periods returned (oldest first)
    so a long outage produces a bounded, predictable run.
    """
    if max_backfill < 1:
        raise ValueError("max_backfill must be >= 1")

    current = monthly_period_containing(now, timezone)
    newest_closed = previous_monthly_period(current, timezone)
    if not newest_closed.is_closed_at(now):
        # Defensive: previous month is always closed, but keep the invariant
        # explicit so a future cadence cannot leak an open period.
        return ()

    if last_success_label is None:
        return (newest_closed,)

    if last_success_label >= newest_closed.label:
        # Watermark is at or ahead of the newest closed period: nothing to do.
        # ``>=`` (not ``==``) so a watermark somehow ahead of wall-clock --
        # e.g. after a manual back-fill or a clock skew -- is treated as
        # up-to-date rather than enumerating a negative range.
        return ()

    # Walk backwards from the newest closed period until we reach the
    # watermark, collecting each period that is newer than it.
    collected: list[Period] = []
    cursor = newest_closed
    while cursor.label > last_success_label:
        collected.append(cursor)
        cursor = previous_monthly_period(cursor, timezone)
    collected.reverse()  # oldest first
    return tuple(collected[:max_backfill])
