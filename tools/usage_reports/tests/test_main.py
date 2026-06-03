from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from usage_reports import main as main_module


def _patched_period(year: int, month: int):
    """Stable period stub so tests don't depend on real TZ libs."""
    from usage_reports.models import ReportPeriod

    return ReportPeriod(
        year=year,
        month=month,
        begin_utc=datetime(year, month, 1, tzinfo=timezone.utc),
        end_utc=datetime(year, month + 1 if month < 12 else 1, 1, tzinfo=timezone.utc),
    )


def test_resolve_period_uses_arg() -> None:
    p = main_module._resolve_period("2026-05", "Asia/Taipei")
    assert p.year == 2026
    assert p.month == 5


def test_resolve_period_defaults_to_previous_month() -> None:
    with patch(
        "usage_reports.main.datetime",
        autospec=True,
        wraps=datetime,
    ) as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 27, tzinfo=timezone.utc)
        p = main_module._resolve_period(None, "UTC")
    assert (p.year, p.month) == (2026, 4)


def test_parse_args_inserts_generate_when_first_token_is_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = main_module._parse_args(["--dry-run", "--month", "2026-05"])
    assert args.dry_run is True
    assert args.month == "2026-05"
    assert args.force is False


def test_parse_args_force_flag() -> None:
    args = main_module._parse_args(["generate", "--force"])
    assert args.force is True


def test_parse_args_scoping_flags_default_none() -> None:
    args = main_module._parse_args(["generate"])
    assert args.only_project is None
    assert args.only_email is None


def test_parse_args_scoping_flags_parsed() -> None:
    args = main_module._parse_args(
        ["generate", "--only-project", "p-1", "--only-email", "me@x.com"]
    )
    assert args.only_project == "p-1"
    assert args.only_email == "me@x.com"


def test_parse_args_record_deliveries_flag() -> None:
    assert main_module._parse_args(["generate"]).record_deliveries is False
    args = main_module._parse_args(["generate", "--record-deliveries"])
    assert args.record_deliveries is True


def test_parse_args_no_args_uses_generate() -> None:
    args = main_module._parse_args([])
    # generate handler resolved
    assert args.func is not None


def test_main_generate_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFRA_LABS_RESEND_API_KEY", "re_test")
    monkeypatch.setenv("INFRA_LABS_RESEND_FROM_EMAIL", "x@y.com")

    with patch("usage_reports.main.run_report", return_value=0) as mock_run:
        rc = main_module.main(["generate", "--dry-run", "--month", "2026-05"])
    assert rc == 0
    mock_run.assert_called_once()
    period_arg = mock_run.call_args.args[1]
    assert period_arg.label == "2026-05"
