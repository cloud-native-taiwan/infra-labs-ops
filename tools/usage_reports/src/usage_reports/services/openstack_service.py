"""Keystone / Nova lookups for report enrichment.

Wraps openstacksdk so the orchestrator can:
  - List a project's member users and their emails (R12, R13)
  - Resolve a project_id to its human-readable name
  - Build a once-per-run cross-project index of servers
    (``build_resource_index``) so enrichment is a dict lookup, not an N+1 of
    per-resource GETs
  - Resolve resource UUIDs from CloudKitty into names / specs / status
"""
from __future__ import annotations

import logging
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Protocol

import openstack
from openstack import exceptions as os_exceptions
from openstack.connection import Connection

from usage_reports.config import AppConfig
from usage_reports.models import (
    ProjectMember,
    ProjectMembership,
    ResourceCost,
    ResourceIndex,
    ResourceKind,
)
from usage_reports.retry import STANDARD_RETRY


LOGGER = logging.getLogger(__name__)


class OpenStackService(Protocol):
    def list_project_members(self, project_id: str) -> ProjectMembership: ...

    def get_project_name(self, project_id: str) -> str: ...

    def project_exists(self, project_id: str) -> bool: ...

    def build_resource_index(self) -> ResourceIndex: ...

    def enrich_resource(
        self, resource: ResourceCost, index: ResourceIndex | None = None
    ) -> ResourceCost: ...


class OpenStackServiceImpl:
    def __init__(self, config: AppConfig, connection: Connection | None = None) -> None:
        self._config = config
        self._conn: Connection = connection or openstack.connect(cloud=config.openstack_cloud)

    # openstacksdk ships no usable stubs for its service proxies: `identity`
    # is typed as a `v2 | v3` Proxy union (and we only ever talk to v3), while
    # the compute proxy methods are untyped. Expose them as Any at this single
    # boundary so mypy --strict stays clean without scattering per-call ignores;
    # attribute access on the SDK results already goes through getattr, so no
    # real type safety is lost.
    @property
    def _identity(self) -> Any:
        return self._conn.identity

    @property
    def _compute(self) -> Any:
        return self._conn.compute

    @STANDARD_RETRY
    def list_project_members(self, project_id: str) -> ProjectMembership:
        # `effective=True` expands group-scoped role assignments into the
        # underlying user IDs, which is required because this fleet
        # assigns the `member` role to Keystone groups (per
        # account_automation's pattern). Without it, users granted access
        # via group membership would silently miss their cost reports.
        assignments = list(
            self._identity.role_assignments(
                scope_project_id=project_id,
                effective=True,
            )
        )
        seen: set[str] = set()
        members: list[ProjectMember] = []
        unresolved: list[str] = []
        for assignment in assignments:
            user_id = _user_id_from_assignment(assignment)
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            try:
                user = self._safe_get_user(user_id)
            except Exception as exc:
                # A transient Keystone error survived per-user retries. Do NOT
                # silently drop the recipient: record the id so the run reports
                # an incomplete member list and exits non-zero, rather than
                # under-delivering without a trace. Other members still resolve.
                LOGGER.error(
                    "User lookup failed after retries user_id=%s err=%s; "
                    "recording as unresolved",
                    user_id,
                    exc,
                )
                unresolved.append(user_id)
                continue
            if user is None:
                continue  # definitive 404 -- the user is gone, skip permanently
            email = (user.get("email") or "").strip()
            if not email:
                # A member with no email is legitimate (not a failure), but it is
                # a silent gap in coverage otherwise -- log it so the operator
                # can see who was skipped and why.
                LOGGER.warning(
                    "Skipping project member with no email project_id=%s user_id=%s",
                    project_id,
                    user_id,
                )
                continue
            members.append(
                ProjectMember(
                    user_id=user_id,
                    user_name=user.get("name") or user_id,
                    email=email,
                )
            )
        return ProjectMembership(
            members=tuple(members),
            unresolved_user_ids=tuple(unresolved),
        )

    @STANDARD_RETRY
    def get_project_name(self, project_id: str) -> str:
        try:
            project = self._identity.get_project(project_id)
        except Exception as exc:
            LOGGER.warning("Project lookup failed project_id=%s err=%s", project_id, exc)
            return project_id
        return str(getattr(project, "name", None) or project_id)

    @STANDARD_RETRY
    def project_exists(self, project_id: str) -> bool:
        """Whether the project still exists in Keystone.

        Returns False only on a definitive 404 (the project is gone), and
        True if it exists. A transient lookup error propagates (and is
        retried) rather than being mistaken for deletion -- the freshness
        gate must not excuse a live project's lagging scope on a blip.
        """
        try:
            self._identity.get_project(project_id)
        except os_exceptions.ResourceNotFound:
            return False
        return True

    @STANDARD_RETRY
    def build_resource_index(self) -> ResourceIndex:
        """One bulk listing of every server, keyed by id -- a single API call
        versus one GET per rated instance. Threaded through the run loop so
        per-project enrichment is a dict lookup.

        Only servers are indexed: storage is aggregated by CloudKitty into a
        project-level line with no per-volume resource_id, so storage rows never
        reach a per-resource lookup. A volume listing would need reintroducing
        here if per-volume itemization ever lands.
        """
        servers = {
            str(server.id): server
            for server in self._compute.servers(all_projects=True, details=True)
        }
        # MappingProxyType so the shared index cannot be mutated in-place as it
        # is threaded through every project in the run loop.
        return ResourceIndex(servers=MappingProxyType(servers))

    def enrich_resource(
        self, resource: ResourceCost, index: ResourceIndex | None = None
    ) -> ResourceCost:
        # Storage rows are project-level aggregates (resource_id="") that return
        # at the guard below, so only instances use the index. Instance paths:
        #   - index hit  -> resolve from the snapshot, no API call. A resource
        #     deleted *after* the index was built still reports its snapshot
        #     status; that point-in-time skew is the cost of dropping the N+1.
        #   - index miss -> fall through to the per-resource GET, which CONFIRMS
        #     before labeling: 404 -> "deleted", transient -> "unknown". A short
        #     index (e.g. a down Nova cell) must not be read as "deleted".
        #   - index is None (build failed) -> legacy per-resource path.
        if not resource.resource_id:
            return resource
        if resource.kind is ResourceKind.INSTANCE:
            if index is not None and resource.resource_id in index.servers:
                return _apply_server(resource, index.servers[resource.resource_id])
            return self._enrich_instance(resource)
        return resource

    def _enrich_instance(self, resource: ResourceCost) -> ResourceCost:
        try:
            server = self._compute.get_server(resource.resource_id)
        except os_exceptions.ResourceNotFound:
            LOGGER.info(
                "Instance no longer exists uuid=%s; reporting as deleted",
                resource.resource_id,
            )
            return replace(resource, name=resource.resource_id, status="deleted")
        except Exception as exc:
            # Transient API errors should not be confused with deletion.
            LOGGER.warning(
                "Instance lookup failed (transient) uuid=%s err=%s; reporting as unknown",
                resource.resource_id,
                exc,
            )
            return replace(resource, name=resource.resource_id, status="unknown")
        return _apply_server(resource, server)

    def _safe_get_user(self, user_id: str) -> dict[str, Any] | None:
        """Resolve one user. Returns None ONLY on a definitive 404 (the user is
        gone -- skip permanently). A transient failure that survives retries
        propagates so the caller records the recipient as unresolved instead of
        silently dropping their report."""
        user = self._fetch_user(user_id)
        if user is None:
            LOGGER.info("User %s not found (404); skipping recipient", user_id)
            return None
        return {
            "id": getattr(user, "id", user_id),
            "name": getattr(user, "name", None),
            "email": getattr(user, "email", None),
        }

    @STANDARD_RETRY
    def _fetch_user(self, user_id: str) -> Any | None:
        """GET one user, retrying transient errors with backoff. A definitive
        404 returns None (not retried); every other error is transient and is
        retried, then re-raised for the caller to surface."""
        try:
            return self._identity.get_user(user_id)
        except os_exceptions.ResourceNotFound:
            return None


def _user_id_from_assignment(assignment: Any) -> str | None:
    user = getattr(assignment, "user", None)
    if isinstance(user, dict):
        uid = user.get("id")
    else:
        uid = getattr(user, "id", None) if user else None
    return str(uid) if uid else None


def _apply_server(resource: ResourceCost, server: Any) -> ResourceCost:
    flavor = getattr(server, "flavor", {}) or {}
    vcpus = flavor.get("vcpus") if isinstance(flavor, dict) else getattr(flavor, "vcpus", None)
    ram = flavor.get("ram") if isinstance(flavor, dict) else getattr(flavor, "ram", None)
    specs = _format_instance_specs(vcpus, ram)
    return replace(
        resource,
        name=str(getattr(server, "name", None) or resource.resource_id),
        specs=specs,
        status=str(getattr(server, "status", "") or ""),
    )


def _format_instance_specs(vcpus: Any, ram_mb: Any) -> str:
    parts: list[str] = []
    if vcpus is not None:
        parts.append(f"{vcpus} vCPU")
    if ram_mb is not None:
        try:
            ram_gib = round(int(ram_mb) / 1024, 1)
            parts.append(f"{ram_gib} GiB RAM")
        except (TypeError, ValueError):
            pass
    return " / ".join(parts)
