from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from usage_reports.models import (
    ProjectMember,
    ProjectUsage,
    ReportPeriod,
    ResourceCost,
    ResourceKind,
)
from usage_reports.orchestrator import run


def _period() -> ReportPeriod:
    return ReportPeriod(
        year=2026,
        month=5,
        begin_utc=datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
    )


def _resource(kind: ResourceKind = ResourceKind.INSTANCE, cost: float = 1.0) -> ResourceCost:
    return ResourceCost(
        kind=kind,
        resource_id="r-1",
        name="",
        specs="",
        hours=1.0,
        cost=cost,
    )


def _make_project(project_id: str = "proj-1", cost: float = 1.0) -> ProjectUsage:
    return ProjectUsage(
        project_id=project_id,
        project_name="",
        resources=(_resource(cost=cost),),
    )


def _make_member(email: str = "alice@example.com") -> ProjectMember:
    return ProjectMember(user_id="u-1", user_name="alice", email=email)


def _services(
    projects: tuple[ProjectUsage, ...] = (),
    members: tuple[ProjectMember, ...] = (),
    scope_fresh: bool = True,
    enriched_passthrough: bool = True,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    cloudkitty = MagicMock()
    cloudkitty.get_summary.return_value = projects
    if scope_fresh:
        cloudkitty.get_scope_last_processed.return_value = {
            "proj-1": datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        }
    else:
        cloudkitty.get_scope_last_processed.return_value = {"proj-1": None}

    openstack = MagicMock()
    openstack.get_project_name.side_effect = lambda pid: f"name-{pid}"
    openstack.list_project_members.return_value = members
    if enriched_passthrough:
        openstack.enrich_resource.side_effect = lambda r: r

    email = MagicMock()
    return cloudkitty, openstack, email


def test_run_no_projects_returns_zero(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(projects=())
    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 0
    email.send_cost_report.assert_not_called()


def test_run_happy_path_sends_per_member(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    members = (
        _make_member("alice@example.com"),
        _make_member("bob@example.com"),
    )
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=members,
    )
    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 0
    assert email.send_cost_report.call_count == 2


def test_run_freshness_check_blocks_unless_force(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
        scope_fresh=False,
    )
    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 2
    email.send_cost_report.assert_not_called()


def test_run_force_bypasses_freshness(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
        scope_fresh=False,
    )
    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 0
    email.send_cost_report.assert_called_once()


def test_run_skips_project_with_no_members(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(),
    )
    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 0
    email.send_cost_report.assert_not_called()


def test_run_skips_project_with_zero_cost(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(cost=0.0),),
        members=(_make_member(),),
    )
    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 0
    email.send_cost_report.assert_not_called()


def test_run_per_project_failure_isolated(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    p1 = _make_project("proj-1")
    p2 = _make_project("proj-2")
    cloudkitty, openstack, email = _services(
        projects=(p1, p2),
        members=(_make_member(),),
    )

    # Cause proj-1 to fail at project-name lookup; proj-2 should still send.
    def fail_first(pid: str) -> str:
        if pid == "proj-1":
            raise RuntimeError("ldap down")
        return f"name-{pid}"

    openstack.get_project_name.side_effect = fail_first

    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 1
    assert email.send_cost_report.call_count == 1


def test_run_all_recipients_failing_counts_as_project_failure(
    make_config, tmp_path: Path
) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    members = (_make_member("alice@example.com"), _make_member("bob@example.com"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=members,
    )
    email.send_cost_report.side_effect = RuntimeError("resend down")

    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 1


def test_run_corrupt_manifest_raises(make_config, tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text("not valid json {")
    config = make_config(delivery_manifest_path=str(manifest_path))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
    )
    with pytest.raises(RuntimeError, match="corrupt"):
        run(
            config=config,
            period=_period(),
            force=False,
            cloudkitty=cloudkitty,
            openstack=openstack,
            email=email,
        )


def test_run_persists_manifest_after_each_send(make_config, tmp_path: Path) -> None:
    """A mid-run crash must not lose already-delivered records."""
    manifest_path = tmp_path / "m.json"
    config = make_config(delivery_manifest_path=str(manifest_path))
    members = (_make_member("alice@example.com"), _make_member("bob@example.com"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=members,
    )

    persisted_after_alice: dict[str, str] = {}

    def capture_after_alice(report, recipient):
        if recipient.email == "alice@example.com" and manifest_path.exists():
            persisted_after_alice.update(json.loads(manifest_path.read_text()))
        if recipient.email == "bob@example.com":
            raise RuntimeError("simulated crash after Alice succeeded")

    email.send_cost_report.side_effect = capture_after_alice

    run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    # Alice's manifest key is persisted before Bob's send is attempted.
    final = json.loads(manifest_path.read_text())
    assert "2026-05/proj-1/alice@example.com" in final


def test_run_email_failure_for_one_user_does_not_block_others(
    make_config, tmp_path: Path
) -> None:
    """Bob's send failure does not prevent Alice's. The project counts as
    failed (rc=1) but Alice still gets her email."""
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    members = (
        _make_member("alice@example.com"),
        _make_member("bob@example.com"),
    )
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=members,
    )

    def fail_bob(report, recipient):
        if recipient.email == "bob@example.com":
            raise RuntimeError("transient")
        return None

    email.send_cost_report.side_effect = fail_bob

    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    # Any per-recipient failure escalates to a project-level failure so
    # cron exits non-zero. Both recipients are still attempted.
    assert rc == 1
    assert email.send_cost_report.call_count == 2


def test_run_manifest_idempotency_blocks_resend(
    make_config, tmp_path: Path
) -> None:
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(
        json.dumps(
            {
                "2026-05/proj-1/alice@example.com": "2026-05-30T00:00:00+00:00",
            }
        )
    )
    config = make_config(delivery_manifest_path=str(manifest_path))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
    )
    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 0
    email.send_cost_report.assert_not_called()


def test_run_force_resends_despite_manifest(make_config, tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(
        json.dumps(
            {
                "2026-05/proj-1/alice@example.com": "2026-05-30T00:00:00+00:00",
            }
        )
    )
    config = make_config(delivery_manifest_path=str(manifest_path))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
    )
    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 0
    email.send_cost_report.assert_called_once()


def test_run_writes_manifest_after_send(make_config, tmp_path: Path) -> None:
    manifest_path = tmp_path / "deliveries.json"
    config = make_config(delivery_manifest_path=str(manifest_path))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
    )
    run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert "2026-05/proj-1/alice@example.com" in data


def test_run_dry_run_does_not_write_manifest(make_config, tmp_path: Path) -> None:
    manifest_path = tmp_path / "deliveries.json"
    config = make_config(
        dry_run=True, delivery_manifest_path=str(manifest_path)
    )
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
    )
    run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert not manifest_path.exists()


def test_run_ae3_no_network_line_items(make_config, tmp_path: Path) -> None:
    """AE3: project with usage produces report with only instance and
    storage line items (network is filtered upstream in the CloudKitty
    service since metrics.yml excludes network metrics)."""
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    project = ProjectUsage(
        project_id="proj-1",
        project_name="",
        resources=(
            _resource(kind=ResourceKind.INSTANCE, cost=1.0),
            _resource(kind=ResourceKind.STORAGE, cost=0.1),
        ),
    )
    cloudkitty, openstack, email = _services(
        projects=(project,),
        members=(_make_member(),),
    )
    run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    # The report passed to send_cost_report carries only instance + storage,
    # never network -- network was excluded by metrics.yml before reaching us.
    sent_report = email.send_cost_report.call_args.args[0]
    kinds = {r.kind for r in sent_report.project.resources}
    assert kinds == {ResourceKind.INSTANCE, ResourceKind.STORAGE}


def test_run_cloudkitty_summary_failure_returns_error(
    make_config, tmp_path: Path
) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty = MagicMock()
    cloudkitty.get_scope_last_processed.return_value = {
        "x": datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    }
    cloudkitty.get_summary.side_effect = RuntimeError("api down")
    openstack = MagicMock()
    email = MagicMock()
    with pytest.raises(RuntimeError, match="api down"):
        run(
            config=config,
            period=_period(),
            force=False,
            cloudkitty=cloudkitty,
            openstack=openstack,
            email=email,
        )


def test_run_enrich_failure_uses_original_resource(
    make_config, tmp_path: Path
) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
    )
    openstack.enrich_resource.side_effect = RuntimeError("nova down")
    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    assert rc == 0
    sent_report = email.send_cost_report.call_args.args[0]
    # Original resource passed through
    assert sent_report.project.resources[0].resource_id == "r-1"
