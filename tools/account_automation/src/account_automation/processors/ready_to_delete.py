import logging
from datetime import date

from account_automation.config import AppConfig
from account_automation.models import ProcessingResult, RowUpdate, SheetRow, Status
from account_automation.sanitize import sanitize_exception_message
from account_automation.services.email_service import EmailService
from account_automation.services.openstack_service import OpenStackService


LOGGER = logging.getLogger(__name__)


def process(
    row: SheetRow,
    today: date,
    config: AppConfig,
    openstack: OpenStackService,
    email: EmailService,
) -> ProcessingResult:
    del today
    del config
    del email

    if row.status != Status.READY_TO_DELETE:
        return ProcessingResult.skip(row)

    try:
        openstack.log_project_resources(row.username)
    except Exception:
        LOGGER.warning(
            "Failed to log pre-deletion resources for username=%s",
            row.username,
            exc_info=True,
        )

    try:
        openstack.delete_user_and_project(row.username)
    except Exception as exc:
        return ProcessingResult.failure(row, sanitize_exception_message(str(exc)))

    return ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, status=Status.DELETED),
        success=True,
        message="",
    )
