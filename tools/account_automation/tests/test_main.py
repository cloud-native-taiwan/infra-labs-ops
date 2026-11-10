from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import mock_open

import pytest

from account_automation import main as main_module
from account_automation.backfill import BackfillOutcome
from account_automation.models import DeletePreview, InvalidRow, ResourceItem, RunOutcome
from account_automation.services.keystone_accounts import UnmanagedAccountError


def test_parse_args_defaults_bare_invocation_to_run() -> None:
    args = main_module._parse_args([])

    assert args.func is main_module._handle_run
    assert args.dry_run is False


def test_parse_args_defaults_option_only_invocation_to_run() -> None:
    args = main_module._parse_args(["--dry-run"])

    assert args.func is main_module._handle_run
    assert args.dry_run is True


def test_main_calls_parsed_handler(mocker) -> None:
    handler = mocker.Mock(return_value=7)
    args = SimpleNamespace(func=handler)
    configure_logging = mocker.patch("account_automation.main.configure_logging")
    mocker.patch("account_automation.main._parse_args", return_value=args)

    result = main_module.main()

    assert result == 7
    configure_logging.assert_called_once_with("INFO")
    handler.assert_called_once_with(args)


def test_handle_run_uses_lock_and_requires_full_config(make_config, mocker) -> None:
    config = make_config(log_level="DEBUG")
    load_config = mocker.patch("account_automation.main.load_config", return_value=config)
    configure_logging = mocker.patch("account_automation.main.configure_logging")
    os_open = mocker.patch("account_automation.main.os.open", return_value=99)
    fdopen = mocker.patch("account_automation.main.os.fdopen", mock_open())
    flock = mocker.patch("account_automation.main.fcntl.flock")
    repo = mocker.patch("account_automation.main.GoogleSheetsRepository", autospec=True)
    openstack = mocker.patch("account_automation.main.OpenStackServiceImpl", autospec=True)
    email = mocker.patch("account_automation.main.ResendEmailService", autospec=True)
    orchestrator_run = mocker.patch(
        "account_automation.main.run",
        return_value=RunOutcome(
            results=(SimpleNamespace(success=True), SimpleNamespace(success=False)),
            invalid=(),
        ),
    )

    result = main_module._handle_run(SimpleNamespace(dry_run=False))

    assert result == 1
    load_config.assert_called_once_with(require_all=True)
    os_open.assert_called_once_with(
        main_module.LOCK_PATH,
        main_module.os.O_CREAT | main_module.os.O_RDWR | main_module.os.O_NOFOLLOW,
        0o600,
    )
    fdopen.assert_called_once_with(99, "r+", encoding="utf-8")
    flock.assert_called_once()
    configure_logging.assert_any_call("DEBUG")
    repo.assert_called_once_with(config)
    openstack.assert_called_once_with(config)
    email.assert_called_once_with(config)
    orchestrator_run.assert_called_once()


@pytest.fixture
def run_lock(mocker, tmp_path):
    """Point the run lock at a temp file; returns a spy on flock."""
    mocker.patch.object(main_module, "LOCK_PATH", str(tmp_path / "run.lock"))
    return mocker.patch("account_automation.main.fcntl.flock")


_INVALID = InvalidRow(row_number=4, username="bad user", status=None, reason="Invalid username")


@pytest.mark.parametrize(
    ("successes", "invalid", "dry_run_flag", "dry_run_env", "expected_exit", "summary"),
    [
        ((True,), (), False, False, 0, "succeeded=1 failed=0 invalid=0"),
        ((), (), False, False, 0, "succeeded=0 failed=0 invalid=0"),
        ((True,), (_INVALID,), False, False, 65, "succeeded=1 failed=0 invalid=1"),
        ((False,), (_INVALID,), False, False, 1, "succeeded=0 failed=1 invalid=1"),
        ((True,), (), True, False, 0, "succeeded=1 failed=0 invalid=0"),
        ((True,), (), False, True, 0, "succeeded=1 failed=0 invalid=0"),
    ],
)
def test_handle_run_exit_code_and_success_metric(
    make_config,
    mocker,
    run_lock,
    production_logging,
    tmp_path,
    successes: tuple[bool, ...],
    invalid: tuple[InvalidRow, ...],
    dry_run_flag: bool,
    dry_run_env: bool,
    expected_exit: int,
    summary: str,
) -> None:
    config = make_config(textfile_collector_dir=str(tmp_path), dry_run=dry_run_env)
    mocker.patch("account_automation.main.load_config", return_value=config)
    for name in ("GoogleSheetsRepository", "OpenStackServiceImpl", "ResendEmailService"):
        mocker.patch(f"account_automation.main.{name}", autospec=True)
    mocker.patch(
        "account_automation.main.run",
        return_value=RunOutcome(
            results=tuple(SimpleNamespace(success=ok) for ok in successes),
            invalid=invalid,
        ),
    )

    assert main_module._handle_run(SimpleNamespace(dry_run=dry_run_flag)) == expected_exit
    assert f"Processing summary: {summary}" in production_logging.getvalue()
    # The success metric is written only for a fully successful, real run.
    metric_written = expected_exit == 0 and not (dry_run_flag or dry_run_env)
    assert (tmp_path / "task_account_automation.prom").exists() is metric_written


def test_handle_run_keeps_exit_0_when_metric_write_fails(
    make_config, mocker, run_lock, production_logging, tmp_path
) -> None:
    config = make_config(textfile_collector_dir=str(tmp_path))
    mocker.patch("account_automation.main.load_config", return_value=config)
    for name in ("GoogleSheetsRepository", "OpenStackServiceImpl", "ResendEmailService"):
        mocker.patch(f"account_automation.main.{name}", autospec=True)
    mocker.patch("account_automation.main.run", return_value=RunOutcome(results=(), invalid=()))
    mocker.patch(
        "account_automation.main.write_success_metric",
        side_effect=PermissionError("Permission denied"),
    )

    # The pass itself succeeded; a missing metric surfaces as a stale-metric alert.
    assert main_module._handle_run(SimpleNamespace(dry_run=False)) == 0
    assert "Could not write success metric: PermissionError" in production_logging.getvalue()


def test_handle_run_skips_with_tempfail_when_lock_is_held(
    mocker, run_lock, production_logging
) -> None:
    run_lock.side_effect = BlockingIOError
    load_config = mocker.patch("account_automation.main.load_config")
    write_metric = mocker.patch("account_automation.main.write_success_metric")

    result = main_module._handle_run(SimpleNamespace(dry_run=False))

    assert result == main_module.EXIT_LOCK_HELD == 75
    load_config.assert_not_called()
    write_metric.assert_not_called()
    assert "[WARNING]" in production_logging.getvalue()
    assert "skipping this run" in production_logging.getvalue()


def test_handle_run_logs_redacted_fatal_error_and_returns_1(
    mocker, run_lock, production_logging
) -> None:
    mocker.patch(
        "account_automation.main.load_config",
        side_effect=ValueError("sheet open failed token=not-a-real-token"),
    )

    result = main_module._handle_run(SimpleNamespace(dry_run=False))

    assert result == 1
    err = production_logging.getvalue()
    assert "Run aborted: ValueError: sheet open failed token=[REDACTED]" in err
    assert "not-a-real-token" not in err


def test_handle_delete_requires_force_when_stdin_is_not_tty(
    make_config,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_config()
    load_config = mocker.patch("account_automation.main.load_config", return_value=config)
    mocker.patch("account_automation.main.configure_logging")
    openstack = mocker.Mock()
    mocker.patch("account_automation.main.OpenStackServiceImpl", return_value=openstack)
    mocker.patch("builtins.open", side_effect=AssertionError("lock should not be used"))
    monkeypatch.setattr(main_module.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    result = main_module._handle_delete(
        SimpleNamespace(username="alice", dry_run=False, force=False)
    )

    assert result == 1
    load_config.assert_called_once_with(require_all=False)
    openstack.preview_delete.assert_not_called()
    openstack.delete_user_and_project.assert_not_called()
    assert "Refusing deletion without --force" in capsys.readouterr().err


def test_handle_delete_previews_and_confirms_before_deleting(
    make_config,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_config()
    preview = DeletePreview(
        username="alice",
        user_found=True,
        project_found=True,
        servers=(
            ResourceItem(id="s1", name="web", extra="ACTIVE"),
            ResourceItem(id="s2", name="db", extra="ACTIVE"),
        ),
        volumes=(ResourceItem(id="v1", name="data", extra="in-use"),),
    )
    mocker.patch("account_automation.main.load_config", return_value=config)
    mocker.patch("account_automation.main.configure_logging")
    openstack = mocker.Mock()
    openstack.preview_delete.return_value = preview
    mocker.patch("account_automation.main.OpenStackServiceImpl", return_value=openstack)
    mocker.patch("builtins.open", side_effect=AssertionError("lock should not be used"))
    mocker.patch("builtins.input", return_value="y")
    monkeypatch.setattr(main_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    result = main_module._handle_delete(
        SimpleNamespace(username="alice", dry_run=False, force=False)
    )

    assert result == 0
    openstack.preview_delete.assert_called_once_with("alice")
    openstack.delete_user_and_project.assert_called_once_with("alice")
    output = capsys.readouterr().out
    assert "Username: alice" in output
    assert "Servers: 2" in output
    assert "Volumes: 1" in output


def test_handle_delete_force_skips_preview(make_config, mocker) -> None:
    config = make_config()
    mocker.patch("account_automation.main.load_config", return_value=config)
    mocker.patch("account_automation.main.configure_logging")
    openstack = mocker.Mock()
    mocker.patch("account_automation.main.OpenStackServiceImpl", return_value=openstack)
    mocker.patch("builtins.open", side_effect=AssertionError("lock should not be used"))

    result = main_module._handle_delete(
        SimpleNamespace(username="alice", dry_run=True, force=True)
    )

    assert result == 0
    openstack.preview_delete.assert_not_called()
    openstack.delete_user_and_project.assert_called_once_with("alice")


def test_handle_preview_prints_readable_output(
    make_config,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_config()
    preview = DeletePreview(
        username="alice",
        user_found=False,
        project_found=True,
        volumes=(
            ResourceItem(id="v1", name="vol-a", extra="available"),
            ResourceItem(id="v2", name="vol-b", extra="available"),
            ResourceItem(id="v3", name="vol-c", extra="available"),
        ),
    )
    load_config = mocker.patch("account_automation.main.load_config", return_value=config)
    mocker.patch("account_automation.main.configure_logging")
    openstack = mocker.Mock()
    openstack.preview_delete.return_value = preview
    mocker.patch("account_automation.main.OpenStackServiceImpl", return_value=openstack)
    mocker.patch("builtins.open", side_effect=AssertionError("lock should not be used"))

    result = main_module._handle_preview(SimpleNamespace(username="alice"))

    assert result == 0
    load_config.assert_called_once_with(require_all=False)
    openstack.preview_delete.assert_called_once_with("alice")
    output = capsys.readouterr().out
    assert "Username: alice" in output
    assert "User found: no" in output
    assert "Project found: yes" in output
    assert "Volumes: 3" in output
    assert "Group found: no" in output


def test_handle_preview_prints_group_members(
    make_config,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_config()
    preview = DeletePreview(
        username="org-team",
        user_found=True,
        project_found=True,
        group_found=True,
        group_members=(
            ResourceItem(id="u1", name="alice", extra="alice@example.com"),
            ResourceItem(id="u2", name="bob", extra="bob@example.com"),
        ),
    )
    mocker.patch("account_automation.main.load_config", return_value=config)
    mocker.patch("account_automation.main.configure_logging")
    openstack = mocker.Mock()
    openstack.preview_delete.return_value = preview
    mocker.patch("account_automation.main.OpenStackServiceImpl", return_value=openstack)
    mocker.patch("builtins.open", side_effect=AssertionError("lock should not be used"))

    result = main_module._handle_preview(SimpleNamespace(username="org-team"))

    assert result == 0
    output = capsys.readouterr().out
    assert "Group found: yes" in output
    assert "Group Members: 2" in output
    assert "alice [u1] (alice@example.com)" in output
    assert "bob [u2] (bob@example.com)" in output


def test_handle_preview_shows_user_retention_when_other_roles(
    make_config,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_config()
    preview = DeletePreview(
        username="alice",
        user_found=True,
        project_found=True,
        user_has_other_roles=True,
    )
    mocker.patch("account_automation.main.load_config", return_value=config)
    mocker.patch("account_automation.main.configure_logging")
    openstack = mocker.Mock()
    openstack.preview_delete.return_value = preview
    mocker.patch("account_automation.main.OpenStackServiceImpl", return_value=openstack)
    mocker.patch("builtins.open", side_effect=AssertionError("lock should not be used"))

    result = main_module._handle_preview(SimpleNamespace(username="alice"))

    assert result == 0
    output = capsys.readouterr().out
    assert "will be RETAINED" in output


def test_handle_preview_shows_user_deletion_when_no_other_roles(
    make_config,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_config()
    preview = DeletePreview(
        username="alice",
        user_found=True,
        project_found=True,
        user_has_other_roles=False,
    )
    mocker.patch("account_automation.main.load_config", return_value=config)
    mocker.patch("account_automation.main.configure_logging")
    openstack = mocker.Mock()
    openstack.preview_delete.return_value = preview
    mocker.patch("account_automation.main.OpenStackServiceImpl", return_value=openstack)
    mocker.patch("builtins.open", side_effect=AssertionError("lock should not be used"))

    result = main_module._handle_preview(SimpleNamespace(username="alice"))

    assert result == 0
    output = capsys.readouterr().out
    assert "will be DELETED" in output


def test_main_reports_unmanaged_account_refusal(
    mocker, capsys: pytest.CaptureFixture[str],
) -> None:
    handler = mocker.Mock(side_effect=UnmanagedAccountError("Refusing to touch 'admin'"))
    mocker.patch("account_automation.main.configure_logging")
    mocker.patch("account_automation.main._parse_args", return_value=SimpleNamespace(func=handler))

    assert main_module.main() == 1
    assert "Refusing to touch 'admin'" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "apply", "only"),
    [([], False, None), (["--apply", "alice"], True, frozenset({"alice"}))],
)
def test_handle_backfill_tags_defaults_to_dry_run(
    make_config,
    make_row,
    mocker,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    apply: bool,
    only: frozenset[str] | None,
) -> None:
    config = make_config()
    mocker.patch("account_automation.main.load_config", return_value=config)
    mocker.patch("account_automation.main.configure_logging")
    repo = mocker.patch("account_automation.main.GoogleSheetsRepository", autospec=True)
    sheet_row = make_row()
    # Unparseable rows were never passed to backfill; tagging gates deletion,
    # so they still must not be.
    repo.return_value.read_all_rows.return_value = (sheet_row, _INVALID)
    connect = mocker.patch("account_automation.main.connect_openstack")
    backfill = mocker.patch(
        "account_automation.main.backfill_project_tags",
        return_value=(
            BackfillOutcome("alice", "would-tag"),
            BackfillOutcome("service", "skipped", "Reserved username"),
        ),
    )
    args = main_module._parse_args(["backfill-tags", *argv])

    assert args.func(args) == 0
    connect.assert_called_once_with(config.openstack_cloud)
    backfill.assert_called_once_with(
        connect.return_value,
        config.openstack_domain_id,
        (sheet_row,),
        apply=apply,
        only=only,
    )
    output = capsys.readouterr().out
    assert "alice: would-tag" in output
    assert "service: skipped (Reserved username)" in output
    # The unparseable row is named, so its project is not silently left untagged.
    assert "row 4 (bad user): unparseable, not considered (Invalid username)" in output


def test_handle_backfill_tags_apply_requires_named_usernames(
    mocker, capsys: pytest.CaptureFixture[str],
) -> None:
    load_config = mocker.patch("account_automation.main.load_config")
    args = main_module._parse_args(["backfill-tags", "--apply"])

    assert args.func(args) == 2
    load_config.assert_not_called()
    assert "--apply needs the usernames" in capsys.readouterr().err
