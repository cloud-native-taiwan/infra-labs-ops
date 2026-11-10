import logging
import stat
from pathlib import Path

import pytest

from account_automation.metrics import METRIC_FILENAME, write_success_metric


def test_write_success_metric_writes_gauge_atomically_world_readable(tmp_path: Path) -> None:
    write_success_metric(str(tmp_path), 1767225600.9)

    target = tmp_path / "task_account_automation.prom"
    assert METRIC_FILENAME == target.name
    assert target.read_text(encoding="utf-8") == (
        "# HELP deploy_host_task_last_success_timestamp_seconds"
        " Unix time of the last successful run of a deploy-host task.\n"
        "# TYPE deploy_host_task_last_success_timestamp_seconds gauge\n"
        'deploy_host_task_last_success_timestamp_seconds{task="account_automation"}'
        " 1767225600\n"
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    # Only the final file remains; no temp file node_exporter could pick up.
    assert [p.name for p in tmp_path.iterdir()] == [target.name]


def test_write_success_metric_skips_missing_directory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "absent"

    with caplog.at_level(logging.DEBUG):
        write_success_metric(str(missing), 1767225600)

    assert not missing.exists()
    assert "does not exist" in caplog.text
