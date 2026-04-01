from datetime import date
from unittest.mock import MagicMock

from account_automation.models import DeletePreview, ProcessingResult, ResourceItem, RowUpdate, Status
from account_automation.processors import pending_delete


def test_process_pending_delete_sends_preview_email_and_marks_sent(
    make_row, make_config
) -> None:
    row = make_row(status=Status.PENDING_DELETE)
    config = make_config(admin_email="admin@example.com")
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    preview = DeletePreview(
        username=row.username,
        user_found=True,
        project_found=True,
        servers=(
            ResourceItem(id="s1", name="web", extra="ACTIVE"),
            ResourceItem(id="s2", name="db", extra="ACTIVE"),
        ),
        volumes=(ResourceItem(id="v1", name="data", extra="in-use, 50GB"),),
    )
    openstack.preview_delete.return_value = preview

    result = pending_delete.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=RowUpdate(row_number=row.row_number, delete_preview_sent_at=today),
        success=True,
        message="",
    )
    openstack.preview_delete.assert_called_once_with(row.username)
    email.send_delete_preview_email.assert_called_once_with(
        row, preview, config.admin_email
    )


def test_process_pending_delete_skips_when_preview_already_sent(make_row, make_config) -> None:
    row = make_row(
        status=Status.PENDING_DELETE,
        delete_preview_sent_at=date(2026, 3, 24),
    )
    config = make_config(admin_email="admin@example.com")
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()

    result = pending_delete.process(row, today, config, openstack, email)

    assert result == ProcessingResult.skip(row)
    openstack.preview_delete.assert_not_called()
    email.send_delete_preview_email.assert_not_called()


def test_process_pending_delete_skips_when_admin_email_missing(
    make_row, make_config, caplog
) -> None:
    row = make_row(status=Status.PENDING_DELETE)
    config = make_config(admin_email="")
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.preview_delete.return_value = DeletePreview(
        username=row.username,
        user_found=False,
        project_found=False,
    )

    result = pending_delete.process(row, today, config, openstack, email)

    assert result == ProcessingResult.skip(row)
    assert "No admin_email configured; skipping deletion preview email" in caplog.text
    openstack.preview_delete.assert_called_once_with(row.username)
    email.send_delete_preview_email.assert_not_called()


def test_process_pending_delete_returns_failure_on_preview_error(
    make_row, make_config
) -> None:
    row = make_row(status=Status.PENDING_DELETE)
    config = make_config(admin_email="admin@example.com")
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    openstack.preview_delete.side_effect = RuntimeError("preview failed")

    result = pending_delete.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="preview failed",
    )
    email.send_delete_preview_email.assert_not_called()


def test_process_pending_delete_returns_failure_on_email_error(
    make_row, make_config
) -> None:
    row = make_row(status=Status.PENDING_DELETE)
    config = make_config(admin_email="admin@example.com")
    today = date(2026, 3, 25)
    openstack = MagicMock()
    email = MagicMock()
    preview = DeletePreview(
        username=row.username,
        user_found=True,
        project_found=True,
    )
    openstack.preview_delete.return_value = preview
    email.send_delete_preview_email.side_effect = RuntimeError("email failed")

    result = pending_delete.process(row, today, config, openstack, email)

    assert result == ProcessingResult(
        row=row,
        update=None,
        success=False,
        message="email failed",
    )
