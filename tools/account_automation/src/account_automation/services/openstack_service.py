# pragma: exclude file from coverage
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import openstack
from openstack.connection import Connection

from account_automation.config import AppConfig
from account_automation.models import DeletePreview, ResourceItem, SheetRow
from account_automation.retry import STANDARD_RETRY


LOGGER = logging.getLogger(__name__)
LOAD_BALANCER_EXTRA = "Load Balancer"
_ROUTER_INTERFACE_OWNER = "network:router_interface"
SYSTEM_PORT_OWNERS: frozenset[str] = frozenset({
    "network:dhcp",
    _ROUTER_INTERFACE_OWNER,
    "network:router_gateway",
    "network:floatingip",
    "network:ha_router_replicated_interface",
})


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

    def log_project_resources(self, username: str) -> None:
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

        group = self._find_group(username)
        if group is None:
            LOGGER.info("OpenStack group already absent for username=%s", username)
        else:
            self._remove_all_group_members(group, username)
            LOGGER.info("Deleting OpenStack group for username=%s", username)
            self._conn.identity.delete_group(group, ignore_missing=False)

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
            self._purge_project_resources(project.id, username)
            LOGGER.info("Deleting OpenStack project for username=%s", username)
            self._conn.identity.delete_project(project, ignore_missing=False)

    @STANDARD_RETRY
    def preview_delete(self, username: str) -> DeletePreview:
        user = self._find_user(username)
        project = self._find_project(username)
        group = self._find_group(username)
        group_members = self._collect_group_members(group) if group else ()
        if project is None:
            return DeletePreview(
                username=username,
                user_found=user is not None,
                project_found=False,
                group_found=group is not None,
                group_members=group_members,
            )

        resources = self._collect_project_resources(project.id)
        return DeletePreview(
            username=username,
            user_found=user is not None,
            project_found=True,
            group_found=group is not None,
            group_members=group_members,
            **resources,
        )

    def log_project_resources(self, username: str) -> None:
        try:
            group = self._find_group(username)
            if group is not None:
                members = self._collect_group_members(group)
                LOGGER.info(
                    "Pre-deletion inventory for username=%s: group_members=%d",
                    username,
                    len(members),
                )
                for member in members:
                    LOGGER.info(
                        "  group_members: id=%s name=%s (%s)",
                        member.id,
                        member.name,
                        member.extra,
                    )

            project = self._find_project(username)
            if project is None:
                LOGGER.info(
                    "No project found for username=%s, skipping resource log",
                    username,
                )
                return

            resources = self._collect_project_resources(project.id)
            for resource_type, items in resources.items():
                LOGGER.info(
                    "Pre-deletion inventory for username=%s: %s=%d",
                    username,
                    resource_type,
                    len(items),
                )
                for item in items:
                    LOGGER.info(
                        "  %s: id=%s name=%s (%s)",
                        resource_type,
                        item.id,
                        item.name,
                        item.extra,
                    )
        except Exception:
            LOGGER.warning(
                "Failed to log project resources for username=%s",
                username,
                exc_info=True,
            )

    def _purge_project_resources(self, project_id: str, username: str) -> None:
        """Delete all resources in a project before deleting the project itself.

        Deletion order respects resource dependencies:
        servers (wait) -> load balancers (wait) -> floating IPs -> routers
        (gateway cleared, interfaces removed) -> ports -> snapshots -> volumes
        -> networks -> security groups -> images

        Servers and load balancers use wait=True because their teardown is
        asynchronous in Nova/Octavia; without waiting the project delete
        immediately following may still see child resources and fail.
        """
        LOGGER.info("Purging project resources for username=%s", username)

        def _del_server(s: Any) -> None:
            self._conn.compute.delete_server(s.id, force=True)
            self._conn.compute.wait_for_delete(s)

        def _del_lb(lb: Any) -> None:
            self._conn.load_balancer.delete_load_balancer(lb.id, cascade=True)
            self._conn.load_balancer.wait_for_delete(lb)

        self._delete_resources(
            "servers",
            lambda: self._conn.compute.servers(
                project_id=project_id, all_projects=True,
            ),
            _del_server,
            username,
        )

        self._delete_resources(
            "load_balancers",
            lambda: self._conn.load_balancer.load_balancers(
                project_id=project_id,
            ),
            _del_lb,
            username,
        )

        self._delete_resources(
            "floating_ips",
            lambda: self._conn.network.ips(project_id=project_id),
            lambda fip: self._conn.network.delete_ip(fip.id),
            username,
        )

        self._purge_routers(project_id, username)

        self._delete_resources(
            "ports",
            lambda: self._conn.network.ports(project_id=project_id),
            lambda p: self._conn.network.delete_port(p.id),
            username,
            skip_fn=lambda p: (p.device_owner or "") in SYSTEM_PORT_OWNERS,
        )

        self._delete_resources(
            "snapshots",
            lambda: self._conn.block_storage.snapshots(
                project_id=project_id, all_projects=True,
            ),
            lambda snap: self._conn.block_storage.delete_snapshot(snap.id, force=True),
            username,
        )

        self._delete_resources(
            "volumes",
            lambda: self._conn.block_storage.volumes(
                project_id=project_id, all_projects=True,
            ),
            lambda v: self._conn.block_storage.delete_volume(v.id, force=True),
            username,
        )

        self._delete_resources(
            "networks",
            lambda: self._conn.network.networks(project_id=project_id),
            lambda n: self._conn.network.delete_network(n.id),
            username,
        )

        self._delete_resources(
            "security_groups",
            lambda: self._conn.network.security_groups(project_id=project_id),
            lambda sg: self._conn.network.delete_security_group(sg.id),
            username,
            skip_fn=lambda sg: sg.name == "default",
        )

        self._delete_resources(
            "images",
            lambda: self._conn.image.images(owner=project_id),
            lambda img: self._conn.image.delete_image(img.id),
            username,
        )

    def _delete_resources(
        self,
        label: str,
        list_fn: Callable[[], Iterable[Any]],
        delete_fn: Callable[[Any], None],
        username: str,
        *,
        skip_fn: Callable[[Any], bool] | None = None,
    ) -> None:
        try:
            items = list(list_fn())
        except Exception:
            LOGGER.warning(
                "Failed to list %s for username=%s", label, username, exc_info=True,
            )
            return

        for item in items:
            item_name = getattr(item, "name", None) or item.id
            if skip_fn is not None and skip_fn(item):
                LOGGER.info("Skipping %s %s for username=%s", label, item_name, username)
                continue
            try:
                LOGGER.info("Deleting %s %s for username=%s", label, item_name, username)
                delete_fn(item)
            except Exception:
                LOGGER.warning(
                    "Failed to delete %s %s for username=%s",
                    label,
                    item_name,
                    username,
                    exc_info=True,
                )

    def _purge_routers(self, project_id: str, username: str) -> None:
        try:
            routers = list(self._conn.network.routers(project_id=project_id))
        except Exception:
            LOGGER.warning(
                "Failed to list routers for username=%s", username, exc_info=True,
            )
            return

        for router in routers:
            router_name = router.name or router.id

            # Clear external gateway first (prevents 409 on delete)
            try:
                LOGGER.info(
                    "Clearing external gateway on %s for username=%s",
                    router_name,
                    username,
                )
                self._conn.network.update_router(
                    router, external_gateway_info=None,
                )
            except Exception:
                LOGGER.warning(
                    "Failed to clear gateway on router %s for username=%s",
                    router_name,
                    username,
                    exc_info=True,
                )

            # Remove all internal interfaces
            try:
                ports = list(self._conn.network.ports(
                    device_id=router.id,
                    device_owner=_ROUTER_INTERFACE_OWNER,
                ))
            except Exception:
                LOGGER.warning(
                    "Failed to list interfaces for router %s for username=%s",
                    router_name,
                    username,
                    exc_info=True,
                )
                ports = []

            for port in ports:
                try:
                    LOGGER.info(
                        "Removing router interface %s from %s for username=%s",
                        port.id,
                        router_name,
                        username,
                    )
                    self._conn.network.remove_interface_from_router(
                        router, port_id=port.id,
                    )
                except Exception:
                    LOGGER.warning(
                        "Failed to remove interface %s from router %s for username=%s",
                        port.id,
                        router_name,
                        username,
                        exc_info=True,
                    )

            try:
                LOGGER.info(
                    "Deleting router %s for username=%s", router_name, username,
                )
                self._conn.network.delete_router(router.id)
            except Exception:
                LOGGER.warning(
                    "Failed to delete router %s for username=%s",
                    router_name,
                    username,
                    exc_info=True,
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

    def _find_group(self, username: str) -> Any | None:
        return self._conn.identity.find_group(
            username,
            ignore_missing=True,
            domain_id=self._config.openstack_domain_id,
        )

    def _list_resource(
        self,
        label: str,
        list_fn: Callable[[], Iterable[Any]],
        transform: Callable[[Any], ResourceItem],
    ) -> tuple[ResourceItem, ...]:
        try:
            return tuple(transform(item) for item in list_fn())
        except Exception:
            LOGGER.warning("Failed to list %s", label, exc_info=True)
            return ()

    def _collect_group_members(self, group: Any) -> tuple[ResourceItem, ...]:
        return self._list_resource(
            "group_members",
            lambda: self._conn.identity.group_users(group),
            lambda u: ResourceItem(
                id=u.id,
                name=u.name or "",
                extra=getattr(u, "email", "") or "",
            ),
        )

    def _collect_project_resources(
        self,
        project_id: str,
    ) -> dict[str, tuple[ResourceItem, ...]]:
        return {
            "servers": self._list_resource(
                "servers",
                lambda: self._conn.compute.servers(project_id=project_id, all_projects=True),
                lambda s: ResourceItem(id=s.id, name=s.name, extra=s.status or ""),
            ),
            "volumes": self._list_resource(
                "volumes",
                lambda: self._conn.block_storage.volumes(
                    project_id=project_id,
                    all_projects=True,
                ),
                lambda v: ResourceItem(
                    id=v.id,
                    name=v.name or "",
                    extra=f"{v.status}, {v.size}GB" if v.status else "",
                ),
            ),
            "networks": self._list_resource(
                "networks",
                lambda: self._conn.network.networks(project_id=project_id),
                lambda n: ResourceItem(id=n.id, name=n.name or "", extra=n.status or ""),
            ),
            "ports": self._list_resource(
                "ports",
                lambda: self._conn.network.ports(project_id=project_id),
                lambda p: ResourceItem(
                    id=p.id,
                    name=p.name or "",
                    extra=p.device_owner or "",
                ),
            ),
            "routers": self._list_resource(
                "routers",
                lambda: self._conn.network.routers(project_id=project_id),
                lambda r: ResourceItem(id=r.id, name=r.name or "", extra=r.status or ""),
            ),
            "floating_ips": self._list_resource(
                "floating_ips",
                lambda: self._conn.network.ips(project_id=project_id),
                lambda f: ResourceItem(
                    id=f.id,
                    name=getattr(f, "floating_ip_address", "") or "",
                    extra=f.status or "",
                ),
            ),
            "security_groups": self._list_resource(
                "security_groups",
                lambda: self._conn.network.security_groups(project_id=project_id),
                lambda sg: ResourceItem(
                    id=sg.id,
                    name=sg.name or "",
                    extra="default" if sg.name == "default" else "",
                ),
            ),
            "snapshots": self._list_resource(
                "snapshots",
                lambda: self._conn.block_storage.snapshots(
                    project_id=project_id,
                    all_projects=True,
                ),
                lambda snap: ResourceItem(
                    id=snap.id,
                    name=snap.name or "",
                    extra=snap.status or "",
                ),
            ),
            "load_balancers": self._list_resource(
                "load_balancers",
                lambda: self._conn.load_balancer.load_balancers(project_id=project_id),
                lambda lb: ResourceItem(
                    id=lb.id,
                    name=lb.name or "",
                    extra=getattr(lb, "vip_address", "") or "",
                ),
            ),
            "images": self._list_resource(
                "images",
                lambda: self._conn.image.images(owner=project_id),
                lambda img: ResourceItem(
                    id=img.id,
                    name=img.name or "",
                    extra=img.status or "",
                ),
            ),
        }

    def _get_role(self, role_name: str) -> Any:
        role = self._conn.identity.find_role(role_name, ignore_missing=True)
        if role is None:
            raise LookupError(f"OpenStack role not found: {role_name}")
        return role

    def _remove_all_group_members(self, group: Any, username: str) -> None:
        members = list(self._conn.identity.group_users(group))
        for member in members:
            LOGGER.info(
                "Removing user %s from group for username=%s",
                member.name,
                username,
            )
            try:
                self._conn.identity.remove_user_from_group(member, group)
            except openstack.exceptions.NotFoundException:
                LOGGER.info(
                    "User %s already removed from group for username=%s",
                    member.name,
                    username,
                )

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
