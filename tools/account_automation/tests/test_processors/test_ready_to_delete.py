from datetime import date
from unittest.mock import MagicMock

from account_automation.models import ProcessingResult, RowUpdate, Status
from account_automation.processors import ready_to_delete


def test_process_ready_to_delete_happy_path(make_row, make_config) -> None:
    row = make_row(status=Status.READY_TO_DELETE)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = ready_to_delete.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, status=Status.DELETED),
        success=True,
        message="",
    )
    openstack.delete_user_and_project.assert_called_once_with(row.username)


def test_process_ready_to_delete_returns_failure_on_openstack_error(
    make_row, make_config
) -> None:
    row = make_row(status=Status.READY_TO_DELETE)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.delete_user_and_project.side_effect = RuntimeError("delete failed")

    result = ready_to_delete.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="delete failed",
    )


def test_process_ready_to_delete_skips_wrong_status(make_row, make_config) -> None:
    row = make_row(status=Status.PENDING_DELETE)
    config = make_config()
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = ready_to_delete.process(row, today, config, openstack, email)

    assert result == ProcessingResult.skip(row)
    openstack.delete_user_and_project.assert_not_called()
