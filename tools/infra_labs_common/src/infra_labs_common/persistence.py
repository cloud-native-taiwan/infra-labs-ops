"""Durable atomic JSON persistence.

Writes JSON via a temp file in the target directory, fsyncs the file, renames
it into place, then fsyncs the parent directory so the rename itself survives a
host crash. Callers that lose this durability could re-run already-completed
work (re-deliver an email, re-run a closed reporting period), so a write
failure is raised rather than swallowed.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(
    path: str | Path,
    payload: object,
    *,
    tmp_prefix: str,
    indent: int = 2,
    sort_keys: bool = True,
) -> None:
    """Serialize ``payload`` to JSON and write it to ``path`` atomically.

    The parent directory is created if needed. ``tmp_prefix`` names the
    in-directory temp file so a caller can recognize its own leftovers. Raises
    on any write failure after cleaning up the temp file.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=tmp_prefix, suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=indent, sort_keys=sort_keys)
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
