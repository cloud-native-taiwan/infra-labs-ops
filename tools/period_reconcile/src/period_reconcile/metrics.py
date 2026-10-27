"""Prometheus textfile metrics for a reconcile pass.

node_exporter's textfile collector re-exports the file on every scrape, so the
alert rules (kolla/config/prometheus/period-reconcile-alerts.rules) work on the
invariant itself -- "a closed period has gone unreported for days" -- rather
than on exit codes, which a permanently refused (exit 2) or hung job never
turns into an error.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from infra_labs_common.persistence import atomic_write_text

from period_reconcile.periods import Period


def write_metrics(
    path: str,
    *,
    job: str,
    now: datetime,
    pending: tuple[Period, ...],
) -> None:
    """Atomically write the gauges for ``job`` to ``path`` (rename into place,
    so node_exporter never reads a half-written file)."""
    oldest_closed = pending[0].end.timestamp() if pending else 0
    # Not `job`: Prometheus overwrites that with the scrape job's name.
    labels = f'{{reconcile_job="{job}"}}'
    body = "".join(
        f"# TYPE {name} gauge\n{name}{labels} {value}\n"
        for name, value in (
            ("period_reconcile_last_run_timestamp_seconds", now.timestamp()),
            ("period_reconcile_pending_periods", len(pending)),
            ("period_reconcile_oldest_pending_closed_timestamp_seconds", oldest_closed),
        )
    )
    # 0644: node_exporter runs as a different user than the writer.
    atomic_write_text(path, body, tmp_prefix=f".{Path(path).name}.", mode=0o644)
