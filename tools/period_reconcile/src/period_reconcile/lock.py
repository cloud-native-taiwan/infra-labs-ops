"""Advisory file lock for double-fire protection.

Two reconcile invocations can race: the hourly supercronic tick and the
container-start run, or two overlapping hourly ticks if one runs long. Both
would read the same watermark, see the same missed period, and run the
wrapped job twice. ``flock`` (advisory, non-blocking) lets the second caller
detect the first and bow out cleanly.

The lock is held for the whole reconcile pass. We use a separate lock file
(not the watermark file) so locking is independent of watermark reads/writes.
"""
from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockHeld(Exception):
    """Raised when another reconcile holds the lock."""


@contextmanager
def reconcile_lock(path: str) -> Iterator[None]:
    """Hold an exclusive, non-blocking advisory lock for the ``with`` body.

    Raises ``LockHeld`` immediately if another process holds it, so the caller
    can exit 0 (already being reconciled) rather than block or double-run.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Closing the handle releases the flock; no explicit LOCK_UN needed.
    with open(p, "w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockHeld(f"reconcile already running (lock {path})") from exc
        yield
