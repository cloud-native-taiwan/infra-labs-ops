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
    del email

    if row.status != Status.EXPIRING:
        return ProcessingResult.skip(row)

    if row.expiry_email_sent_at is None:
        return ProcessingResult.failure(
            row, "Missing expiry_email_sent_at for expiring row"
        )

    if row.expiry_date is None:
        return ProcessingResult.failure(row, "Missing expiry_date for expiring row")

    grace_end = row.expiry_date + timedelta(days=config.grace_period_days)
    if grace_end <= today:
        # Disable before flipping to EXPIRED. A real API error propagates, so the
        # orchestrator records a failure and EXPIRED is not written (next pass
        # retries). A missing user returns without error — already gone, fine to
        # expire.
        openstack.set_user_enabled(row.username, False)
        return ProcessingResult(
            row=row,
            update=RowUpdate(row_number=row.row_number, status=Status.EXPIRED),
            success=True,
            message="",
        )

    return ProcessingResult.skip(row)
