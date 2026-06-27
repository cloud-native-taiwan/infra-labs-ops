from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock

from account_automation.models import ProcessingResult, RowUpdate, SheetRow, Status
from account_automation.orchestrator import run


class FakeRepo:
    def __init__(self, rows: tuple[SheetRow, ...]) -> None:
        self._rows = rows
        self.writes: list[RowUpdate] = []

    def read_all_rows(self) -> tuple[SheetRow, ...]:
        return self._rows

    def write_row_update(self, update: RowUpdate) -> None:
        self.writes.append(update)


def test_run_happy_path_writes_successful_update(make_row, make_config, mocker) -> None:
    row = make_row(status=Status.APPROVED)
    config = make_config()
    expected_result = ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, status=Status.ACTIVE),
        success=True,
        message="",
    )
    processor = MagicMock(return_value=expected_result)
    repo = FakeRepo((row,))

    get_processor = mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        return_value=processor,
    )

    results = run(config, repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    get_processor.assert_called_once_with(Status.APPROVED)
    processor.assert_called_once()
    assert results == [expected_result]
    assert repo.writes == [expected_result.update]


def test_run_returns_failure_for_invalid_row(make_row, make_config, mocker) -> None:
    row = make_row(username="bad username")
    repo = FakeRepo((row,))
    get_processor = mocker.patch("account_automation.orchestrator.registry.get_processor")

    results = run(make_config(), repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    get_processor.assert_not_called()
    assert results == [
        ProcessingResult(
            row=row,
            update=None,
            success=False,
            message="Invalid username",
        )
    ]
    assert repo.writes == []


def test_run_continues_when_processor_raises(make_row, make_config, mocker) -> None:
    approved_row = make_row(row_number=2, username="approved_user", status=Status.APPROVED)
    active_row = make_row(row_number=3, username="active_user", status=Status.ACTIVE)
    repo = FakeRepo((approved_row, active_row))
    successful_result = ProcessingResult(
        row=active_row,
        update=RowUpdate(row_number=active_row.row_number, status=Status.EXPIRING),
        success=True,
        message="",
    )

    def failing_processor(*args: object) -> ProcessingResult:
        raise RuntimeError("processor exploded")

    successful_processor = MagicMock(return_value=successful_result)
    mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        side_effect=lambda status: (
            failing_processor if status == Status.APPROVED else successful_processor
        ),
    )

    results = run(make_config(), repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    assert results == [
        ProcessingResult(
            row=approved_row,
            update=None,
            success=False,
            message="processor exploded",
        ),
        successful_result,
    ]
    successful_processor.assert_called_once()
    assert repo.writes == [successful_result.update]


def test_run_continues_when_write_fails(make_row, make_config, mocker) -> None:
    failing_row = make_row(row_number=2, username="renew_user", status=Status.RENEWAL)
    ok_row = make_row(row_number=3, username="active_user", status=Status.ACTIVE)
    failing_result = ProcessingResult(
        row=failing_row,
        update=RowUpdate(row_number=failing_row.row_number, status=Status.ACTIVE),
        success=True,
        message="",
    )
    ok_result = ProcessingResult(
        row=ok_row,
        update=RowUpdate(row_number=ok_row.row_number, status=Status.EXPIRING),
        success=True,
        message="",
    )

    class FlakyRepo(FakeRepo):
        def write_row_update(self, update: RowUpdate) -> None:
            if update.row_number == failing_row.row_number:
                raise RuntimeError("sheet unavailable")
            super().write_row_update(update)

    repo = FlakyRepo((failing_row, ok_row))
    mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        side_effect=lambda status: MagicMock(
            return_value=failing_result if status == Status.RENEWAL else ok_result
        ),
    )

    results = run(make_config(), repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    assert results[0].success is False
    assert "Sheet write failed" in results[0].message
    assert results[1] == ok_result
    # The good row still wrote despite the earlier failure.
    assert repo.writes == [ok_result.update]


def test_run_skips_status_without_registered_processor(
    make_row, make_config, mocker
) -> None:
    row = make_row(status=Status.DELETED)
    repo = FakeRepo((row,))
    get_processor = mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        return_value=None,
    )

    results = run(make_config(), repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    get_processor.assert_called_once_with(Status.DELETED)
    assert results == []
    assert repo.writes == []


def test_run_does_not_write_updates_in_dry_run(make_row, make_config, mocker) -> None:
    row = make_row(status=Status.APPROVED)
    config = replace(make_config(), dry_run=True)
    expected_result = ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, status=Status.ACTIVE),
        success=True,
        message="",
    )
    processor = MagicMock(return_value=expected_result)
    repo = FakeRepo((row,))

    mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        return_value=processor,
    )

    results = run(config, repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    processor.assert_called_once()
    assert results == [expected_result]
    assert repo.writes == []


def test_run_pending_delete_sets_delete_preview_sent_at(
    make_row, make_config, mocker
) -> None:
    row = make_row(status=Status.PENDING_DELETE)
    config = make_config(admin_email="admin@example.com")
    repo = FakeRepo((row,))
    openstack = MagicMock()
    email = MagicMock()
    openstack.preview_delete.return_value = MagicMock()

    from account_automation.processors import pending_delete

    mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        return_value=pending_delete.process,
    )

    results = run(config, repo, openstack, email, today=date(2026, 3, 25))

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].update is not None
    assert results[0].update.delete_preview_sent_at == date(2026, 3, 25)
    assert len(repo.writes) == 1
    assert repo.writes[0].delete_preview_sent_at == date(2026, 3, 25)
