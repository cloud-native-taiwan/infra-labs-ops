from __future__ import annotations

from pathlib import Path

import pytest

from period_reconcile.lock import LockHeld, reconcile_lock


def test_lock_acquires_and_releases(tmp_path: Path) -> None:
    path = str(tmp_path / "lock")
    with reconcile_lock(path):
        pass
    # Re-acquirable after release.
    with reconcile_lock(path):
        pass


def test_lock_held_blocks_second_acquire(tmp_path: Path) -> None:
    path = str(tmp_path / "lock")
    with reconcile_lock(path), pytest.raises(LockHeld), reconcile_lock(path):
        pass


def test_lock_creates_parent_dir(tmp_path: Path) -> None:
    path = str(tmp_path / "nested" / "dir" / "lock")
    with reconcile_lock(path):
        assert Path(path).exists()
