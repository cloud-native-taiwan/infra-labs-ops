from datetime import date
from unittest.mock import MagicMock

from account_automation.models import ProcessingResult, RowUpdate, Status
from account_automation.processors import active


def test_process_active_near_expiry_sends_warning_and_marks_expiring(
    make_row, make_config
) -> None:
    row = make_row(
        status=Status.ACTIVE,
        expiry_date=date(2026, 4, 1),
        expiry_email_sent_at=None,
    )
    config = make_config(expiry_warning_days=14)
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = active.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(
            row_number=row.row_number,
            status=Status.EXPIRING,
            expiry_email_sent_at=today,
        ),
        success=True,
        message="",
    )
    email.send_expiry_warning.assert_called_once_with(row, row.expiry_date)


def test_process_active_far_from_expiry_is_no_op(make_row, make_config) -> None:
    row = make_row(
        status=Status.ACTIVE,
        expiry_date=date(2026, 5, 15),
        expiry_email_sent_at=None,
    )
    config = make_config(expiry_warning_days=14)
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = active.process(row, today, config, openstack, email)

    assert result == ProcessingResult(row=row, update=None, success=True, message="")
    email.send_expiry_warning.assert_not_called()


def test_process_active_skips_resend_when_warning_email_already_sent(
    make_row, make_config
) -> None:
    row = make_row(
        status=Status.ACTIVE,
        expiry_date=date(2026, 4, 1),
        expiry_email_sent_at=date(2026, 3, 20),
    )
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = active.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, status=Status.EXPIRING),
        success=True,
        message="",
    )
    email.send_expiry_warning.assert_not_called()


def test_process_active_fails_without_expiry_date(make_row, make_config) -> None:
    row = make_row(
        status=Status.ACTIVE,
        expiry_date=None,
        expiry_email_sent_at=None,
    )
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = active.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="Missing expiry_date for active row",
    )
    email.send_expiry_warning.assert_not_called()
