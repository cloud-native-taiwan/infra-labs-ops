from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from period_reconcile import cli
from period_reconcile.lock import reconcile_lock
from period_reconcile.watermark import load_watermark

# Mid-June 2026: newest closed monthly period (Asia/Taipei) is 2026-05.
_NOW = datetime(2026, 6, 12, tzinfo=UTC)


def _args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--job",
        "usage-reports",
        "--watermark",
        str(tmp_path / "wm.json"),
        "--lock",
        str(tmp_path / "lock"),
        "--timezone",
        "Asia/Taipei",
        "--max-backfill",
        "6",
        "--",
        *extra,
    ]


def test_parse_args_strips_leading_dash_dash(tmp_path: Path) -> None:
    parsed = cli._parse_args(_args(tmp_path, "echo", "{period}"))
    assert parsed.command == ["echo", "{period}"]
    assert parsed.job == "usage-reports"
    assert parsed.max_backfill == 6


def test_main_refuses_command_without_period_token(tmp_path: Path) -> None:
    rc = cli.main(_args(tmp_path, "/bin/true"))
    assert rc == 1


def test_main_refuses_empty_command(tmp_path: Path) -> None:
    rc = cli.main(_args(tmp_path))
    assert rc == 1


def test_main_runs_and_substitutes_period(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    # Use sh to capture the substituted period into a file.
    rc = cli.main(
        _args(tmp_path, "/bin/sh", "-c", f"echo {{period}} >> {out}"),
        _now=_NOW,
    )
    assert rc == 0
    # Fresh deploy backfills exactly the single newest closed period.
    contents = out.read_text(encoding="utf-8").strip().splitlines()
    assert contents == ["2026-05"]
    wm = load_watermark(str(tmp_path / "wm.json"), "usage-reports")
    assert wm.last_success_label == "2026-05"


def test_main_propagates_error_exit(tmp_path: Path) -> None:
    rc = cli.main(_args(tmp_path, "/bin/sh", "-c", "echo {period}; exit 1"))
    assert rc == 1
    wm = load_watermark(str(tmp_path / "wm.json"), "usage-reports")
    assert wm.last_success_label is None


def test_main_propagates_refused_exit(tmp_path: Path) -> None:
    rc = cli.main(_args(tmp_path, "/bin/sh", "-c", "echo {period}; exit 2"))
    assert rc == 2


def test_main_exits_zero_when_lock_held(tmp_path: Path) -> None:
    """A concurrent reconcile owns the pass: exit 0, run nothing."""
    out = tmp_path / "out.txt"
    with reconcile_lock(str(tmp_path / "lock")):
        rc = cli.main(
            _args(tmp_path, "/bin/sh", "-c", f"echo {{period}} >> {out}"),
            _now=_NOW,
        )
    assert rc == 0
    assert not out.exists()
    wm = load_watermark(str(tmp_path / "wm.json"), "usage-reports")
    assert wm.last_success_label is None
