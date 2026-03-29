from datetime import date

from account_automation.config import AppConfig
from account_automation.models import ProcessingResult, RowUpdate, SheetRow, Status
from account_automation.sanitize import sanitize_exception_message
from account_automation.services.email_service import EmailService
from account_automation.services.openstack_service import OpenStackService


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

    if row.status != Status.PENDING_DELETE:
        return ProcessingResult.skip(row)

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
