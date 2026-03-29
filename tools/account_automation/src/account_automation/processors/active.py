from datetime import date, timedelta

from account_automation.config import AppConfig
from account_automation.models import ProcessingResult, RowUpdate, SheetRow, Status
from account_automation.services.email_service import EmailService
from account_automation.services.openstack_service import OpenStackService


def process(
    row: SheetRow,
    today: date,
    config: AppConfig,
    openstack: OpenStackService,
    email: EmailService,
) -> ProcessingResult:
    del openstack

    if row.status != Status.ACTIVE:
        return ProcessingResult.skip(row)

    if row.expiry_date is None:
        return ProcessingResult.failure(row, "Missing expiry_date for active row")

    if row.expiry_email_sent_at is not None:
        return ProcessingResult(
            row=row,
            update=RowUpdate(row_number=row.row_number, status=Status.EXPIRING),
            success=True,
            message="",
        )

    warning_date = today + timedelta(days=config.expiry_warning_days)
    if row.expiry_date <= warning_date:
        email.send_expiry_warning(row, row.expiry_date)
        return ProcessingResult(
            row=row,
            update=RowUpdate(
                row_number=row.row_number,
                status=Status.EXPIRING,
                expiry_email_sent_at=today,
            ),
            success=True,
            message="",
        )

    return ProcessingResult.skip(row)
