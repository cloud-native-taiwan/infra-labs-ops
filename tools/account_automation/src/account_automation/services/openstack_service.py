# pragma: exclude file from coverage
from __future__ import annotations

import logging
from typing import Any, Protocol

import openstack
from openstack.connection import Connection

from account_automation.config import AppConfig
from account_automation.models import DeletePreview, SheetRow
from account_automation.retry import STANDARD_RETRY


LOGGER = logging.getLogger(__name__)
LOAD_BALANCER_EXTRA = "Load Balancer"


class OpenStackService(Protocol):
    def user_exists(self, username: str) -> bool:
        ...

    def project_exists(self, username: str) -> bool:
        ...

    def create_user_and_project(self, row: SheetRow, password: str) -> None:
        ...

    def delete_user_and_project(self, username: str) -> None:
        ...

    def preview_delete(self, username: str) -> "DeletePreview":
        ...


class OpenStackServiceImpl:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._conn: Connection = openstack.connect(cloud=config.openstack_cloud)

    @STANDARD_RETRY
    def user_exists(self, username: str) -> bool:
        return self._find_user(username) is not None

    @STANDARD_RETRY
    def project_exists(self, username: str) -> bool:
        return self._find_project(username) is not None

    @STANDARD_RETRY
    def create_user_and_project(self, row: SheetRow, password: str) -> None:
        if self._config.dry_run:
            LOGGER.info(
                "Dry run enabled; skipping OpenStack create for username=%s",
                row.username,
            )
            return

        user = self._find_user(row.username)
        project = self._find_project(row.username)

        if project is None:
            LOGGER.info("Creating OpenStack project for username=%s", row.username)
            project = self._conn.identity.create_project(
                name=row.username,
                domain_id=self._config.openstack_domain_id,
            )
        else:
            LOGGER.info("OpenStack project already exists for username=%s", row.username)

        if user is None:
            LOGGER.info("Creating OpenStack user for username=%s", row.username)
            user = self._conn.identity.create_user(
                name=row.username,
                email=row.email,
                domain_id=self._config.openstack_domain_id,
                password=password,
            )
        else:
            LOGGER.info("OpenStack user already exists for username=%s", row.username)

        member_role = self._get_role(self._config.openstack_member_role)
        self._ensure_project_role(project, user, member_role, row.username)

        if LOAD_BALANCER_EXTRA in row.quota.extras:
            lb_role = self._get_role(self._config.openstack_lb_role)
            self._ensure_project_role(project, user, lb_role, row.username)

        LOGGER.info("Updating compute quota for username=%s", row.username)
        self._conn.compute.update_quota_set(
            project,
            cores=row.quota.vcpus,
            ram=row.quota.ram_gb * 1024,
        )

        LOGGER.info("Updating block storage quota for username=%s", row.username)
        self._conn.block_storage.update_quota_set(
            project,
            gigabytes=row.quota.storage_gb,
        )

    @STANDARD_RETRY
    def delete_user_and_project(self, username: str) -> None:
        if self._config.dry_run:
            LOGGER.info(
                "Dry run enabled; skipping OpenStack delete for username=%s",
                username,
            )
            return

        user = self._find_user(username)
        if user is None:
            LOGGER.info("OpenStack user already absent for username=%s", username)
        else:
            LOGGER.info("Deleting OpenStack user for username=%s", username)
            self._conn.identity.delete_user(user, ignore_missing=False)

        project = self._find_project(username)
        if project is None:
            LOGGER.info("OpenStack project already absent for username=%s", username)
        else:
            LOGGER.info("Deleting OpenStack project for username=%s", username)
            self._conn.identity.delete_project(project, ignore_missing=False)

    @STANDARD_RETRY
    def preview_delete(self, username: str) -> DeletePreview:
        user = self._find_user(username)
        project = self._find_project(username)
        server_count = 0
        volume_count = 0
        if project is not None:
            servers = list(self._conn.compute.servers(project_id=project.id, all_projects=True))
            server_count = len(servers)
            volumes = list(self._conn.block_storage.volumes(project_id=project.id, all_projects=True))
            volume_count = len(volumes)
        return DeletePreview(
            username=username,
            user_found=user is not None,
            project_found=project is not None,
            server_count=server_count,
            volume_count=volume_count,
        )

    def _find_user(self, username: str) -> Any | None:
        return self._conn.identity.find_user(
            username,
            ignore_missing=True,
            domain_id=self._config.openstack_domain_id,
        )

    def _find_project(self, username: str) -> Any | None:
        return self._conn.identity.find_project(
            username,
            ignore_missing=True,
            domain_id=self._config.openstack_domain_id,
        )

    def _get_role(self, role_name: str) -> Any:
        role = self._conn.identity.find_role(role_name, ignore_missing=True)
        if role is None:
            raise LookupError(f"OpenStack role not found: {role_name}")
        return role

    def _ensure_project_role(
        self,
        project: Any,
        user: Any,
        role: Any,
        username: str,
    ) -> None:
        if self._conn.identity.validate_user_has_project_role(project, user, role):
            LOGGER.info(
                "OpenStack role %s already assigned for username=%s",
                role.name,
                username,
            )
            return

        LOGGER.info(
            "Assigning OpenStack role %s for username=%s",
            role.name,
            username,
        )
        self._conn.identity.assign_project_role_to_user(project, user, role)
