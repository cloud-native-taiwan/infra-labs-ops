"""The reconcile loop: back-fill every closed period the job has missed.

Exit-code contract (the house 0/2/1 convention), reflecting the *worst*
outcome across all attempted periods:

* 0  success -- nothing to do, or every attempted period exited 0.
* 2  refused/gated -- a wrapped job exited 2 (e.g. usage_reports' freshness
     gate: data not ready). This is not an error; we stop and will retry on
     the next tick. The watermark is NOT advanced past the refused period.
* 1  error -- a wrapped job exited 1 (or any unexpected code). We stop and
     surface failure so a human is alerted.

Stop-on-failure: periods are attempted oldest-first and the loop halts at the
first non-zero exit so we never skip a hole. The watermark advances only past
periods that exited 0, so a partial back-fill resumes exactly where it left
off on the next tick.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from period_reconcile.periods import enumerate_missed_periods
from period_reconcile.watermark import load_watermark, save_watermark

LOGGER = logging.getLogger(__name__)

# A runner takes a period label and returns the wrapped job's exit code.
JobRunner = Callable[[str], int]

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_ERROR = 1


@dataclass(frozen=True)
class ReconcileOutcome:
    """Result of a reconcile pass."""

    exit_code: int
    succeeded: tuple[str, ...]
    final_watermark: str | None


def reconcile(
    *,
    job: str,
    watermark_path: str,
    run_period: JobRunner,
    now: datetime,
    timezone: str,
    max_backfill: int,
) -> ReconcileOutcome:
    """Run one reconcile pass for ``job``.

    The caller is responsible for holding the reconcile lock around this call
    (see ``lock.reconcile_lock``) so two passes cannot overlap. ``run_period``
    must be idempotent for a closed period: re-running an already-delivered
    period must not duplicate side effects (usage_reports satisfies this via
    its per-recipient delivery manifest).
    """
    watermark = load_watermark(watermark_path, job)
    missed = enumerate_missed_periods(
        last_success_label=watermark.last_success_label,
        now=now,
        timezone=timezone,
        max_backfill=max_backfill,
    )

    if not missed:
        LOGGER.info(
            "job=%s up-to-date (watermark=%s); nothing to back-fill",
            job,
            watermark.last_success_label,
        )
        return ReconcileOutcome(
            exit_code=EXIT_OK,
            succeeded=(),
            final_watermark=watermark.last_success_label,
        )

    LOGGER.info(
        "job=%s back-filling %d period(s): %s (watermark=%s)",
        job,
        len(missed),
        ", ".join(p.label for p in missed),
        watermark.last_success_label,
    )

    succeeded: list[str] = []
    worst = EXIT_OK

    for period in missed:
        LOGGER.info("job=%s running period=%s", job, period.label)
        code = run_period(period.label)

        if code == EXIT_OK:
            watermark = watermark.advanced_to(period.label, now)
            # Persist after every success so a crash mid-back-fill does not
            # re-run an already-completed period on the next tick.
            save_watermark(watermark_path, watermark)
            succeeded.append(period.label)
            LOGGER.info(
                "job=%s period=%s succeeded; watermark advanced to %s",
                job,
                period.label,
                watermark.last_success_label,
            )
            continue

        if code == EXIT_REFUSED:
            worst = EXIT_REFUSED
            LOGGER.warning(
                "job=%s period=%s refused (exit 2); stopping back-fill, will "
                "retry next tick. Watermark stays at %s",
                job,
                period.label,
                watermark.last_success_label,
            )
            break

        # Any other non-zero code is an error.
        worst = EXIT_ERROR
        LOGGER.error(
            "job=%s period=%s failed (exit %s); stopping back-fill. "
            "Watermark stays at %s",
            job,
            period.label,
            code,
            watermark.last_success_label,
        )
        break

    return ReconcileOutcome(
        exit_code=worst,
        succeeded=tuple(succeeded),
        final_watermark=watermark.last_success_label,
    )
