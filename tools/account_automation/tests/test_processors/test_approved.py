from datetime import date
from unittest.mock import MagicMock

from account_automation.models import ProcessingResult, RowUpdate, Status
from account_automation.processors import approved


def test_process_approved_happy_path(make_row, make_config, mocker) -> None:
    row = make_row(status=Status.APPROVED, expiry_date=None)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.user_exists.return_value = False
    openstack.project_exists.return_value = False
    token_mock = mocker.patch(
        "account_automation.processors.approved.secrets.token_urlsafe",
        return_value="generated-password",
    )

    result = approved.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(
            row_number=row.row_number,
            status=Status.ACTIVE,
            expiry_date=date(2026, 4, 25),
        ),
        success=True,
        message="",
    )
    openstack.user_exists.assert_called_once_with(row.username)
    openstack.project_exists.assert_called_once_with(row.username)
    token_mock.assert_called_once_with(16)
    openstack.create_user_and_project.assert_called_once_with(row, "generated-password")
    email.send_welcome_email.assert_called_once_with(row, "generated-password", date(2026, 4, 25))


def test_process_approved_is_idempotent_when_resources_already_exist(
    make_row, make_config, mocker
) -> None:
    row = make_row(status=Status.APPROVED)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.user_exists.return_value = True
    openstack.project_exists.return_value = True
    token_mock = mocker.patch(
        "account_automation.processors.approved.secrets.token_urlsafe",
        return_value="generated-password",
    )

    result = approved.process(row, today, config, openstack, email)

    assert result.success is True
    assert result.update == RowUpdate(
        row_number=row.row_number,
        status=Status.ACTIVE,
        expiry_date=date(2026, 4, 25),
    )
    openstack.user_exists.assert_called_once_with(row.username)
    openstack.project_exists.assert_called_once_with(row.username)
    token_mock.assert_not_called()
    openstack.create_user_and_project.assert_not_called()
    email.send_welcome_email.assert_not_called()


def test_process_approved_uses_computed_expiry_when_resources_exist_without_stored_expiry(
    make_row, make_config, mocker
) -> None:
    row = make_row(status=Status.APPROVED, expiry_date=None)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.user_exists.return_value = True
    openstack.project_exists.return_value = True
    token_mock = mocker.patch(
        "account_automation.processors.approved.secrets.token_urlsafe",
        return_value="generated-password",
    )

    result = approved.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(
            row_number=row.row_number,
            status=Status.ACTIVE,
            expiry_date=date(2026, 4, 25),
        ),
        success=True,
        message="",
    )
    openstack.user_exists.assert_called_once_with(row.username)
    openstack.project_exists.assert_called_once_with(row.username)
    token_mock.assert_not_called()
    openstack.create_user_and_project.assert_not_called()
    email.send_welcome_email.assert_not_called()


def test_process_approved_skips_email_when_user_already_exists(
    make_row, make_config, mocker
) -> None:
    row = make_row(status=Status.APPROVED, expiry_date=None)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.user_exists.return_value = True
    openstack.project_exists.return_value = False
    mocker.patch(
        "account_automation.processors.approved.secrets.token_urlsafe",
        return_value="generated-password",
    )

    result = approved.process(row, today, config, openstack, email)

    assert result.success is True
    assert result.update is not None
    assert result.update.status is Status.ACTIVE
    openstack.create_user_and_project.assert_called_once_with(row, "generated-password")
    email.send_welcome_email.assert_not_called()


def test_process_approved_returns_failure_when_openstack_create_fails(
    make_row, make_config, mocker
) -> None:
    row = make_row(status=Status.APPROVED)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.user_exists.return_value = False
    openstack.project_exists.return_value = False
    openstack.create_user_and_project.side_effect = RuntimeError("openstack unavailable")
    mocker.patch(
        "account_automation.processors.approved.secrets.token_urlsafe",
        return_value="generated-password",
    )

    result = approved.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="openstack unavailable",
    )
    email.send_welcome_email.assert_not_called()


def test_process_approved_returns_failure_when_email_send_fails(
    make_row, make_config, mocker
) -> None:
    row = make_row(status=Status.APPROVED)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.user_exists.return_value = False
    openstack.project_exists.return_value = False
    email.send_welcome_email.side_effect = RuntimeError("email unavailable")
    mocker.patch(
        "account_automation.processors.approved.secrets.token_urlsafe",
        return_value="generated-password",
    )

    result = approved.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="email unavailable",
    )
    openstack.create_user_and_project.assert_called_once_with(row, "generated-password")
