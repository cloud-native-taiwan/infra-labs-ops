"""CloudKitty v2 API client.

openstacksdk does not ship a native CloudKitty proxy, so we use the
authenticated session it provides to talk to the `rating` service
directly. Endpoint discovery goes through the Keystone service catalog
unless the operator overrides it via configuration.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Protocol

import openstack
from keystoneauth1 import exceptions as ksa_exceptions
from openstack.connection import Connection

from usage_reports.config import AppConfig
from usage_reports.models import (
    ProjectUsage,
    ReportPeriod,
    ResourceCost,
    ResourceKind,
)
from usage_reports.retry import STANDARD_RETRY


LOGGER = logging.getLogger(__name__)

SUMMARY_PAGE_SIZE = 200
HTTP_TIMEOUT_SECONDS = 30.0


class CloudKittyService(Protocol):
    def get_summary(
        self, period: ReportPeriod, project_id: str | None = None
    ) -> tuple[ProjectUsage, ...]: ...

    def get_scope_last_processed(
        self, project_id: str | None = None
    ) -> dict[str, datetime | None]: ...


class CloudKittyServiceImpl:
    def __init__(self, config: AppConfig, connection: Connection | None = None) -> None:
        self._config = config
        self._conn: Connection = connection or openstack.connect(cloud=config.openstack_cloud)
        self._base_url = self._resolve_base_url()

    def _resolve_base_url(self) -> str:
        if self._config.cloudkitty_endpoint_override:
            return self._config.cloudkitty_endpoint_override.rstrip("/")
        # Honour the cloud's configured interface preference rather than
        # forcing 'public'. Some clouds publish rating only on 'internal'.
        endpoint = self._conn.session.get_endpoint(service_type="rating")
        if not endpoint:
            raise RuntimeError(
                "CloudKitty (service_type=rating) not found in Keystone catalog; "
                "set INFRA_LABS_CLOUDKITTY_ENDPOINT_OVERRIDE"
            )
        return str(endpoint).rstrip("/")

    def get_summary(
        self, period: ReportPeriod, project_id: str | None = None
    ) -> tuple[ProjectUsage, ...]:
        params = {
            "begin": _format_utc(period.begin_utc),
            "end": _format_utc(period.end_utc),
            "groupby": ["project_id", "type", "id"],
            "limit": SUMMARY_PAGE_SIZE,
        }
        if project_id is not None:
            params["filters"] = f"project_id:{project_id}"

        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page_rows, total = self._fetch_summary_page({**params, "offset": offset})
            rows.extend(page_rows)
            offset += len(page_rows)
            if not page_rows:
                break
            # Prefer the server's `total` when present. When it is absent,
            # fall back to a short-page check: a len()-based total guess
            # would stop one page early on an exactly-full final page.
            if total is not None and offset >= total:
                break
            if total is None and len(page_rows) < SUMMARY_PAGE_SIZE:
                break

        return _group_into_projects(rows)

    def get_scope_last_processed(
        self, project_id: str | None = None
    ) -> dict[str, datetime | None]:
        params: dict[str, Any] = {}
        if project_id is not None:
            params["project_id"] = project_id

        body = self._request_json(f"{self._base_url}/v2/scope", params)

        out: dict[str, datetime | None] = {}
        for entry in body.get("results", []):
            scope_id = entry.get("scope_id") or entry.get("project_id")
            ts_raw = entry.get("last_processed_timestamp")
            out[scope_id] = _parse_iso(ts_raw) if ts_raw else None
        return out

    def _fetch_summary_page(
        self, params: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int | None]:
        body = self._request_json(f"{self._base_url}/v2/summary", params)
        columns = body.get("columns", [])
        results = body.get("results", [])
        rows = [dict(zip(columns, row, strict=False)) for row in results]
        # `total` may be absent; get_summary's loop handles None.
        return rows, body.get("total")

    @STANDARD_RETRY
    def _request_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET `url` and return parsed JSON, retrying transient failures.

        Retry lives here -- on the single HTTP call -- not on the public
        methods: decorating a paginating method would replay the whole
        page loop on any mid-stream transient error. A 401 means the
        service account lacks the rating:rating:get_all role (a permanent
        condition), so it is raised as PermissionError, which
        STANDARD_RETRY does not retry.
        """
        try:
            response = self._conn.session.get(
                url, params=params, timeout=HTTP_TIMEOUT_SECONDS
            )
        except ksa_exceptions.Unauthorized as exc:
            raise PermissionError(
                "CloudKitty rejected the request as Unauthorized; the service "
                "account is missing the rating:rating:get_all role."
            ) from exc
        # Keystoneauth's default raise_exc=True converts 4xx/5xx into
        # exceptions before returning, but tests mock session.get
        # directly. Keep the status-code branch as a defensive guard.
        if response.status_code == 401:
            raise PermissionError(
                "CloudKitty returned 401: credentials missing rating:rating:get_all role"
            )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body


def _format_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _parse_iso(value: str) -> datetime | None:
    """Parse a CloudKitty ISO timestamp and force it to aware UTC.

    Some CloudKitty versions return naive ISO strings (no timezone
    offset). Comparing those to the period's aware boundary would raise
    TypeError; treat them as UTC explicitly.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _group_into_projects(rows: Iterable[dict[str, Any]]) -> tuple[ProjectUsage, ...]:
    """Group flat summary rows into per-project ProjectUsage tuples.

    Each input row carries `project_id`, `type`, `id`, `qty`, `rate`,
    `begin`, `end` keys (the columns CloudKitty returned). The `id`
    column is the resource UUID when the underlying metric carries one
    (instance). Project-aggregate metrics like storage emit rows with
    an empty `id`; those are surfaced as a single project-level row.
    """
    buckets: dict[str, list[ResourceCost]] = {}
    for row in rows:
        project_id = str(row.get("project_id") or "")
        if not project_id:
            continue
        kind_raw = str(row.get("type") or "")
        kind = _coerce_kind(kind_raw)
        if kind is None:
            continue
        resource_id = str(row.get("id") or "")
        qty = float(row.get("qty") or 0)
        rate = float(row.get("rate") or 0)
        hours = _qty_to_hours(kind, qty)
        name = "" if resource_id else _project_aggregate_name(kind)
        cost = ResourceCost(
            kind=kind,
            resource_id=resource_id,
            name=name,
            specs="",
            hours=round(hours, 4),
            cost=round(rate, 4),
        )
        buckets.setdefault(project_id, []).append(cost)

    projects = [
        ProjectUsage(
            project_id=pid,
            project_name="",
            resources=tuple(buckets[pid]),
        )
        for pid in sorted(buckets)
    ]
    return tuple(projects)


def _project_aggregate_name(kind: ResourceKind) -> str:
    if kind is ResourceKind.STORAGE:
        return "Project storage (aggregate)"
    return f"{kind.value} (aggregate)"


def _coerce_kind(value: str) -> ResourceKind | None:
    if not value:
        return None
    try:
        return ResourceKind(value)
    except ValueError:
        return None


def _qty_to_hours(kind: ResourceKind, qty: float) -> float:
    """Convert CloudKitty `qty` to a wall-clock-hours figure.

    CloudKitty sums the per-period value (here, max(value) within each
    600 s collection window) across the rating range. With the MAP
    mutator from metrics.yml emitting 1.0 only when status=ACTIVE, an
    always-on instance accumulates `qty = number_of_active_periods`, so
    dividing by 6 yields hours.

    For storage, qty is a sum of GiB-period samples; dividing by 6
    yields GiB-hours, which the email template labels separately
    (see templates/report.html). The conversion factor 6 is the number
    of 600 s periods per hour and MUST match `period = 600` in
    cloudkitty.conf — if the operator changes the period there, update
    this divisor too.
    """
    return qty / 6.0
