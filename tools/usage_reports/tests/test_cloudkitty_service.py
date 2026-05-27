from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from usage_reports.models import ReportPeriod, ResourceKind
from usage_reports.services.cloudkitty_service import (
    CloudKittyServiceImpl,
    _format_utc,
    _group_into_projects,
)


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


def _build_service(
    make_config,
    pages: list[dict[str, Any]] | None = None,
    scope_body: dict[str, Any] | None = None,
    status_code: int = 200,
) -> tuple[CloudKittyServiceImpl, MagicMock]:
    conn = MagicMock()
    session = MagicMock()
    conn.session = session
    session.get_endpoint.return_value = "http://cloudkitty.local"

    # Use chain + cycle on the last page so STANDARD_RETRY's repeated
    # attempts on error paths don't exhaust the iterator.
    if pages:
        page_iter = itertools.chain(pages[:-1], itertools.cycle([pages[-1]]))
    else:
        page_iter = itertools.cycle([{}])

    def get(url: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> MagicMock:
        if url.endswith("/v2/summary"):
            return _mock_response(status_code=status_code, body=next(page_iter))
        if url.endswith("/v2/scope"):
            return _mock_response(status_code=status_code, body=scope_body or {})
        raise AssertionError(f"unexpected URL {url}")

    session.get.side_effect = get
    config = make_config(cloudkitty_endpoint_override="http://cloudkitty.local")
    service = CloudKittyServiceImpl(config=config, connection=conn)
    return service, session


def test_format_utc_strips_timezone_aware() -> None:
    assert _format_utc(datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)) == (
        "2026-05-01T00:00:00+00:00"
    )


def test_get_summary_happy_path_parses_per_resource(make_config) -> None:
    body = {
        "total": 2,
        "columns": ["project_id", "type", "id", "qty", "rate"],
        "results": [
            ["proj-1", "instance", "vm-uuid-a", 6.0, 0.5],
            ["proj-1", "storage", "vol-uuid-a", 60.0, 0.06],
        ],
    }
    service, _ = _build_service(make_config, pages=[body])
    result = service.get_summary(_period())

    assert len(result) == 1
    project = result[0]
    assert project.project_id == "proj-1"
    assert len(project.resources) == 2
    assert {r.kind for r in project.resources} == {ResourceKind.INSTANCE, ResourceKind.STORAGE}
    instance = next(r for r in project.resources if r.kind is ResourceKind.INSTANCE)
    assert instance.resource_id == "vm-uuid-a"
    assert instance.hours == 1.0  # 6 periods / 6 = 1 hour
    assert instance.cost == 0.5


def test_get_summary_groups_multiple_projects(make_config) -> None:
    body = {
        "total": 2,
        "columns": ["project_id", "type", "id", "qty", "rate"],
        "results": [
            ["proj-1", "instance", "vm-1", 6.0, 0.5],
            ["proj-2", "instance", "vm-2", 12.0, 1.0],
        ],
    }
    service, _ = _build_service(make_config, pages=[body])
    result = service.get_summary(_period())
    assert [p.project_id for p in result] == ["proj-1", "proj-2"]


def test_get_summary_storage_only_project(make_config) -> None:
    body = {
        "total": 1,
        "columns": ["project_id", "type", "id", "qty", "rate"],
        "results": [["proj-1", "storage", "vol-1", 60.0, 0.06]],
    }
    service, _ = _build_service(make_config, pages=[body])
    result = service.get_summary(_period())
    assert len(result) == 1
    assert all(r.kind is ResourceKind.STORAGE for r in result[0].resources)


def test_get_summary_empty_response(make_config) -> None:
    body = {"total": 0, "columns": [], "results": []}
    service, _ = _build_service(make_config, pages=[body])
    assert service.get_summary(_period()) == ()


def test_get_summary_pagination_loops(make_config) -> None:
    page1 = {
        "total": 3,
        "columns": ["project_id", "type", "id", "qty", "rate"],
        "results": [
            ["proj-1", "instance", "vm-1", 6.0, 0.5],
            ["proj-1", "instance", "vm-2", 6.0, 0.5],
        ],
    }
    page2 = {
        "total": 3,
        "columns": ["project_id", "type", "id", "qty", "rate"],
        "results": [["proj-1", "instance", "vm-3", 6.0, 0.5]],
    }
    service, session = _build_service(make_config, pages=[page1, page2])
    result = service.get_summary(_period())
    assert len(result) == 1
    assert len(result[0].resources) == 3
    # session.get called twice for /v2/summary
    assert session.get.call_count == 2


def test_get_summary_raises_on_401(make_config) -> None:
    body: dict[str, Any] = {}
    service, _ = _build_service(make_config, pages=[body], status_code=401)
    with pytest.raises(PermissionError, match="rating:rating:get_all"):
        service.get_summary(_period())


def test_get_summary_filters_by_project(make_config) -> None:
    body = {
        "total": 1,
        "columns": ["project_id", "type", "id", "qty", "rate"],
        "results": [["proj-1", "instance", "vm-1", 6.0, 0.5]],
    }
    service, session = _build_service(make_config, pages=[body])
    service.get_summary(_period(), project_id="proj-1")
    # Inspect the captured params
    call_kwargs = session.get.call_args_list[0]
    assert call_kwargs.kwargs["params"]["filters"] == "project_id:proj-1"


def test_get_summary_unknown_type_skipped(make_config) -> None:
    body = {
        "total": 1,
        "columns": ["project_id", "type", "id", "qty", "rate"],
        "results": [["proj-1", "network", "n-1", 1.0, 0.1]],
    }
    service, _ = _build_service(make_config, pages=[body])
    result = service.get_summary(_period())
    assert result == ()


def test_get_scope_last_processed_parses_iso(make_config) -> None:
    scope_body = {
        "results": [
            {"scope_id": "proj-1", "last_processed_timestamp": "2026-05-31T16:10:00+00:00"},
            {"scope_id": "proj-2", "last_processed_timestamp": None},
        ]
    }
    service, _ = _build_service(make_config, pages=[], scope_body=scope_body)
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


def test_group_into_projects_skips_blank_project_ids() -> None:
    rows = [
        {"project_id": "", "type": "instance", "id": "x", "qty": 1.0, "rate": 0.1},
        {"project_id": "proj-1", "type": "instance", "id": "y", "qty": 6.0, "rate": 0.5},
    ]
    result = _group_into_projects(rows)
    assert len(result) == 1
    assert result[0].project_id == "proj-1"
