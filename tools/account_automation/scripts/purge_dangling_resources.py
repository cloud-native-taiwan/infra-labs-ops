#!/usr/bin/env python3
"""Purge OpenStack resources orphaned from deleted Keystone projects.

Enumerates every resource type across all tenants and deletes any whose
project_id no longer exists in Keystone.  Meant as a one-shot admin
remediation tool -- use ``account-automation delete`` for normal lifecycle.

Requires cloud-admin credentials with all_projects access (system-scoped or
a cloud-admin user).  Configure via ``clouds.yaml`` and pass ``--cloud``.

Usage:
    python purge_dangling_resources.py --dry-run
    python purge_dangling_resources.py --cloud mycloud --dry-run
    python purge_dangling_resources.py --project-id <orphaned-project-id> --dry-run
    python purge_dangling_resources.py --force
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from typing import Any

import openstack
from openstack.connection import Connection


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
) -> dict[str, dict[str, list[Any]]]:
    """Return {orphaned_project_id: {resource_type: [items]}} for each dangling resource."""
    result: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))

    def _add(pid: str | None, rtype: str, item: Any) -> None:
        if pid and pid not in valid_projects:
            result[pid][rtype].append(item)

    _scan(conn, result, _add, valid_projects)
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


def _purge_project(
    conn: Connection,
    project_id: str,
    resources: dict[str, list[Any]],
    dry_run: bool,
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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    conn = openstack.connect(cloud=args.cloud)

    LOGGER.info("Fetching active project IDs from Keystone...")
    valid_projects = _get_valid_project_ids(conn)
    LOGGER.info("Found %d active projects", len(valid_projects))

    LOGGER.info("Scanning for dangling resources (this may take a while)...")
    dangling = _collect_dangling(conn, valid_projects)

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
            _purge_project(conn, pid, dangling[pid], dry_run=True)
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
        _purge_project(conn, pid, dangling[pid], dry_run=False)

    LOGGER.info("Done")


if __name__ == "__main__":
    main()
