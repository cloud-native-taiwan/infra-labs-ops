"""Period-job integrity contract for deploy-host cron.

Supercronic does not catch up jobs that were due while the container was
down (unlike systemd ``Persistent=true``). For period-anchored jobs -- such
as the monthly usage report -- a missed fire window means the report for a
closed month silently never runs, and the only detection mechanism is a
human noticing its absence.

This package inverts the model from "fire at a wall-clock time" to
"reconcile toward the invariant: a successful run exists for every closed
period". A thin wrapper records a per-job last-success watermark on disk and,
on every hourly tick and on container start, back-fills each closed period
that has not yet succeeded.

See ``README.md`` for the full contract.
"""
from __future__ import annotations

from period_reconcile.periods import (
    Period,
    enumerate_missed_periods,
    monthly_period_containing,
    previous_monthly_period,
)
from period_reconcile.reconcile import ReconcileOutcome, reconcile
from period_reconcile.watermark import Watermark, load_watermark, save_watermark

__all__ = [
    "Period",
    "ReconcileOutcome",
    "Watermark",
    "enumerate_missed_periods",
    "load_watermark",
    "monthly_period_containing",
    "previous_monthly_period",
    "reconcile",
    "save_watermark",
]
