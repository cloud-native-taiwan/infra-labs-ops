import secrets
from datetime import date

from account_automation.config import AppConfig
from account_automation.duration import parse_duration
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
    del config

    if row.status != Status.APPROVED:
        return ProcessingResult.skip(row)

    try:
        user_existed = openstack.user_exists(row.username)
        project_exists = openstack.project_exists(row.username)
        expiry_date = row.expiry_date or parse_duration(row.duration_raw)(today)

        if user_existed and project_exists:
            return ProcessingResult(
                row=row,
                update=RowUpdate(
                    row_number=row.row_number,
                    status=Status.ACTIVE,
                    expiry_date=expiry_date,
                ),
                success=True,
                message="",
            )

        password = secrets.token_urlsafe(16)
        openstack.create_user_and_project(row, password)
        if not user_existed:
            email.send_welcome_email(row, password, expiry_date)
    except Exception as exc:
        return ProcessingResult.failure(row, sanitize_exception_message(str(exc)))

    return ProcessingResult(
        row=row,
        update=RowUpdate(
            row_number=row.row_number,
            status=Status.ACTIVE,
            expiry_date=expiry_date,
        ),
        success=True,
        message="",
    )
