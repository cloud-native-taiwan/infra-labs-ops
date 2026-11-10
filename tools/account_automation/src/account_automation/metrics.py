"""Success metric for node_exporter's textfile collector.

A fully successful run writes ``deploy_host_task_last_success_timestamp_seconds``
so the DeployHostTaskStale alert can fire when runs stop succeeding, whatever
the reason (crash, hang, lock contention, invalid rows, failed rows).
"""
from __future__ import annotations

import logging
from pathlib import Path

from infra_labs_common.persistence import atomic_write_text

LOGGER = logging.getLogger(__name__)

METRIC_NAME = "deploy_host_task_last_success_timestamp_seconds"
# Byte-identical across every deploy-host task writing this metric: node_exporter
# rejects one metric with differing HELP text across textfiles.
_HELP = "Unix time of the last successful run of a deploy-host task."
_TASK = "account_automation"
METRIC_FILENAME = f"task_{_TASK}.prom"


def write_success_metric(directory: str, now: float) -> None:
    """Atomically write the last-success gauge into ``directory``.

    Skips (DEBUG log) when the directory does not exist, so local runs and
    tests need no node_exporter. The temp file shares the directory but not the
    ``.prom`` suffix, so the collector never reads a partial file; 0644 because
    node_exporter runs as a different user. Raises on write failure.
    """
    target_dir = Path(directory)
    if not target_dir.is_dir():
        LOGGER.debug(
            "Textfile collector directory %s does not exist; not writing %s",
            directory,
            METRIC_FILENAME,
        )
        return
    body = (
        f"# HELP {METRIC_NAME} {_HELP}\n"
        f"# TYPE {METRIC_NAME} gauge\n"
        f'{METRIC_NAME}{{task="{_TASK}"}} {int(now)}\n'
    )
    atomic_write_text(
        target_dir / METRIC_FILENAME, body, tmp_prefix=f".{METRIC_FILENAME}.", mode=0o644
    )
