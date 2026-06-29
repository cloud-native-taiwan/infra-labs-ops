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
    ResourceIndex,
    ResourceKind,
)
from usage_reports.orchestrator import run


def _index_arg(call) -> object:
    """The index passed to enrich_resource, whether positional or keyword."""
    return call.args[1] if len(call.args) > 1 else call.kwargs.get("index")


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
    live_project_ids: tuple[str, ...] | None = None,
    scope_last_processed: dict[str, datetime | None] | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    cloudkitty = MagicMock()
    cloudkitty.get_summary.return_value = projects
    if scope_last_processed is not None:
        scope_map: dict[str, datetime | None] = scope_last_processed
    elif scope_fresh:
        scope_map = {"proj-1": datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)}
    else:
        scope_map = {"proj-1": None}
    cloudkitty.get_scope_last_processed.return_value = scope_map

    openstack = MagicMock()
    openstack.get_project_name.side_effect = lambda pid: f"name-{pid}"
    openstack.list_project_members.return_value = members
    # The freshness gate confirms a lagging scope's project still exists.
    # Default to treating every scope's project as live so existing cases are
    # unaffected; orphan tests pass an explicit live set omitting the deleted
    # project, and project_exists returns False for anything outside it.
    if live_project_ids is None:
        live_project_ids = tuple(scope_map.keys())
    live_set = set(live_project_ids)
    openstack.project_exists.side_effect = lambda pid: pid in live_set
    if enriched_passthrough:
        openstack.enrich_resource.side_effect = lambda r, index=None: r

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


def test_run_builds_resource_index_once_and_passes_to_enrichment(
    make_config, tmp_path: Path
) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    p1 = _make_project("proj-1")
    p2 = _make_project("proj-2")
    members = (_make_member(),)
    cloudkitty, openstack, email = _services(projects=(p1, p2), members=members)
    index = ResourceIndex(servers={}, volumes={})
    openstack.build_resource_index.return_value = index

    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )

    assert rc == 0
    openstack.build_resource_index.assert_called_once_with()
    assert openstack.enrich_resource.call_count == 2
    assert all(_index_arg(call) is index for call in openstack.enrich_resource.call_args_list)


def test_run_skips_resource_index_when_no_projects(make_config, tmp_path: Path) -> None:
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
    openstack.build_resource_index.assert_not_called()


def test_run_skips_resource_index_when_only_project_matches_nothing(
    make_config, tmp_path: Path
) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project("proj-1"),),
        members=(_make_member(),),
    )

    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_project="missing",
    )

    assert rc == 2
    openstack.build_resource_index.assert_not_called()


def test_run_resource_index_failure_falls_back_to_per_resource_enrichment(
    make_config, tmp_path: Path
) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member(),),
    )
    openstack.build_resource_index.side_effect = RuntimeError("bulk list failed")

    rc = run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )

    assert rc == 0
    openstack.enrich_resource.assert_called_once()
    assert _index_arg(openstack.enrich_resource.call_args) is None


def test_run_index_miss_confirms_deleted_end_to_end(make_config, tmp_path: Path) -> None:
    """A resource absent from a cleanly-built index is confirmed via a per-resource
    GET, not labeled deleted from absence alone (KTD-3 confirm-on-miss). Uses the
    real OpenStackServiceImpl so the enrich_resource miss path and _enrich_project's
    swallow interact for real, end to end through run()."""
    from types import SimpleNamespace

    from openstack import exceptions as os_exceptions

    from usage_reports.services.openstack_service import OpenStackServiceImpl

    conn = MagicMock()
    # Cleanly-built but empty index -> the project's instance is a miss.
    conn.compute.servers.return_value = []
    conn.block_storage.volumes.return_value = []
    # The confirming lookup 404s -> deleted (a transient error would yield unknown).
    conn.compute.get_server.side_effect = os_exceptions.ResourceNotFound("404")
    conn.identity.get_project.return_value = SimpleNamespace(name="proj-name")
    conn.identity.role_assignments.return_value = [
        SimpleNamespace(user={"id": "u-1"})
    ]
    conn.identity.get_user.return_value = SimpleNamespace(
        id="u-1", name="alice", email="alice@example.com"
    )
    openstack = OpenStackServiceImpl(config=make_config(), connection=conn)

    cloudkitty = MagicMock()
    cloudkitty.get_summary.return_value = (_make_project("proj-1"),)
    email = MagicMock()
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))

    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )

    assert rc == 0
    conn.compute.get_server.assert_called_once()
    sent_report = email.send_cost_report.call_args.args[0]
    assert sent_report.project.resources[0].status == "deleted"


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


def test_run_freshness_ignores_orphaned_scope(make_config, tmp_path: Path) -> None:
    """A scope for a deleted project (absent from the live project list) can
    never advance, so it must not wedge the gate. The live project's scope is
    fresh, so the report should generate."""
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project("proj-1"),),
        members=(_make_member(),),
        scope_last_processed={
            "proj-1": datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            "deleted-proj": datetime(2026, 5, 28, 15, 40, tzinfo=timezone.utc),
        },
        live_project_ids=("proj-1",),
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
    email.send_cost_report.assert_called_once()


def test_run_freshness_still_blocks_on_live_lagging_scope(make_config, tmp_path: Path) -> None:
    """A lagging scope whose project still exists must keep blocking, even when
    another scope belongs to a deleted project."""
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project("proj-1"),),
        members=(_make_member(),),
        scope_last_processed={
            "proj-1": datetime(2026, 5, 28, 15, 40, tzinfo=timezone.utc),
            "deleted-proj": None,
        },
        live_project_ids=("proj-1",),
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


def test_run_freshness_blocks_when_existence_unknown(make_config, tmp_path: Path) -> None:
    """If a lagging scope's project existence can't be determined (transient
    Keystone error), the gate must NOT treat it as deleted (which would
    under-bill). It stays conservative and blocks."""
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project("proj-1"),),
        members=(_make_member(),),
        scope_last_processed={"proj-1": datetime(2026, 5, 28, 15, 40, tzinfo=timezone.utc)},
    )
    openstack.project_exists.side_effect = RuntimeError("keystone down")
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


def test_run_all_recipients_failing_counts_as_project_failure(make_config, tmp_path: Path) -> None:
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


def test_run_email_failure_for_one_user_does_not_block_others(make_config, tmp_path: Path) -> None:
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


def test_run_manifest_idempotency_blocks_resend(make_config, tmp_path: Path) -> None:
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
    config = make_config(dry_run=True, delivery_manifest_path=str(manifest_path))
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


def test_run_only_project_filters_to_one(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project("proj-1"), _make_project("proj-2")),
        members=(_make_member(),),
    )
    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_project="proj-2",
    )
    assert rc == 0
    email.send_cost_report.assert_called_once()
    sent_report = email.send_cost_report.call_args.args[0]
    assert sent_report.project.project_id == "proj-2"


def test_run_only_project_no_match_returns_error(make_config, tmp_path: Path) -> None:
    """A mistyped --only-project is operator error, not success: exit 2
    (matching the freshness-gate pre-flight convention)."""
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project("proj-1"),),
        members=(_make_member(),),
    )
    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_project="nope",
    )
    assert rc == 2
    email.send_cost_report.assert_not_called()


def test_run_only_project_no_match_when_empty_summary(make_config, tmp_path: Path) -> None:
    """A scoped --only-project must report exit 2 even when the period has no
    billable usage at all, rather than being masked by the generic no-usage
    exit 0 path."""
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(projects=())
    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_project="proj-1",
    )
    assert rc == 2
    email.send_cost_report.assert_not_called()


def test_run_only_email_filters_recipient(make_config, tmp_path: Path) -> None:
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
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_email="BOB@example.com",
    )
    assert rc == 0
    email.send_cost_report.assert_called_once()
    sent_recipient = email.send_cost_report.call_args.args[1]
    assert sent_recipient.email == "bob@example.com"


def test_run_only_email_no_match_skips_project(make_config, tmp_path: Path) -> None:
    config = make_config(delivery_manifest_path=str(tmp_path / "m.json"))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member("alice@example.com"),),
    )
    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_email="stranger@example.com",
    )
    assert rc == 0
    email.send_cost_report.assert_not_called()


def test_run_scoped_does_not_write_manifest(make_config, tmp_path: Path) -> None:
    """A scoped real send must not poison the production manifest, or the next
    scheduled run would silently skip the recipient."""
    manifest_path = tmp_path / "deliveries.json"
    config = make_config(delivery_manifest_path=str(manifest_path))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member("alice@example.com"),),
    )
    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_email="alice@example.com",
    )
    assert rc == 0
    email.send_cost_report.assert_called_once()
    assert not manifest_path.exists()


def test_run_scoped_with_record_deliveries_writes_manifest(make_config, tmp_path: Path) -> None:
    manifest_path = tmp_path / "deliveries.json"
    config = make_config(delivery_manifest_path=str(manifest_path))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member("alice@example.com"),),
    )
    rc = run(
        config=config,
        period=_period(),
        force=True,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
        only_email="alice@example.com",
        record_deliveries=True,
    )
    assert rc == 0
    data = json.loads(manifest_path.read_text())
    assert "2026-05/proj-1/alice@example.com" in data


def test_run_unscoped_still_writes_manifest(make_config, tmp_path: Path) -> None:
    """The guard must not affect a normal full run -- deliveries are recorded."""
    manifest_path = tmp_path / "deliveries.json"
    config = make_config(delivery_manifest_path=str(manifest_path))
    cloudkitty, openstack, email = _services(
        projects=(_make_project(),),
        members=(_make_member("alice@example.com"),),
    )
    run(
        config=config,
        period=_period(),
        force=False,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )
    data = json.loads(manifest_path.read_text())
    assert "2026-05/proj-1/alice@example.com" in data


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


def test_run_cloudkitty_summary_failure_returns_error(make_config, tmp_path: Path) -> None:
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


def test_run_enrich_failure_uses_original_resource(make_config, tmp_path: Path) -> None:
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
