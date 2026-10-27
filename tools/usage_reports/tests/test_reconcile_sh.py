"""deploy/reconcile.sh, run for real with a stub period-reconcile.

The script is the only scheduled entry point; two bugs in it (a `--` glued to
the command, a dry-run guard looser than get_bool) would each have stalled or
silently skipped every month without failing any Python test.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "reconcile.sh"


def _run(tmp_path: Path, dry_run: str | None) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "period-reconcile"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "INFRA_LABS_DRY_RUN"}
    env |= {"PYTHON": sys.executable, "PERIOD_RECONCILE": str(stub)}
    if dry_run is not None:
        env["INFRA_LABS_DRY_RUN"] = dry_run
    return subprocess.run(
        ["sh", str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )


def test_passes_wrapped_command_after_separator(tmp_path: Path) -> None:
    result = _run(tmp_path, dry_run=None)
    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    sep = args.index("--")
    assert args[sep + 1 :] == [
        "/usr/local/bin/usage-reports",
        "generate",
        "--month",
        "{period}",
    ]
    assert args[args.index("--metrics-file") + 1] == "/metrics/usage-reports.prom"


@pytest.mark.parametrize("value", ["true", "TRUE", "\ttrue\t", " true ", "bogus"])
def test_refuses_dry_run_or_invalid(tmp_path: Path, value: str) -> None:
    result = _run(tmp_path, dry_run=value)
    assert result.returncode == 2
    assert result.stdout == ""


@pytest.mark.parametrize("value", ["", "false", "0"])
def test_runs_when_not_dry_run(tmp_path: Path, value: str) -> None:
    assert _run(tmp_path, dry_run=value).returncode == 0
