from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from period_reconcile.reconcile import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_REFUSED,
    reconcile,
)
from period_reconcile.watermark import Watermark, load_watermark, save_watermark

TZ = "Asia/Taipei"


def _now() -> datetime:
    # Mid-June 2026: newest closed monthly period is 2026-05.
    return datetime(2026, 6, 12, tzinfo=UTC)


class _Recorder:
    """Deterministic JobRunner that returns scripted exit codes per label."""

    def __init__(self, codes: dict[str, int], default: int = EXIT_OK) -> None:
        self._codes = codes
        self._default = default
        self.calls: list[str] = []

    def __call__(self, label: str) -> int:
        self.calls.append(label)
        return self._codes.get(label, self._default)


def test_up_to_date_does_nothing(tmp_path: Path) -> None:
    wm_path = str(tmp_path / "wm.json")
    save_watermark(
        wm_path, Watermark(job="j", last_success_label="2026-05", updated_at=_now())
    )
    runner = _Recorder({})
    outcome = reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=runner,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    assert outcome.exit_code == EXIT_OK
    assert runner.calls == []
    assert outcome.final_watermark == "2026-05"


def test_fresh_deploy_backfills_single_latest(tmp_path: Path) -> None:
    wm_path = str(tmp_path / "wm.json")
    runner = _Recorder({})
    outcome = reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=runner,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    assert runner.calls == ["2026-05"]
    assert outcome.exit_code == EXIT_OK
    assert outcome.succeeded == ("2026-05",)
    assert load_watermark(wm_path, "j").last_success_label == "2026-05"


def test_backfill_multiple_advances_watermark_each_step(tmp_path: Path) -> None:
    wm_path = str(tmp_path / "wm.json")
    save_watermark(
        wm_path, Watermark(job="j", last_success_label="2026-02", updated_at=_now())
    )
    runner = _Recorder({})
    outcome = reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=runner,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    assert runner.calls == ["2026-03", "2026-04", "2026-05"]
    assert outcome.exit_code == EXIT_OK
    assert load_watermark(wm_path, "j").last_success_label == "2026-05"


def test_refused_stops_and_keeps_watermark(tmp_path: Path) -> None:
    wm_path = str(tmp_path / "wm.json")
    save_watermark(
        wm_path, Watermark(job="j", last_success_label="2026-02", updated_at=_now())
    )
    # March succeeds, April refuses (freshness gate): stop, watermark at March.
    runner = _Recorder({"2026-04": EXIT_REFUSED})
    outcome = reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=runner,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    assert runner.calls == ["2026-03", "2026-04"]
    assert outcome.exit_code == EXIT_REFUSED
    assert outcome.succeeded == ("2026-03",)
    assert load_watermark(wm_path, "j").last_success_label == "2026-03"


def test_error_stops_and_keeps_watermark(tmp_path: Path) -> None:
    wm_path = str(tmp_path / "wm.json")
    save_watermark(
        wm_path, Watermark(job="j", last_success_label="2026-02", updated_at=_now())
    )
    runner = _Recorder({"2026-03": EXIT_ERROR})
    outcome = reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=runner,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    assert runner.calls == ["2026-03"]
    assert outcome.exit_code == EXIT_ERROR
    assert load_watermark(wm_path, "j").last_success_label == "2026-02"


def test_error_after_success_keeps_partial_progress(tmp_path: Path) -> None:
    """An error mid-back-fill must not roll back already-succeeded periods."""
    wm_path = str(tmp_path / "wm.json")
    save_watermark(
        wm_path, Watermark(job="j", last_success_label="2026-02", updated_at=_now())
    )
    runner = _Recorder({"2026-04": EXIT_ERROR})
    outcome = reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=runner,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    assert runner.calls == ["2026-03", "2026-04"]
    assert outcome.exit_code == EXIT_ERROR
    assert outcome.succeeded == ("2026-03",)
    assert load_watermark(wm_path, "j").last_success_label == "2026-03"


def test_unexpected_exit_code_treated_as_error(tmp_path: Path) -> None:
    wm_path = str(tmp_path / "wm.json")
    runner = _Recorder({"2026-05": 137})  # e.g. SIGKILL
    outcome = reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=runner,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    assert outcome.exit_code == EXIT_ERROR
    assert outcome.succeeded == ()


def test_resume_after_partial_backfill(tmp_path: Path) -> None:
    """Second pass resumes where the first stopped (idempotent reconcile)."""
    wm_path = str(tmp_path / "wm.json")
    save_watermark(
        wm_path, Watermark(job="j", last_success_label="2026-02", updated_at=_now())
    )
    # First pass: April refuses after March succeeds.
    first = _Recorder({"2026-04": EXIT_REFUSED})
    reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=first,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    # Second pass: everything now succeeds; should start at April, not March.
    second = _Recorder({})
    outcome = reconcile(
        job="j",
        watermark_path=wm_path,
        run_period=second,
        now=_now(),
        timezone=TZ,
        max_backfill=6,
    )
    assert second.calls == ["2026-04", "2026-05"]
    assert outcome.exit_code == EXIT_OK
    assert load_watermark(wm_path, "j").last_success_label == "2026-05"
