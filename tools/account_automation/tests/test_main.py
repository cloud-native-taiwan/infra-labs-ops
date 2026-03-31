from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import mock_open

import pytest

from account_automation import main as main_module
from account_automation.models import DeletePreview


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
    flock = mocker.patch("account_automation.main.fcntl.flock")
    open_file = mocker.patch("builtins.open", mock_open())
    repo = mocker.patch("account_automation.main.GoogleSheetsRepository", autospec=True)
    openstack = mocker.patch("account_automation.main.OpenStackServiceImpl", autospec=True)
    email = mocker.patch("account_automation.main.ResendEmailService", autospec=True)
    orchestrator_run = mocker.patch(
        "account_automation.main.run",
        return_value=[SimpleNamespace(success=True), SimpleNamespace(success=False)],
    )

    result = main_module._handle_run(SimpleNamespace(dry_run=False))

    assert result == 1
    load_config.assert_called_once_with(require_all=True)
    open_file.assert_called_once_with(main_module.LOCK_PATH, "w", encoding="utf-8")
    flock.assert_called_once()
    configure_logging.assert_any_call("DEBUG")
    repo.assert_called_once_with(config)
    openstack.assert_called_once_with(config)
    email.assert_called_once_with(config)
    orchestrator_run.assert_called_once()


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
        server_count=2,
        volume_count=1,
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
        server_count=0,
        volume_count=3,
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
