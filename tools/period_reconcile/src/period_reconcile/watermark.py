"""Per-job last-success watermark, persisted as JSON on disk.

The watermark records the label of the newest period for which the wrapped
job has exited 0. It is the single source of truth for "how far have we
reconciled". It lives on the same persistent volume the wrapped job already
uses for its own state (e.g. usage_reports' delivery manifest), so a
container rebuild does not lose it.

File format (JSON object)::

    {
      "version": 1,
      "job": "usage-reports",
      "last_success_label": "2026-05",
      "updated_at": "2026-06-01T01:00:00+00:00"
    }

A missing file means "no period has ever succeeded" (fresh deploy). A corrupt
or schema-wrong file is a hard error -- we refuse rather than guess, because
silently treating it as empty could re-run already-delivered periods or
(worse) skip periods on a later write.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

WATERMARK_VERSION = 1

# The reconcile loop orders periods by lexicographic label comparison, which
# is only correct for zero-padded YYYY-MM. A malformed label (e.g. "2026-5")
# would sort wrongly and silently skip closed months, so it is a hard error.
_LABEL_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class Watermark:
    """Immutable snapshot of a job's reconcile progress."""

    job: str
    last_success_label: str | None
    updated_at: datetime | None

    def advanced_to(self, label: str, now: datetime) -> Watermark:
        """Return a new watermark advanced to ``label`` (never regresses)."""
        if self.last_success_label is not None and label <= self.last_success_label:
            return self
        return Watermark(job=self.job, last_success_label=label, updated_at=now)


def load_watermark(path: str, job: str) -> Watermark:
    """Load the watermark for ``job``; an absent file yields an empty one.

    Raises ``RuntimeError`` on a corrupt or schema-mismatched file -- treating
    such a file as empty would defeat the integrity guarantee.
    """
    p = Path(path)
    if not p.exists():
        return Watermark(job=job, last_success_label=None, updated_at=None)

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Watermark at {path} is corrupt ({exc}); refusing to continue. "
            "Inspect/repair the file -- treating it as empty could re-run or "
            "skip closed periods."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot read watermark at {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError(f"Watermark at {path} is not a JSON object.")

    version = raw.get("version")
    if version != WATERMARK_VERSION:
        raise RuntimeError(
            f"Watermark at {path} has version {version!r}, expected "
            f"{WATERMARK_VERSION}; refusing to continue."
        )

    stored_job = raw.get("job")
    if stored_job != job:
        raise RuntimeError(
            f"Watermark at {path} belongs to job {stored_job!r}, not {job!r}; "
            "refusing to continue (wrong watermark file for this job)."
        )

    if "last_success_label" not in raw:
        raise RuntimeError(
            f"Watermark at {path} is missing last_success_label; refusing to "
            "continue (schema mismatch)."
        )
    label = raw["last_success_label"]
    if label is not None and (
        not isinstance(label, str) or not _LABEL_RE.fullmatch(label)
    ):
        raise RuntimeError(
            f"Watermark at {path} has malformed last_success_label {label!r} "
            "(want zero-padded YYYY-MM); refusing to continue -- the label "
            "comparison is lexicographic and a malformed value could skip "
            "closed periods."
        )

    updated_raw = raw.get("updated_at")
    updated_at: datetime | None = None
    if isinstance(updated_raw, str):
        try:
            updated_at = datetime.fromisoformat(updated_raw)
        except ValueError as exc:
            raise RuntimeError(
                f"Watermark at {path} has invalid updated_at {updated_raw!r}: {exc}"
            ) from exc

    return Watermark(job=job, last_success_label=label, updated_at=updated_at)


def save_watermark(path: str, watermark: Watermark) -> None:
    """Persist ``watermark`` atomically (tmp file + rename + fsync).

    Raises on write failure: silently swallowing would let the caller believe
    progress is durable when it is not, causing a closed period to be re-run
    on the next tick.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    updated = watermark.updated_at or datetime.now(UTC)
    payload = {
        "version": WATERMARK_VERSION,
        "job": watermark.job,
        "last_success_label": watermark.last_success_label,
        "updated_at": updated.isoformat(),
    }

    fd, tmp_path = tempfile.mkstemp(
        prefix=".watermark-", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, p)
        # fsync the directory too: os.replace alone is not durable across a
        # host crash until the directory entry itself is flushed.
        dir_fd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
