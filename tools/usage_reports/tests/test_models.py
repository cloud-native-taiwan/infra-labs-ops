from datetime import datetime, timezone

from usage_reports.models import (
    ProjectUsage,
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


def test_project_usage_total_cost_sums_and_rounds() -> None:
    usage = ProjectUsage(
        project_id="p1",
        project_name="lab1",
        resources=(_make_resource(2.5), _make_resource(1.5)),
    )
    assert usage.total_cost == 4.0


def test_project_usage_default_resources_total_zero() -> None:
    usage = ProjectUsage(project_id="p1", project_name="lab1")
    assert usage.resources == ()
    assert usage.total_cost == 0.0


def test_report_period_label_zero_pads() -> None:
    p = ReportPeriod(
        year=2026,
        month=5,
        begin_utc=datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
    )
    assert p.label == "2026-05"
