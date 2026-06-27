from datetime import date, timedelta
from unittest.mock import MagicMock

from dateutil.relativedelta import relativedelta

from account_automation.models import ProcessingResult, RowUpdate, Status
from account_automation.processors import renewal


def test_renewal_expired_account_recomputes_and_reenables(make_row, make_config) -> None:
    today = date(2026, 6, 27)
    row = make_row(
        status=Status.RENEWAL,
        duration_raw="三個月",
        expiry_date=date(2026, 5, 1),  # already past
        expiry_email_sent_at=date(2026, 4, 17),
    )
    openstack = MagicMock()

    result = renewal.process(row, today, make_config(), openstack, MagicMock())

    openstack.set_user_enabled.assert_called_once_with(row.username, True)
    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(
            row_number=row.row_number,
            status=Status.ACTIVE,
            expiry_date=today + relativedelta(months=3),
            clear_expiry_email_sent_at=True,
        ),
        success=True,
        message="",
    )


def test_renewal_before_expiry_uses_recompute_not_stale_date(make_row, make_config) -> None:
    today = date(2026, 6, 27)
    row = make_row(
        status=Status.RENEWAL,
        duration_raw="一個月",
        expiry_date=date(2026, 7, 2),  # still future but sooner than today + 1 month
    )
    openstack = MagicMock()

    result = renewal.process(row, today, make_config(), openstack, MagicMock())

    assert result.update is not None
    assert result.update.expiry_date == today + relativedelta(months=1)


def test_renewal_honors_later_admin_date(make_row, make_config) -> None:
    today = date(2026, 6, 27)
    later = date(2026, 12, 1)  # well beyond today + 1 month
    row = make_row(status=Status.RENEWAL, duration_raw="一個月", expiry_date=later)
    openstack = MagicMock()

    result = renewal.process(row, today, make_config(), openstack, MagicMock())

    assert result.update is not None
    assert result.update.expiry_date == later


def test_renewal_handles_missing_expiry_date(make_row, make_config) -> None:
    today = date(2026, 6, 27)
    row = make_row(status=Status.RENEWAL, duration_raw="兩週", expiry_date=None)
    openstack = MagicMock()

    result = renewal.process(row, today, make_config(), openstack, MagicMock())

    assert result.success
    assert result.update is not None
    assert result.update.expiry_date == today + timedelta(days=14)


def test_renewal_unknown_duration_fails(make_row, make_config) -> None:
    today = date(2026, 6, 27)
    row = make_row(status=Status.RENEWAL, duration_raw="十年", expiry_date=date(2026, 5, 1))
    openstack = MagicMock()

    result = renewal.process(row, today, make_config(), openstack, MagicMock())

    assert result.success is False
    assert "Unknown duration" in result.message
    assert result.update is None
    openstack.set_user_enabled.assert_not_called()


def test_renewal_missing_user_fails(make_row, make_config) -> None:
    today = date(2026, 6, 27)
    row = make_row(status=Status.RENEWAL, duration_raw="一個月", expiry_date=date(2026, 5, 1))
    openstack = MagicMock()
    openstack.user_exists.return_value = False

    result = renewal.process(row, today, make_config(), openstack, MagicMock())

    assert result.success is False
    assert result.update is None
    assert "no longer exists" in result.message
    openstack.set_user_enabled.assert_not_called()


def test_renewal_skips_non_renewal_status(make_row, make_config) -> None:
    today = date(2026, 6, 27)
    row = make_row(status=Status.ACTIVE)
    openstack = MagicMock()

    result = renewal.process(row, today, make_config(), openstack, MagicMock())

    assert result == ProcessingResult.skip(row)
    openstack.set_user_enabled.assert_not_called()


def test_renewal_is_dry_run_agnostic_at_processor_level(make_row, make_config) -> None:
    # The processor does not branch on dry_run; the service honors it (skips the
    # enable call) and the orchestrator skips the write. The processor itself
    # always returns the ACTIVE update and issues the enable call.
    today = date(2026, 6, 27)
    row = make_row(status=Status.RENEWAL, duration_raw="一個月", expiry_date=date(2026, 5, 1))
    openstack = MagicMock()

    result = renewal.process(row, today, make_config(dry_run=True), openstack, MagicMock())

    openstack.set_user_enabled.assert_called_once_with(row.username, True)
    assert result.update is not None
    assert result.update.status is Status.ACTIVE
