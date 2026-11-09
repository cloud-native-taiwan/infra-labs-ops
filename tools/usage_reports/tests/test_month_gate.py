"""Month-level checks in the orchestrator: the month summary line, unpriced
instance reporting, and the refusal (exit 2) of a month that looks unrated.

Kept apart from test_orchestrator.py (already over 900 lines); reuses its
service and fixture builders.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from usage_reports.models import ResourceKind
from usage_reports.orchestrator import run
from tests.test_orchestrator import (
    _make_member,
    _make_project,
    _period,
    _resource,
    _services,
)


def _run(make_config, tmp_path: Path, projects: tuple, *, force: bool = False, **kw) -> tuple:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"), **kw)
    cloudkitty, openstack, email = _services(projects=projects, members=(_make_member(),))
    rc = run(
        config=config,
        period=_period(),
        force=force,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    return rc, email


def test_run_no_projects_is_refused(make_config, tmp_path: Path) -> None:
    """An empty month means rated data is missing, not that the cloud was
    idle. Exit 2 (refused) so period-reconcile does not advance the watermark
    past a month that was never reported."""
    rc, email = _run(make_config, tmp_path, ())
    assert rc == 2
    email.send_cost_report.assert_not_called()


def test_run_all_zero_month_is_refused(make_config, tmp_path: Path) -> None:
    """Every project at $0 means rating is broken (pyscript disabled, Nova
    lookups failing), not a free month."""
    projects = (_make_project("proj-1", cost=0.0), _make_project("proj-2", cost=0.0))
    rc, email = _run(make_config, tmp_path, projects)
    assert rc == 2
    email.send_cost_report.assert_not_called()


def test_run_month_with_no_priced_instance_is_refused(make_config, tmp_path: Path) -> None:
    """Storage still priced but every running instance at $0: instance rating
    is broken, so the month must not be marked done on the storage total."""
    storage = _resource(kind=ResourceKind.STORAGE, resource_id="", cost=3.0)
    ghost = _resource(resource_id="vm-ghost", hours=12.0, cost=0.0)
    projects = (_make_project("proj-1", resources=(storage, ghost)),)
    rc, email = _run(make_config, tmp_path, projects)
    assert rc == 2
    email.send_cost_report.assert_not_called()


def test_run_force_overrides_empty_month_refusal(make_config, tmp_path: Path) -> None:
    rc, _ = _run(make_config, tmp_path, (), force=True)
    assert rc == 0


def test_run_logs_unpriced_instances_and_month_total(
    make_config, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An instance with active hours but $0 cost was not priced by rate.py
    (e.g. a VM whose flavor could not be resolved). It must be counted and
    named, never silently $0 -- and the month total makes a dry-run usable as
    a backlog re-check."""
    unpriced = _resource(resource_id="vm-ghost", hours=12.0, cost=0.0)
    shelved = _resource(resource_id="vm-shelved", hours=0.0, cost=0.0)
    projects = (
        _make_project("proj-1", cost=2.5),
        _make_project("proj-2", resources=(unpriced, shelved)),
    )

    with caplog.at_level("INFO", logger="usage_reports.orchestrator"):
        rc, _ = _run(make_config, tmp_path, projects, dry_run=True)

    assert rc == 0
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("vm-ghost" in m and "1 instance" in m for m in warnings)
    assert not any("vm-shelved" in m for m in warnings)
    assert any(
        "month=2026-05" in m and "total_cost=2.5" in m and "unpriced_instances=1" in m
        for m in (r.getMessage() for r in caplog.records)
    )
