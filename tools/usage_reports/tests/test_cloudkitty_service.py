from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from usage_reports.models import ReportPeriod, ResourceKind
from usage_reports.services.cloudkitty_service import (
    AGGREGATE_GROUPBY,
    PER_RESOURCE_GROUPBY,
    RESOURCE_ID_KEY,
    CloudKittyServiceImpl,
    _format_utc,
    _group_into_projects,
)


# CloudKitty returns the groupby keys verbatim as `columns`, plus `qty`/`rate`.
# Derive these from the service constants so the tests can't drift if a groupby
# key is renamed.
PER_RESOURCE_COLUMNS = [*PER_RESOURCE_GROUPBY, "qty", "rate"]
AGGREGATE_COLUMNS = [*AGGREGATE_GROUPBY, "qty", "rate"]


def _period() -> ReportPeriod:
    return ReportPeriod(
        year=2026,
        month=5,
        begin_utc=datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
    )


def _mock_response(status_code: int = 200, body: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.raise_for_status = MagicMock()
    return resp


def _empty_page() -> dict[str, Any]:
    return {"total": 0, "columns": [], "results": []}


def _page_iter(pages: list[dict[str, Any]] | None):
    # Cycle on the last page so STANDARD_RETRY's repeated attempts on error
    # paths don't exhaust the iterator.
    if pages:
        return itertools.chain(pages[:-1], itertools.cycle([pages[-1]]))
    return itertools.cycle([_empty_page()])


def _build_service(
    make_config,
    per_resource_pages: list[dict[str, Any]] | None = None,
    aggregate_pages: list[dict[str, Any]] | None = None,
    scope_body: dict[str, Any] | None = None,
    status_code: int = 200,
) -> tuple[CloudKittyServiceImpl, MagicMock]:
    conn = MagicMock()
    session = MagicMock()
    conn.session = session
    session.get_endpoint.return_value = "http://cloudkitty.local"

    per_iter = _page_iter(per_resource_pages)
    agg_iter = _page_iter(aggregate_pages)

    def get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> MagicMock:
        if url.endswith("/v2/summary"):
            groupby = (params or {}).get("groupby", [])
            body = next(per_iter) if RESOURCE_ID_KEY in groupby else next(agg_iter)
            return _mock_response(status_code=status_code, body=body)
        if url.endswith("/v2/scope"):
            return _mock_response(status_code=status_code, body=scope_body or {})
        raise AssertionError(f"unexpected URL {url}")

    session.get.side_effect = get
    config = make_config(cloudkitty_endpoint_override="http://cloudkitty.local")
    service = CloudKittyServiceImpl(config=config, connection=conn)
    return service, session


def _summary_calls(session: MagicMock) -> list[Any]:
    return [c for c in session.get.call_args_list if c.args[0].endswith("/v2/summary")]


def test_format_utc_strips_timezone_aware() -> None:
    assert _format_utc(datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)) == (
        "2026-05-01T00:00:00+00:00"
    )


def test_get_summary_merges_per_resource_instance_and_aggregate_storage(make_config) -> None:
    per_resource = {
        "total": 1,
        "columns": PER_RESOURCE_COLUMNS,
        "results": [["proj-1", "instance", "vm-uuid-a", 6.0, 0.5]],
    }
    aggregate = {
        "total": 2,
        "columns": AGGREGATE_COLUMNS,
        # The aggregate query also returns an instance total; it must be
        # discarded so instance cost is not double counted.
        "results": [
            ["proj-1", "instance", 6.0, 0.5],
            ["proj-1", "storage", 60.0, 0.06],
        ],
    }
    service, _ = _build_service(
        make_config, per_resource_pages=[per_resource], aggregate_pages=[aggregate]
    )
    result = service.get_summary(_period())

    assert len(result) == 1
    project = result[0]
    assert project.project_id == "proj-1"
    assert {r.kind for r in project.resources} == {ResourceKind.INSTANCE, ResourceKind.STORAGE}

    instance = next(r for r in project.resources if r.kind is ResourceKind.INSTANCE)
    assert instance.resource_id == "vm-uuid-a"
    assert instance.hours == 1.0  # 6 periods / 6 = 1 hour
    assert instance.cost == 0.5

    storage = next(r for r in project.resources if r.kind is ResourceKind.STORAGE)
    assert storage.resource_id == ""  # aggregate, not per-volume
    assert storage.cost == 0.06
    # Instance counted once (per-resource), not also from the aggregate query.
    assert project.total_cost == 0.56


def test_get_summary_groups_multiple_projects(make_config) -> None:
    per_resource = {
        "total": 2,
        "columns": PER_RESOURCE_COLUMNS,
        "results": [
            ["proj-1", "instance", "vm-1", 6.0, 0.5],
            ["proj-2", "instance", "vm-2", 12.0, 1.0],
        ],
    }
    service, _ = _build_service(make_config, per_resource_pages=[per_resource])
    result = service.get_summary(_period())
    assert [p.project_id for p in result] == ["proj-1", "proj-2"]


def test_get_summary_storage_only_project(make_config) -> None:
    aggregate = {
        "total": 1,
        "columns": AGGREGATE_COLUMNS,
        "results": [["proj-1", "storage", 60.0, 0.06]],
    }
    service, _ = _build_service(make_config, aggregate_pages=[aggregate])
    result = service.get_summary(_period())
    assert len(result) == 1
    assert all(r.kind is ResourceKind.STORAGE for r in result[0].resources)
    assert all(r.resource_id == "" for r in result[0].resources)


def test_get_summary_empty_response(make_config) -> None:
    service, _ = _build_service(make_config)
    assert service.get_summary(_period()) == ()


def test_get_summary_pagination_loops(make_config) -> None:
    page1 = {
        "total": 3,
        "columns": PER_RESOURCE_COLUMNS,
        "results": [
            ["proj-1", "instance", "vm-1", 6.0, 0.5],
            ["proj-1", "instance", "vm-2", 6.0, 0.5],
        ],
    }
    page2 = {
        "total": 3,
        "columns": PER_RESOURCE_COLUMNS,
        "results": [["proj-1", "instance", "vm-3", 6.0, 0.5]],
    }
    service, session = _build_service(make_config, per_resource_pages=[page1, page2])
    result = service.get_summary(_period())
    assert len(result) == 1
    assert len(result[0].resources) == 3
    # 2 paginated per-resource calls + 1 (empty) aggregate call.
    assert len(_summary_calls(session)) == 3


def test_get_summary_aggregate_pagination_loops(make_config) -> None:
    # Storage is the majority of cost, so a paginated aggregate response must
    # be followed to the end or storage rows are silently dropped.
    page1 = {
        "total": 3,
        "columns": AGGREGATE_COLUMNS,
        "results": [
            ["proj-1", "storage", 60.0, 0.06],
            ["proj-2", "storage", 120.0, 0.12],
        ],
    }
    page2 = {
        "total": 3,
        "columns": AGGREGATE_COLUMNS,
        "results": [["proj-3", "storage", 180.0, 0.18]],
    }
    service, session = _build_service(make_config, aggregate_pages=[page1, page2])
    result = service.get_summary(_period())
    assert [p.project_id for p in result] == ["proj-1", "proj-2", "proj-3"]
    # 1 (empty) per-resource call + 2 paginated aggregate calls.
    assert len(_summary_calls(session)) == 3


def test_get_summary_raises_on_401(make_config) -> None:
    service, _ = _build_service(make_config, status_code=401)
    with pytest.raises(PermissionError, match="rating:rating:get_all"):
        service.get_summary(_period())


def test_get_summary_filters_by_tenant_id(make_config) -> None:
    per_resource = {
        "total": 1,
        "columns": PER_RESOURCE_COLUMNS,
        "results": [["proj-1", "instance", "vm-1", 6.0, 0.5]],
    }
    service, session = _build_service(make_config, per_resource_pages=[per_resource])
    service.get_summary(_period(), project_id="proj-1")
    # Both queries must filter on tenant_id (NOT project_id). Assert the call
    # count first so the loop cannot pass vacuously on zero calls.
    summary_calls = _summary_calls(session)
    assert len(summary_calls) == 2
    for call in summary_calls:
        assert call.kwargs["params"]["filters"] == "tenant_id:proj-1"


def test_get_summary_unknown_type_skipped(make_config) -> None:
    per_resource = {
        "total": 1,
        "columns": PER_RESOURCE_COLUMNS,
        "results": [["proj-1", "network", "n-1", 1.0, 0.1]],
    }
    aggregate = {
        "total": 1,
        "columns": AGGREGATE_COLUMNS,
        "results": [["proj-1", "network", 1.0, 0.1]],
    }
    service, _ = _build_service(
        make_config, per_resource_pages=[per_resource], aggregate_pages=[aggregate]
    )
    assert service.get_summary(_period()) == ()


def test_get_scope_last_processed_parses_iso(make_config) -> None:
    scope_body = {
        "results": [
            {"scope_id": "proj-1", "last_processed_timestamp": "2026-05-31T16:10:00+00:00"},
            {"scope_id": "proj-2", "last_processed_timestamp": None},
        ]
    }
    service, _ = _build_service(make_config, scope_body=scope_body)
    out = service.get_scope_last_processed()
    assert out["proj-1"] == datetime(2026, 5, 31, 16, 10, tzinfo=timezone.utc)
    assert out["proj-2"] is None


def test_resolve_base_url_uses_override(make_config) -> None:
    conn = MagicMock()
    config = make_config(cloudkitty_endpoint_override="http://override.local/")
    service = CloudKittyServiceImpl(config=config, connection=conn)
    assert service._base_url == "http://override.local"
    conn.session.get_endpoint.assert_not_called()


def test_resolve_base_url_falls_back_to_catalog(make_config) -> None:
    conn = MagicMock()
    conn.session.get_endpoint.return_value = "http://catalog.local/rating"
    config = make_config()
    service = CloudKittyServiceImpl(config=config, connection=conn)
    assert service._base_url == "http://catalog.local/rating"


def test_resolve_base_url_missing_raises(make_config) -> None:
    conn = MagicMock()
    conn.session.get_endpoint.return_value = None
    config = make_config()
    with pytest.raises(RuntimeError, match="not found in Keystone catalog"):
        CloudKittyServiceImpl(config=config, connection=conn)


def test_group_into_projects_skips_blank_tenant_ids() -> None:
    per_resource_rows = [
        {"tenant_id": "", "type": "instance", "uuid": "x", "qty": 1.0, "rate": 0.1},
        {"tenant_id": "proj-1", "type": "instance", "uuid": "y", "qty": 6.0, "rate": 0.5},
    ]
    result = _group_into_projects(per_resource_rows=per_resource_rows, aggregate_rows=[])
    assert len(result) == 1
    assert result[0].project_id == "proj-1"


def test_group_into_projects_drops_aggregate_instance_to_avoid_double_count() -> None:
    per_resource_rows = [
        {"tenant_id": "proj-1", "type": "instance", "uuid": "vm-1", "qty": 6.0, "rate": 0.5},
    ]
    aggregate_rows = [
        {"tenant_id": "proj-1", "type": "instance", "qty": 6.0, "rate": 0.5},
        {"tenant_id": "proj-1", "type": "storage", "qty": 60.0, "rate": 0.06},
    ]
    result = _group_into_projects(
        per_resource_rows=per_resource_rows, aggregate_rows=aggregate_rows
    )
    assert len(result) == 1
    resources = result[0].resources
    instances = [r for r in resources if r.kind is ResourceKind.INSTANCE]
    assert len(instances) == 1
    assert instances[0].resource_id == "vm-1"
    # Instance (0.5) once + storage (0.06); aggregate instance row dropped.
    assert result[0].total_cost == 0.56
