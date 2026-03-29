from __future__ import annotations

import logging
from datetime import date

from account_automation.config import AppConfig
from account_automation.models import ProcessingResult, SheetRow
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
) -> list[ProcessingResult]:
    current_day = today or date.today()
    rows = repo.read_all_rows()
    results: list[ProcessingResult] = []

    for row in rows:
        is_valid, message = validators.validate_row(row)
        if not is_valid:
            LOGGER.warning(
                "Skipping invalid row row_number=%s username=%s reason=%s",
                row.row_number,
                row.username,
                message,
            )
            result = ProcessingResult.failure(row, message)
            results.append(result)
            _log_result(row, result, action="invalid")
            continue

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

        try:
            result = processor(row, current_day, config, openstack, email)
        except Exception as exc:
            result = ProcessingResult.failure(row, str(exc))
        else:
            if result.success and result.update is not None and not config.dry_run:
                repo.write_row_update(result.update)

        results.append(result)
        _log_result(row, result, action=_determine_action(result, config))

    return results


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
