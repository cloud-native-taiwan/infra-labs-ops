from datetime import date
from unittest.mock import MagicMock

from account_automation.models import ProcessingResult, RowUpdate, Status
from account_automation.processors import expiring


def test_process_expiring_marks_expired_after_grace_period(make_row, make_config) -> None:
    row = make_row(
        status=Status.EXPIRING,
        expiry_date=date(2026, 3, 18),
        expiry_email_sent_at=date(2026, 3, 24),
    )
    config = make_config(grace_period_days=7)
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = expiring.process(row, today, config, openstack, email)

    openstack.set_user_enabled.assert_called_once_with(row.username, False)
    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, status=Status.EXPIRED),
        success=True,
        message="",
    )


def test_process_expiring_within_grace_period_is_no_op(make_row, make_config) -> None:
    row = make_row(
        status=Status.EXPIRING,
        expiry_date=date(2026, 3, 19),
        expiry_email_sent_at=date(2026, 3, 20),
    )
    config = make_config(grace_period_days=7)
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = expiring.process(row, today, config, openstack, email)

    openstack.set_user_enabled.assert_not_called()
    assert result == ProcessingResult(row=row, update=None, success=True, message="")


def test_process_expiring_fails_without_warning_timestamp(make_row, make_config) -> None:
    row = make_row(
        status=Status.EXPIRING,
        expiry_email_sent_at=None,
    )
    config = make_config(grace_period_days=7)
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = expiring.process(row, today, config, openstack, email)

    openstack.set_user_enabled.assert_not_called()
    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="Missing expiry_email_sent_at for expiring row",
    )


def test_process_expiring_fails_without_expiry_date(make_row, make_config) -> None:
    row = make_row(
        status=Status.EXPIRING,
        expiry_date=None,
        expiry_email_sent_at=date(2026, 3, 18),
    )
    config = make_config(grace_period_days=7)
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = expiring.process(row, today, config, openstack, email)

    openstack.set_user_enabled.assert_not_called()
    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="Missing expiry_date for expiring row",
    )
