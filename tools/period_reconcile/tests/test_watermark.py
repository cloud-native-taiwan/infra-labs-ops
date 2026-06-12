from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from period_reconcile.watermark import (
    Watermark,
    load_watermark,
    save_watermark,
)


def _now() -> datetime:
    return datetime(2026, 6, 1, 1, 0, tzinfo=UTC)


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    wm = load_watermark(str(tmp_path / "wm.json"), "usage-reports")
    assert wm == Watermark(
        job="usage-reports", last_success_label=None, updated_at=None
    )


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "wm.json")
    wm = Watermark(
        job="usage-reports", last_success_label="2026-05", updated_at=_now()
    )
    save_watermark(path, wm)
    loaded = load_watermark(path, "usage-reports")
    assert loaded.last_success_label == "2026-05"
    assert loaded.updated_at == _now()


def test_save_is_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    path = str(tmp_path / "wm.json")
    save_watermark(
        path, Watermark(job="j", last_success_label="2026-05", updated_at=_now())
    )
    leftovers = list(tmp_path.glob(".watermark-*.tmp"))
    assert leftovers == []


def test_advanced_to_moves_forward() -> None:
    wm = Watermark(job="j", last_success_label="2026-04", updated_at=None)
    nxt = wm.advanced_to("2026-05", _now())
    assert nxt.last_success_label == "2026-05"
    assert nxt.updated_at == _now()


def test_advanced_to_never_regresses() -> None:
    wm = Watermark(job="j", last_success_label="2026-05", updated_at=_now())
    same = wm.advanced_to("2026-04", _now())
    assert same is wm


def test_advanced_to_from_empty() -> None:
    wm = Watermark(job="j", last_success_label=None, updated_at=None)
    nxt = wm.advanced_to("2026-01", _now())
    assert nxt.last_success_label == "2026-01"


def test_load_corrupt_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "wm.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupt"):
        load_watermark(str(path), "j")


def test_load_non_object_raises(tmp_path: Path) -> None:
    path = tmp_path / "wm.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a JSON object"):
        load_watermark(str(path), "j")


def test_load_wrong_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "wm.json"
    path.write_text(
        json.dumps({"version": 99, "job": "j", "last_success_label": "2026-05"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="version"):
        load_watermark(str(path), "j")


def test_load_wrong_job_raises(tmp_path: Path) -> None:
    path = tmp_path / "wm.json"
    save_watermark(
        str(path),
        Watermark(job="other", last_success_label="2026-05", updated_at=_now()),
    )
    with pytest.raises(RuntimeError, match="belongs to job"):
        load_watermark(str(path), "usage-reports")


def test_load_missing_label_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "wm.json"
    path.write_text(json.dumps({"version": 1, "job": "j"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing last_success_label"):
        load_watermark(str(path), "j")


@pytest.mark.parametrize("label", ["2026-5", "zzz", "2026-13", "2026-00", 5])
def test_load_malformed_label_raises(tmp_path: Path, label: object) -> None:
    """The reconcile loop compares labels lexicographically; a non-YYYY-MM
    value (e.g. unpadded '2026-5') sorts wrongly and could silently skip
    closed periods, so it must be a hard error."""
    path = tmp_path / "wm.json"
    path.write_text(
        json.dumps({"version": 1, "job": "j", "last_success_label": label}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="malformed last_success_label"):
        load_watermark(str(path), "j")


def test_load_null_label_is_fresh(tmp_path: Path) -> None:
    path = tmp_path / "wm.json"
    path.write_text(
        json.dumps({"version": 1, "job": "j", "last_success_label": None}),
        encoding="utf-8",
    )
    assert load_watermark(str(path), "j").last_success_label is None


def test_load_bad_updated_at_raises(tmp_path: Path) -> None:
    path = tmp_path / "wm.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "job": "j",
                "last_success_label": "2026-05",
                "updated_at": "not-a-date",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="updated_at"):
        load_watermark(str(path), "j")
