#!/usr/bin/env python3
"""Purge OpenStack resources orphaned from deleted Keystone projects.

Enumerates every resource type across all tenants and deletes any whose
project_id no longer exists in Keystone.  Meant as a one-shot admin
remediation tool -- use ``account-automation delete`` for normal lifecycle.

Requires the ``account-automation`` package to be installed (``uv sync`` or
``pip install -e .`` from the ``tools/account_automation`` directory).

Requires cloud-admin credentials with all_projects access (system-scoped or
a cloud-admin user).  Configure via ``clouds.yaml`` and pass ``--cloud``.

Ceph RadosGW buckets are purged when ``--rgw-admin-url``,
``--rgw-admin-access-key``, and ``--rgw-admin-secret-key`` are supplied.
The RGW admin API discovers all orphaned implicit-tenant accounts
automatically without needing a ``--project-id`` hint.

Usage:
    python purge_dangling_resources.py --dry-run
    python purge_dangling_resources.py --cloud mycloud --dry-run
    python purge_dangling_resources.py --project-id <orphaned-project-id> --dry-run
    python purge_dangling_resources.py --force \\
        --rgw-admin-url https://rgw.example.com \\
        --rgw-admin-access-key ACCESS \\
        --rgw-admin-secret-key SECRET
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from typing import Any

import openstack
from openstack.connection import Connection

from account_automation.services.rgw_admin import RgwAdminClient, RgwBucket


LOGGER = logging.getLogger(__name__)

_ROUTER_INTERFACE_OWNER = "network:router_interface"
_SYSTEM_PORT_OWNERS: frozenset[str] = frozenset({
    "network:dhcp",
    _ROUTER_INTERFACE_OWNER,
    "network:router_gateway",
    "network:floatingip",
    "network:ha_router_replicated_interface",
})

# Purge must follow dependency order; collection order is irrelevant.
_PURGE_ORDER = (
    "servers",
    "load_balancers",
    "floating_ips",
    "routers",       # handled separately (gateway clear + interface removal)
    "ports",
    "snapshots",
    "volumes",
    "networks",
    "security_groups",
    "images",
)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def _get_valid_project_ids(conn: Connection) -> frozenset[str]:
    return frozenset(p.id for p in conn.identity.projects())


def _collect_dangling(
    conn: Connection,
    valid_projects: frozenset[str],
    rgw: RgwAdminClient | None = None,
) -> dict[str, dict[str, list[Any]]]:
    """Return {orphaned_project_id: {resource_type: [items]}} for each dangling resource."""
    result: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))

    def _add(pid: str | None, rtype: str, item: Any) -> None:
        if pid and pid not in valid_projects:
            result[pid][rtype].append(item)

    _scan(conn, result, _add, valid_projects)

    if rgw is not None:
        # Discovers all orphaned implicit-tenant UIDs automatically via the RGW
        # admin API; no per-project hint needed.
        _scan_rgw(rgw, valid_projects, result)

    return {pid: dict(rtypes) for pid, rtypes in result.items()}


def _scan(
    conn: Connection,
    result: dict[str, dict[str, list[Any]]],
    add: Any,
    valid_projects: frozenset[str],
) -> None:
    _try_collect(
        "servers",
        lambda: conn.compute.servers(all_projects=True),
        lambda s: s.project_id,
        add,
    )
    _try_collect(
        "load_balancers",
        lambda: conn.load_balancer.load_balancers(),
        lambda lb: lb.project_id,
        add,
    )
    _try_collect(
        "floating_ips",
        lambda: conn.network.ips(),
        lambda fip: fip.project_id,
        add,
    )
    _try_collect(
        "routers",
        lambda: conn.network.routers(),
        lambda r: r.project_id,
        add,
    )
    _try_collect(
        "ports",
        lambda: (p for p in conn.network.ports() if (p.device_owner or "") not in _SYSTEM_PORT_OWNERS),
        lambda p: p.project_id,
        add,
    )
    _try_collect(
        "snapshots",
        lambda: conn.block_storage.snapshots(all_projects=True),
        lambda s: s.project_id,
        add,
    )
    _try_collect(
        "volumes",
        lambda: conn.block_storage.volumes(all_projects=True),
        lambda v: v.project_id,
        add,
    )
    _try_collect(
        "networks",
        lambda: conn.network.networks(),
        lambda n: n.project_id,
        add,
    )
    _try_collect(
        "security_groups",
        lambda: (sg for sg in conn.network.security_groups() if sg.name != "default"),
        lambda sg: sg.project_id,
        add,
    )
    # Images use `owner` (not project_id) as the tenant identifier.
    _try_collect(
        "images",
        lambda: conn.image.images(),
        lambda img: img.owner,
        add,
    )


def _try_collect(
    label: str,
    list_fn: Any,
    pid_fn: Any,
    add: Any,
) -> None:
    try:
        for item in list_fn():
            add(pid_fn(item), label, item)
    except Exception:
        LOGGER.warning("Failed to list %s -- skipping", label, exc_info=True)


def _scan_rgw(
    rgw: RgwAdminClient,
    valid_projects: frozenset[str],
    result: dict[str, dict[str, list[Any]]],
) -> None:
    """Discover RGW buckets owned by orphaned implicit-tenant users."""
    uids = rgw.list_implicit_tenant_uids()
    for uid in uids:
        project_id = uid.split("$")[0]
        if project_id in valid_projects:
            continue
        buckets = rgw.list_user_buckets(project_id)
        for bucket in buckets:
            result[project_id]["object_containers"].append(bucket)


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def _safe_delete(label: str, item: Any, delete_fn: Any, dry_run: bool) -> None:
    item_name = getattr(item, "name", None) or item.id
    if dry_run:
        LOGGER.info("Would delete %s %s", label, item_name)
        return
    LOGGER.info("Deleting %s %s", label, item_name)
    try:
        delete_fn()
    except Exception:
        LOGGER.warning("Failed to delete %s %s", label, item_name, exc_info=True)


def _purge_routers(conn: Connection, routers: list[Any], dry_run: bool) -> None:
    for router in routers:
        router_name = router.name or router.id
        if dry_run:
            LOGGER.info("Would delete router %s", router_name)
            continue

        # Clear static routes before clearing gateway; the gateway removal
        # is rejected (400) if any route's nexthop is on the gateway network.
        # Only send the call when routes actually exist to avoid a redundant
        # API round-trip (and a new failure path) on clean routers.
        if getattr(router, "routes", None):
            try:
                LOGGER.info("Clearing static routes on router %s", router_name)
                conn.network.update_router(router, routes=[])
            except Exception:
                LOGGER.warning("Failed to clear routes on router %s", router_name, exc_info=True)

        # Clear external gateway first to avoid 409 on delete.
        try:
            LOGGER.info("Clearing external gateway on router %s", router_name)
            conn.network.update_router(router, external_gateway_info=None)
        except Exception:
            LOGGER.warning("Failed to clear gateway on router %s", router_name, exc_info=True)

        # Remove all internal interfaces.
        try:
            ifaces = list(conn.network.ports(
                device_id=router.id,
                device_owner=_ROUTER_INTERFACE_OWNER,
            ))
        except Exception:
            LOGGER.warning("Failed to list interfaces for router %s", router_name, exc_info=True)
            ifaces = []

        for port in ifaces:
            try:
                LOGGER.info("Removing interface %s from router %s", port.id, router_name)
                conn.network.remove_interface_from_router(router, port_id=port.id)
            except Exception:
                LOGGER.warning(
                    "Failed to remove interface %s from router %s", port.id, router_name,
                    exc_info=True,
                )

        try:
            LOGGER.info("Deleting router %s", router_name)
            conn.network.delete_router(router.id)
        except Exception:
            LOGGER.warning("Failed to delete router %s", router_name, exc_info=True)


def _purge_rgw_buckets(
    rgw: RgwAdminClient,
    project_id: str,
    buckets: list[RgwBucket],
    dry_run: bool,
) -> None:
    for bucket in buckets:
        if dry_run:
            LOGGER.info("Would delete RGW bucket %s (project %s)", bucket.name, project_id)
            continue
        try:
            LOGGER.info("Deleting RGW bucket %s (project %s)", bucket.name, project_id)
            rgw.delete_bucket(bucket.name)
        except Exception:
            LOGGER.warning("Failed to delete RGW bucket %s", bucket.name, exc_info=True)


def _purge_project(
    conn: Connection,
    project_id: str,
    resources: dict[str, list[Any]],
    dry_run: bool,
    rgw: RgwAdminClient | None = None,
) -> None:
    LOGGER.info("Purging orphaned project %s", project_id)
    for rtype in _PURGE_ORDER:
        items = resources.get(rtype, [])
        if not items:
            continue

        if rtype == "routers":
            _purge_routers(conn, items, dry_run)
            continue

        def _del_server(i: Any) -> None:
            conn.compute.delete_server(i.id, force=True)
            conn.compute.wait_for_delete(i)

        def _del_lb(i: Any) -> None:
            conn.load_balancer.delete_load_balancer(i.id, cascade=True)
            conn.load_balancer.wait_for_delete(i)

        dispatch: dict[str, Any] = {
            "servers": _del_server,
            "load_balancers": _del_lb,
            "floating_ips": lambda i: conn.network.delete_ip(i.id),
            "ports": lambda i: conn.network.delete_port(i.id),
            "snapshots": lambda i: conn.block_storage.delete_snapshot(i.id, force=True),
            "volumes": lambda i: conn.block_storage.delete_volume(i.id, force=True),
            "networks": lambda i: conn.network.delete_network(i.id),
            "security_groups": lambda i: conn.network.delete_security_group(i.id),
            "images": lambda i: conn.image.delete_image(i.id),
        }
        delete_fn = dispatch[rtype]
        for item in items:
            _safe_delete(rtype[:-1], item, lambda i=item: delete_fn(i), dry_run)

    if rgw is not None:
        rgw_buckets = resources.get("object_containers", [])
        if rgw_buckets:
            _purge_rgw_buckets(rgw, project_id, rgw_buckets, dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cloud", default="openstack",
        help="OpenStack cloud name from clouds.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List dangling resources without deleting anything",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt (required in non-TTY environments)",
    )
    parser.add_argument(
        "--project-id", metavar="ID",
        help="Only purge resources belonging to this specific orphaned project ID",
    )
    parser.add_argument(
        "--rgw-admin-url", metavar="URL", default="",
        help="Ceph RadosGW admin API base URL (enables RGW bucket purge)",
    )
    parser.add_argument(
        "--rgw-admin-access-key", metavar="KEY", default="",
        help="S3 access key for the RGW admin API",
    )
    parser.add_argument(
        "--rgw-admin-secret-key", metavar="KEY", default="",
        help="S3 secret key for the RGW admin API",
    )
    parser.add_argument(
        "--rgw-admin-region", metavar="REGION", default="",
        help="AWS region name used in Sig V4 credential scope (default: empty)",
    )
    args = parser.parse_args()

    if args.rgw_admin_url and not (args.rgw_admin_access_key and args.rgw_admin_secret_key):
        parser.error("--rgw-admin-access-key and --rgw-admin-secret-key are required with --rgw-admin-url")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    conn = openstack.connect(cloud=args.cloud)

    rgw: RgwAdminClient | None = None
    if args.rgw_admin_url:
        rgw = RgwAdminClient(
            args.rgw_admin_url,
            args.rgw_admin_access_key,
            args.rgw_admin_secret_key,
            args.rgw_admin_region,
        )
        LOGGER.info("RGW admin API enabled at %s", args.rgw_admin_url)

    LOGGER.info("Fetching active project IDs from Keystone...")
    valid_projects = _get_valid_project_ids(conn)
    LOGGER.info("Found %d active projects", len(valid_projects))

    LOGGER.info("Scanning for dangling resources (this may take a while)...")
    dangling = _collect_dangling(conn, valid_projects, rgw=rgw)

    if args.project_id:
        if args.project_id not in dangling:
            LOGGER.info("No dangling resources found for project %s", args.project_id)
            sys.exit(0)
        dangling = {args.project_id: dangling[args.project_id]}

    if not dangling:
        LOGGER.info("No dangling resources found")
        sys.exit(0)

    total = sum(
        len(items)
        for resources in dangling.values()
        for items in resources.values()
    )
    LOGGER.info("Found %d dangling resources across %d orphaned projects:", total, len(dangling))
    for pid in sorted(dangling):
        counts = {k: len(v) for k, v in dangling[pid].items() if v}
        LOGGER.info("  %s  %s", pid, counts)

    if args.dry_run:
        LOGGER.info("Dry run -- listing what would be deleted:")
        for pid in sorted(dangling):
            _purge_project(conn, pid, dangling[pid], dry_run=True, rgw=rgw)
        sys.exit(0)

    if not args.force:
        if not sys.stdin.isatty():
            LOGGER.error("Non-interactive environment detected. Pass --force to delete without a prompt.")
            sys.exit(1)
        answer = input(
            f"\nPermanently delete {total} resources from {len(dangling)} orphaned projects? [y/N] ",
        )
        if answer.strip().lower() != "y":
            LOGGER.info("Aborted")
            sys.exit(0)

    for pid in sorted(dangling):
        _purge_project(conn, pid, dangling[pid], dry_run=False, rgw=rgw)

    LOGGER.info("Done")


if __name__ == "__main__":
    main()
