from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from account_automation.models import ProcessingResult, RowUpdate, Status
from account_automation.processors import ready_to_delete


TODAY = date(2026, 3, 25)
# 5 days before TODAY: safely past the MIN_PREVIEW_AGE_DAYS gate.
OLD_ENOUGH_PREVIEW = date(2026, 3, 20)


def test_process_ready_to_delete_happy_path(make_row, make_config) -> None:
    row = make_row(
        status=Status.READY_TO_DELETE, delete_preview_sent_at=OLD_ENOUGH_PREVIEW
    )
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()

    result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, status=Status.DELETED),
        success=True,
        message="",
    )
    openstack.log_project_resources.assert_called_once_with(row.username)
    openstack.delete_user_and_project.assert_called_once_with(row.username)


def test_process_ready_to_delete_returns_failure_on_openstack_error(
    make_row, make_config
) -> None:
    row = make_row(
        status=Status.READY_TO_DELETE, delete_preview_sent_at=OLD_ENOUGH_PREVIEW
    )
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()
    openstack.delete_user_and_project.side_effect = RuntimeError("delete failed")

    result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="delete failed",
    )


def test_process_ready_to_delete_skips_wrong_status(make_row, make_config) -> None:
    row = make_row(status=Status.PENDING_DELETE)
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()

    result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result == ProcessingResult.skip(row)
    openstack.log_project_resources.assert_not_called()
    openstack.delete_user_and_project.assert_not_called()


def test_process_ready_to_delete_continues_if_logging_fails(
    make_row, make_config
) -> None:
    row = make_row(
        status=Status.READY_TO_DELETE, delete_preview_sent_at=OLD_ENOUGH_PREVIEW
    )
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()
    openstack.log_project_resources.side_effect = RuntimeError("logging failed")

    result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result.success is True
    openstack.delete_user_and_project.assert_called_once_with(row.username)


def test_process_ready_to_delete_refuses_when_no_preview_sent(
    make_row, make_config, caplog: pytest.LogCaptureFixture
) -> None:
    row = make_row(status=Status.READY_TO_DELETE, delete_preview_sent_at=None)
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()

    with caplog.at_level("WARNING"):
        result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result == ProcessingResult.skip(row)
    openstack.delete_user_and_project.assert_not_called()
    assert "no delete preview on record" in caplog.text


def test_process_ready_to_delete_refuses_when_preview_too_fresh(
    make_row, make_config, caplog: pytest.LogCaptureFixture
) -> None:
    # Preview sent yesterday: 1 day < MIN_PREVIEW_AGE_DAYS.
    row = make_row(
        status=Status.READY_TO_DELETE, delete_preview_sent_at=date(2026, 3, 24)
    )
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()

    with caplog.at_level("WARNING"):
        result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result == ProcessingResult.skip(row)
    openstack.delete_user_and_project.assert_not_called()
    assert "delete preview is 1 day(s) old" in caplog.text


def test_process_ready_to_delete_refuses_one_day_below_min_age(
    make_row, make_config
) -> None:
    # One day below the floor must still be refused: pins the boundary to the
    # constant so a wrong value (e.g. an off-by-one 3) would fail this test.
    preview_sent = TODAY - timedelta(days=ready_to_delete.MIN_PREVIEW_AGE_DAYS - 1)
    row = make_row(status=Status.READY_TO_DELETE, delete_preview_sent_at=preview_sent)
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()

    result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result == ProcessingResult.skip(row)
    openstack.delete_user_and_project.assert_not_called()


def test_process_ready_to_delete_refuses_future_dated_preview(
    make_row, make_config, caplog: pytest.LogCaptureFixture
) -> None:
    # A preview dated in the future yields a negative age (< min): a clock skew
    # or a bad paste must never satisfy the gate.
    row = make_row(
        status=Status.READY_TO_DELETE,
        delete_preview_sent_at=TODAY + timedelta(days=2),
    )
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()

    with caplog.at_level("WARNING"):
        result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result == ProcessingResult.skip(row)
    openstack.delete_user_and_project.assert_not_called()
    assert "delete preview is -2 day(s) old" in caplog.text


def test_process_ready_to_delete_proceeds_when_preview_exactly_min_age(
    make_row, make_config
) -> None:
    # Exactly MIN_PREVIEW_AGE_DAYS old: the gate allows deletion.
    preview_sent = TODAY - timedelta(days=ready_to_delete.MIN_PREVIEW_AGE_DAYS)
    row = make_row(status=Status.READY_TO_DELETE, delete_preview_sent_at=preview_sent)
    config = make_config()
    openstack = MagicMock()
    email = MagicMock()

    result = ready_to_delete.process(row, TODAY, config, openstack, email)

    assert result.success is True
    assert result.update == RowUpdate(row_number=row.row_number, status=Status.DELETED)
    openstack.delete_user_and_project.assert_called_once_with(row.username)
