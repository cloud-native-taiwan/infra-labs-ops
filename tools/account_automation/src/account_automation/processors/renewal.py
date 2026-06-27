from datetime import date

from account_automation.config import AppConfig
from account_automation.duration import parse_duration
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
    del config
    del email

    if row.status != Status.RENEWAL:
        return ProcessingResult.skip(row)

    try:
        recomputed = parse_duration(row.duration_raw)(today)
    except ValueError as exc:
        return ProcessingResult.failure(row, str(exc))

    # Renewal can only restore an account whose user still exists. If it was
    # already deleted (the row reached DELETED, or APPROVED never completed),
    # fail loudly rather than silently flipping the sheet to ACTIVE for a ghost.
    if not openstack.user_exists(row.username):
        return ProcessingResult.failure(
            row, f"Cannot renew: OpenStack user {row.username} no longer exists"
        )

    # Extend-only: the later of the recomputed term and any date the admin typed
    # into ExpiryDate wins. A past/near stored date loses to the recompute; a
    # deliberately-later admin date is honored. To shorten, edit the duration.
    new_expiry = max(recomputed, row.expiry_date or date.min)

    openstack.set_user_enabled(row.username, True)

    return ProcessingResult(
        row=row,
        update=RowUpdate(
            row_number=row.row_number,
            status=Status.ACTIVE,
            expiry_date=new_expiry,
            clear_expiry_email_sent_at=True,
        ),
        success=True,
        message="",
    )
