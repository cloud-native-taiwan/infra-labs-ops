"""Keystone / Nova / Cinder lookups for report enrichment.

Wraps openstacksdk so the orchestrator can:
  - List a project's member users and their emails (R12, R13)
  - Resolve a project_id to its human-readable name
  - Resolve resource UUIDs from CloudKitty into names / specs / status
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Protocol

import openstack
from openstack import exceptions as os_exceptions
from openstack.connection import Connection

from usage_reports.config import AppConfig
from usage_reports.models import ProjectMember, ResourceCost, ResourceKind
from usage_reports.retry import STANDARD_RETRY


LOGGER = logging.getLogger(__name__)


class OpenStackService(Protocol):
    def list_project_members(self, project_id: str) -> tuple[ProjectMember, ...]: ...

    def get_project_name(self, project_id: str) -> str: ...

    def project_exists(self, project_id: str) -> bool: ...

    def enrich_resource(self, resource: ResourceCost) -> ResourceCost: ...


class OpenStackServiceImpl:
    def __init__(self, config: AppConfig, connection: Connection | None = None) -> None:
        self._config = config
        self._conn: Connection = connection or openstack.connect(cloud=config.openstack_cloud)

    @STANDARD_RETRY
    def list_project_members(self, project_id: str) -> tuple[ProjectMember, ...]:
        # `effective=True` expands group-scoped role assignments into the
        # underlying user IDs, which is required because this fleet
        # assigns the `member` role to Keystone groups (per
        # account_automation's pattern). Without it, users granted access
        # via group membership would silently miss their cost reports.
        assignments = list(
            self._conn.identity.role_assignments(
                scope_project_id=project_id,
                effective=True,
            )
        )
        seen: set[str] = set()
        members: list[ProjectMember] = []
        for assignment in assignments:
            user_id = _user_id_from_assignment(assignment)
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            user = self._safe_get_user(user_id)
            if user is None:
                continue
            email = (user.get("email") or "").strip()
            if not email:
                continue
            members.append(
                ProjectMember(
                    user_id=user_id,
                    user_name=user.get("name") or user_id,
                    email=email,
                )
            )
        return tuple(members)

    @STANDARD_RETRY
    def get_project_name(self, project_id: str) -> str:
        try:
            project = self._conn.identity.get_project(project_id)
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
            self._conn.identity.get_project(project_id)
        except os_exceptions.ResourceNotFound:
            return False
        return True

    def enrich_resource(self, resource: ResourceCost) -> ResourceCost:
        if not resource.resource_id:
            return resource
        if resource.kind is ResourceKind.INSTANCE:
            return self._enrich_instance(resource)
        if resource.kind is ResourceKind.STORAGE:
            return self._enrich_volume(resource)
        return resource

    def _enrich_instance(self, resource: ResourceCost) -> ResourceCost:
        try:
            server = self._conn.compute.get_server(resource.resource_id)
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

    def _enrich_volume(self, resource: ResourceCost) -> ResourceCost:
        try:
            volume = self._conn.block_storage.get_volume(resource.resource_id)
        except os_exceptions.ResourceNotFound:
            LOGGER.info(
                "Volume no longer exists uuid=%s; reporting as deleted",
                resource.resource_id,
            )
            return replace(resource, name=resource.resource_id, status="deleted")
        except Exception as exc:
            LOGGER.warning(
                "Volume lookup failed (transient) uuid=%s err=%s; reporting as unknown",
                resource.resource_id,
                exc,
            )
            return replace(resource, name=resource.resource_id, status="unknown")
        size = getattr(volume, "size", None)
        specs = f"{size} GiB" if size is not None else ""
        return replace(
            resource,
            name=str(getattr(volume, "name", None) or resource.resource_id),
            specs=specs,
            status=str(getattr(volume, "status", "") or ""),
        )

    def _safe_get_user(self, user_id: str) -> dict[str, Any] | None:
        try:
            user = self._conn.identity.get_user(user_id)
        except Exception as exc:
            LOGGER.warning(
                "User lookup failed user_id=%s err=%s; skipping",
                user_id,
                exc,
            )
            return None
        return {
            "id": getattr(user, "id", user_id),
            "name": getattr(user, "name", None),
            "email": getattr(user, "email", None),
        }


def _user_id_from_assignment(assignment: Any) -> str | None:
    user = getattr(assignment, "user", None)
    if isinstance(user, dict):
        uid = user.get("id")
    else:
        uid = getattr(user, "id", None) if user else None
    return str(uid) if uid else None


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
