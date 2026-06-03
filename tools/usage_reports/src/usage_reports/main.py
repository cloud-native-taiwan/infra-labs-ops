from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from usage_reports.config import AppConfig, load_config
from usage_reports.logging_config import configure_logging
from usage_reports.models import ReportPeriod
from usage_reports.orchestrator import run_report
from usage_reports.periods import build_period, parse_month, previous_month


LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging("INFO")
    args = _parse_args(argv)
    return int(args.func(args))


def _handle_generate(args: argparse.Namespace) -> int:
    config = _load_handler_config(dry_run=args.dry_run)
    period = _resolve_period(args.month, config.report_timezone)

    # Phase 3-U7 wires this in; the scaffold (U3) only resolves the period.
    LOGGER.info(
        "usage-reports generate month=%s begin_utc=%s end_utc=%s dry_run=%s",
        period.label,
        period.begin_utc.isoformat(),
        period.end_utc.isoformat(),
        config.dry_run,
    )
    return run_report(
        config,
        period,
        force=args.force,
        only_project=args.only_project,
        only_email=args.only_email,
        record_deliveries=args.record_deliveries,
    )


def _resolve_period(month_arg: str | None, timezone: str) -> ReportPeriod:
    if month_arg:
        year, month = parse_month(month_arg)
    else:
        now_local = datetime.now(ZoneInfo(timezone))
        year, month = previous_month(now_local)
    return build_period(year, month, timezone)


def _load_handler_config(*, dry_run: bool) -> AppConfig:
    config = load_config(require_all=not dry_run)
    configure_logging(config.log_level)
    if dry_run:
        return replace(config, dry_run=True)
    return config


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="usage-reports")
    subparsers = parser.add_subparsers(dest="command")

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--dry-run", action="store_true")
    generate_parser.add_argument(
        "--month",
        help="Reporting month in YYYY-MM (defaults to previous calendar month)",
        default=None,
    )
    generate_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass freshness check and delivery idempotency manifest",
    )
    generate_parser.add_argument(
        "--only-project",
        default=None,
        help="Restrict the run to a single project by project_id (for testing)",
    )
    generate_parser.add_argument(
        "--only-email",
        default=None,
        help="Restrict delivery to a single recipient email (for testing)",
    )
    generate_parser.add_argument(
        "--record-deliveries",
        action="store_true",
        help=(
            "Record deliveries to the manifest even on a scoped "
            "(--only-project/--only-email) run. Scoped runs skip the manifest "
            "by default so a test send does not suppress the next scheduled run."
        ),
    )
    generate_parser.set_defaults(func=_handle_generate)

    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    if not parsed_argv or parsed_argv[0].startswith("-"):
        parsed_argv.insert(0, "generate")

    return parser.parse_args(parsed_argv)


if __name__ == "__main__":
    sys.exit(main())
