from datetime import datetime, timezone

import pytest

from usage_reports.models import (
    ProjectMember,
    ProjectUsage,
    ReportData,
    ReportPeriod,
    ResourceCost,
    ResourceKind,
)


def _make_resource(cost: float = 1.0) -> ResourceCost:
    return ResourceCost(
        kind=ResourceKind.INSTANCE,
        resource_id="uuid-1",
        name="vm1",
        specs="2 vCPU / 4 GiB",
        hours=24.0,
        cost=cost,
        status="active",
    )


def test_resource_cost_frozen() -> None:
    rc = _make_resource()
    with pytest.raises(Exception):
        rc.cost = 99.0  # type: ignore[misc]


def test_project_usage_resources_is_tuple() -> None:
    usage = ProjectUsage(
        project_id="p1",
        project_name="lab1",
        resources=(_make_resource(2.5), _make_resource(1.5)),
    )
    assert isinstance(usage.resources, tuple)
    assert usage.total_cost == 4.0


def test_project_usage_default_resources() -> None:
    usage = ProjectUsage(project_id="p1", project_name="lab1")
    assert usage.resources == ()
    assert usage.total_cost == 0.0


def test_report_period_label() -> None:
    p = ReportPeriod(
        year=2026,
        month=5,
        begin_utc=datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
    )
    assert p.label == "2026-05"


def test_report_data_holds_period_and_project() -> None:
    period = ReportPeriod(
        year=2026,
        month=5,
        begin_utc=datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
    )
    usage = ProjectUsage(project_id="p", project_name="n")
    report = ReportData(period=period, project=usage)
    assert report.period is period
    assert report.project is usage


def test_project_member_frozen() -> None:
    m = ProjectMember(user_id="u1", user_name="alice", email="a@b.com")
    with pytest.raises(Exception):
        m.email = "x"  # type: ignore[misc]
