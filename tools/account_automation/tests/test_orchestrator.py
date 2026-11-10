from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock

import pytest

from account_automation.models import (
    InvalidRow,
    ProcessingResult,
    RowUpdate,
    SheetRow,
    Status,
)
from account_automation.orchestrator import run


class FakeRepo:
    def __init__(self, rows: tuple[SheetRow | InvalidRow, ...]) -> None:
        self._rows = rows
        self.writes: list[RowUpdate] = []

    def read_all_rows(self) -> tuple[SheetRow | InvalidRow, ...]:
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

    outcome = run(config, repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    get_processor.assert_called_once_with(Status.APPROVED)
    processor.assert_called_once()
    assert outcome.results == (expected_result,)
    assert outcome.invalid == ()
    assert repo.writes == [expected_result.update]


def test_run_skips_and_reports_invalid_actionable_row(make_row, make_config, mocker) -> None:
    bad_row = make_row(row_number=2, username="bad username", status=Status.APPROVED)
    good_row = make_row(row_number=3, username="good_user", status=Status.ACTIVE)
    good_result = ProcessingResult(row=good_row, update=None, success=True, message="")
    processor = MagicMock(return_value=good_result)
    mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        return_value=processor,
    )
    repo = FakeRepo((bad_row, good_row))

    outcome = run(make_config(), repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    # The invalid row never reaches a processor; the valid row still runs.
    processor.assert_called_once()
    assert processor.call_args.args[0] is good_row
    assert outcome.results == (good_result,)
    assert outcome.invalid == (
        InvalidRow(
            row_number=2,
            username="bad username",
            status=Status.APPROVED,
            reason="Invalid username",
        ),
    )
    assert repo.writes == []


@pytest.mark.parametrize("status", [None, Status.DELETED, Status.EXPIRED])
def test_run_ignores_invalid_data_on_rows_it_would_not_act_on(
    make_row, make_config, status: Status | None
) -> None:
    # Junk in a row with no status or no processor (finished/archived rows)
    # must not count against every future run.
    row = make_row(username="bad username", duration_raw="forever", status=status)

    outcome = run(make_config(), FakeRepo((row,)), MagicMock(), MagicMock())

    assert outcome.results == ()
    assert outcome.invalid == ()


def test_run_reports_unparseable_actionable_row_with_row_number(
    make_config, mocker, production_logging
) -> None:
    unparseable = InvalidRow(
        row_number=9,
        username="carol",
        status=Status.ACTIVE,
        reason="ValueError: Non-numeric value '8 cores' in column vCPU 數量",
    )
    processor = MagicMock()
    mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        return_value=processor,
    )

    outcome = run(make_config(), FakeRepo((unparseable,)), MagicMock(), MagicMock())

    processor.assert_not_called()
    assert outcome.invalid == (unparseable,)
    err = production_logging.getvalue()
    assert "[WARNING]" in err
    assert "row_number=9" in err
    assert "Non-numeric value '8 cores'" in err


def test_run_ignores_unparseable_row_without_actionable_status(make_config) -> None:
    unparseable = InvalidRow(row_number=9, username="carol", status=None, reason="bad date")

    outcome = run(make_config(), FakeRepo((unparseable,)), MagicMock(), MagicMock())

    assert outcome.invalid == ()


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

    outcome = run(make_config(), repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    assert outcome.results == (
        ProcessingResult(
            row=approved_row,
            update=None,
            success=False,
            message="processor exploded",
        ),
        successful_result,
    )
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

    outcome = run(make_config(), repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    assert outcome.results[0].success is False
    assert "Sheet write failed" in outcome.results[0].message
    assert outcome.results[1] == ok_result
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

    outcome = run(make_config(), repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    get_processor.assert_called_once_with(Status.DELETED)
    assert outcome.results == ()
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

    outcome = run(config, repo, MagicMock(), MagicMock(), today=date(2026, 3, 25))

    processor.assert_called_once()
    assert outcome.results == (expected_result,)
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

    outcome = run(config, repo, openstack, email, today=date(2026, 3, 25))

    assert len(outcome.results) == 1
    assert outcome.results[0].success is True
    assert outcome.results[0].update is not None
    assert outcome.results[0].update.delete_preview_sent_at == date(2026, 3, 25)
    assert len(repo.writes) == 1
    assert repo.writes[0].delete_preview_sent_at == date(2026, 3, 25)


def test_run_logs_redacted_reason_when_processor_raises(
    make_row, make_config, mocker, production_logging
) -> None:
    row = make_row(row_number=7, username="alice", status=Status.APPROVED)

    def failing_processor(*args: object) -> ProcessingResult:
        raise RuntimeError("keystone refused password=hunter2secret")

    mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        return_value=failing_processor,
    )

    run(make_config(), FakeRepo((row,)), MagicMock(), MagicMock(), today=date(2026, 3, 25))

    err = production_logging.getvalue()
    assert "row_number=7" in err
    assert "RuntimeError: keystone refused password=[REDACTED]" in err
    assert "hunter2secret" not in err


def test_run_logs_redacted_reason_without_traceback_when_write_fails(
    make_row, make_config, mocker, production_logging
) -> None:
    row = make_row(status=Status.RENEWAL)
    result = ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, status=Status.ACTIVE),
        success=True,
        message="",
    )

    class BrokenRepo(FakeRepo):
        def write_row_update(self, update: RowUpdate) -> None:
            raise RuntimeError("sheet said token=not-a-real-token")

    mocker.patch(
        "account_automation.orchestrator.registry.get_processor",
        return_value=MagicMock(return_value=result),
    )

    run(make_config(), BrokenRepo((row,)), MagicMock(), MagicMock(), today=date(2026, 3, 25))

    err = production_logging.getvalue()
    assert "RuntimeError: sheet said token=[REDACTED]" in err
    assert "not-a-real-token" not in err
    # One line per row failure, not a traceback.
    assert "Traceback" not in err
