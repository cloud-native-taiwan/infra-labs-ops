"""``period-reconcile`` CLI -- the supercronic-facing entry point.

Usage (as wired into a crontab / entrypoint)::

    period-reconcile \
        --job usage-reports \
        --watermark /var/lib/usage-reports/reconcile-watermark.json \
        --lock /tmp/usage-reports-reconcile.lock \
        --timezone Asia/Taipei \
        --max-backfill 6 \
        -- /usr/local/bin/usage-reports generate --month {period}

Everything after ``--`` is the wrapped job's command template. The literal
token ``{period}`` is substituted with each missed period's label (e.g.
``2026-05``) before the command is run as a subprocess. The subprocess exit
code flows straight into the 0/2/1 contract.
"""
from __future__ import annotations

import argparse
import logging
import subprocess  # noqa: S404 -- wrapped job command is operator-supplied, not user input
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from period_reconcile.lock import LockHeld, reconcile_lock
from period_reconcile.logging_config import configure_logging
from period_reconcile.reconcile import EXIT_OK, reconcile

LOGGER = logging.getLogger(__name__)

PERIOD_TOKEN = "{period}"  # noqa: S105 -- substitution placeholder, not a credential


def main(argv: Sequence[str] | None = None, _now: datetime | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    command_template = tuple(args.command)
    if not command_template:
        LOGGER.error("no wrapped command given after '--'")
        return 1
    if not any(PERIOD_TOKEN in token for token in command_template):
        LOGGER.error(
            "wrapped command has no %s placeholder; refusing to run (it would "
            "ignore the period being back-filled)",
            PERIOD_TOKEN,
        )
        return 1

    def run_period(label: str) -> int:
        cmd = [token.replace(PERIOD_TOKEN, label) for token in command_template]
        LOGGER.info("job=%s exec: %s", args.job, " ".join(cmd))
        completed = subprocess.run(cmd, check=False)  # noqa: S603 -- operator-supplied argv
        return completed.returncode

    # _now is a test-only injection seam; production always uses the clock.
    now = _now if _now is not None else datetime.now(UTC)
    try:
        with reconcile_lock(args.lock):
            outcome = reconcile(
                job=args.job,
                watermark_path=args.watermark,
                run_period=run_period,
                now=now,
                timezone=args.timezone,
                max_backfill=args.max_backfill,
            )
    except LockHeld as exc:
        # Another reconcile is in progress: this is expected (hourly tick
        # overlapping a boot run). Exit 0 -- the other pass owns the work.
        LOGGER.info("%s; exiting 0 (another reconcile owns this pass)", exc)
        return EXIT_OK

    LOGGER.info(
        "job=%s reconcile done exit=%s succeeded=%s watermark=%s",
        args.job,
        outcome.exit_code,
        list(outcome.succeeded),
        outcome.final_watermark,
    )
    return outcome.exit_code


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="period-reconcile",
        description="Back-fill closed periods a scheduled job missed.",
    )
    parser.add_argument(
        "--job",
        required=True,
        help="Stable job name; namespaces the watermark and appears in logs.",
    )
    parser.add_argument(
        "--watermark",
        required=True,
        help="Path to the JSON watermark file (on the job's persistent volume).",
    )
    parser.add_argument(
        "--lock",
        required=True,
        help="Path to the advisory lock file (double-fire protection).",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Taipei",
        help="Timezone for calendar-period boundaries (default: Asia/Taipei).",
    )
    parser.add_argument(
        "--max-backfill",
        type=int,
        default=6,
        help="Maximum closed periods to back-fill in one pass (default: 6).",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="-- <command ...> with a {period} placeholder for the period label.",
    )

    parsed = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    # argparse.REMAINDER keeps the leading '--'; strip it for a clean argv.
    if parsed.command and parsed.command[0] == "--":
        parsed.command = parsed.command[1:]
    return parsed


if __name__ == "__main__":
    sys.exit(main())
