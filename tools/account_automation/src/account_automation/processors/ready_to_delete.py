import logging
from datetime import date

from account_automation.config import AppConfig
from account_automation.models import ProcessingResult, RowUpdate, SheetRow, Status
from account_automation.sanitize import sanitize_exception_message
from account_automation.services.email_service import EmailService
from account_automation.services.openstack_service import OpenStackService


LOGGER = logging.getLogger(__name__)

# Minimum age of the delete-preview notification before an irreversible purge is
# allowed. The preview (sent in the PENDING_DELETE stage) is the window in which
# an operator can catch a mistaken ready_to_delete -- a typo or a wrong-row paste
# -- before the next 02:00 cron acts on it. Enforcing this age turns the
# two-step convention into an actual gate.
#
# Measured in calendar days because DeletePreviewSentAt has date (not timestamp)
# granularity: we only know the day the preview was sent, not the time. Given the
# 02:00 cron, a 3-day floor could fire only ~50 real hours after a preview dated
# late on day D (purged at 02:00 on D+3). A 4-day floor guarantees more than 72
# real hours even in that worst case, so the intended 72-hour minimum genuinely
# holds.
MIN_PREVIEW_AGE_DAYS = 4


def process(
    row: SheetRow,
    today: date,
    config: AppConfig,
    openstack: OpenStackService,
    email: EmailService,
) -> ProcessingResult:
    del config
    del email

    if row.status != Status.READY_TO_DELETE:
        return ProcessingResult.skip(row)

    preview_sent_at = row.delete_preview_sent_at
    if preview_sent_at is None:
        LOGGER.warning(
            "Refusing deletion for username=%s: no delete preview on record "
            "(DeletePreviewSentAt is empty). Set status to pending_delete to "
            "send a preview before deleting; leaving row untouched.",
            row.username,
        )
        return ProcessingResult.skip(row)

    preview_age_days = (today - preview_sent_at).days
    if preview_age_days < MIN_PREVIEW_AGE_DAYS:
        LOGGER.warning(
            "Refusing deletion for username=%s: delete preview is %s day(s) old "
            "(minimum %s). Leaving row untouched until the preview has aged.",
            row.username,
            preview_age_days,
            MIN_PREVIEW_AGE_DAYS,
        )
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
