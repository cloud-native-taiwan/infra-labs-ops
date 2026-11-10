from __future__ import annotations

import logging
from datetime import date

from account_automation.config import AppConfig
from account_automation.logging_config import describe_exception
from account_automation.models import InvalidRow, ProcessingResult, RunOutcome, SheetRow
from account_automation.processors import registry
from account_automation.repositories.base import SheetRepository
from account_automation.services.email_service import EmailService
from account_automation.services.openstack_service import OpenStackService
from account_automation import validators


LOGGER = logging.getLogger(__name__)


def run(
    config: AppConfig,
    repo: SheetRepository,
    openstack: OpenStackService,
    email: EmailService,
    today: date | None = None,
) -> RunOutcome:
    current_day = today or date.today()
    rows = repo.read_all_rows()
    results: list[ProcessingResult] = []
    invalid: list[InvalidRow] = []

    for row in rows:
        # Status gating comes first: bad data only matters on a row the tool
        # would act on. Finished rows (DELETED, EXPIRED) and rows without a
        # status are never acted on, so junk in them is not reported.
        if row.status is None:
            LOGGER.debug(
                "Skipping row without status row_number=%s username=%s",
                row.row_number,
                row.username,
            )
            continue

        processor = registry.get_processor(row.status)
        if processor is None:
            LOGGER.debug(
                "Skipping row without processor row_number=%s username=%s status=%s",
                row.row_number,
                row.username,
                row.status,
            )
            continue

        if isinstance(row, InvalidRow):
            invalid.append(_skip_invalid(row))
            continue
        is_valid, message = validators.validate_row(row)
        if not is_valid:
            invalid.append(
                _skip_invalid(InvalidRow(row.row_number, row.username, row.status, message))
            )
            continue

        try:
            result = processor(row, current_day, config, openstack, email)
        except Exception as exc:
            LOGGER.error(
                "Row failed row_number=%s username=%s status=%s error=%s",
                row.row_number,
                row.username,
                row.status,
                describe_exception(exc),
            )
            result = ProcessingResult.failure(row, str(exc))
        else:
            if result.success and result.update is not None and not config.dry_run:
                try:
                    repo.write_row_update(result.update)
                except Exception as exc:
                    # Isolate the write so one bad row does not abort the pass.
                    # The processor's OpenStack mutation already happened, but
                    # those ops are idempotent, so recording a failure here lets
                    # the next pass retry the write without corrupting state.
                    LOGGER.error(
                        "Failed to write sheet update for row_number=%s username=%s error=%s",
                        row.row_number,
                        row.username,
                        describe_exception(exc),
                    )
                    result = ProcessingResult.failure(
                        row, f"Sheet write failed: {exc}"
                    )

        results.append(result)
        _log_result(row, result, action=_determine_action(result, config))

    return RunOutcome(results=tuple(results), invalid=tuple(invalid))


def _skip_invalid(row: InvalidRow) -> InvalidRow:
    LOGGER.warning(
        "Skipping invalid row row_number=%s username=%s status=%s reason=%s",
        row.row_number,
        row.username,
        row.status,
        row.reason,
    )
    return row


def _determine_action(result: ProcessingResult, config: AppConfig) -> str:
    if not result.success:
        return "failed"
    if result.update is None:
        return "none"
    if config.dry_run:
        return "dry-run"
    return "sheet-update"


def _log_result(row: SheetRow, result: ProcessingResult, action: str) -> None:
    LOGGER.info(
        "Processed row row_number=%s username=%s status=%s action=%s success=%s",
        row.row_number,
        row.username,
        row.status,
        action,
        result.success,
    )
