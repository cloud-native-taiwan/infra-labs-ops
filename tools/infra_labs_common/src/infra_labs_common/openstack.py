"""OpenStack port-owner constants shared by the deletion paths.

Both the account lifecycle deleter and the orphan-purge script must skip the
same system-managed ports (DHCP, router interfaces/gateways, floating IPs) when
tearing down a project's network resources, and both remove router interfaces
by the same device-owner. Keeping the constants here prevents the two paths
from drifting.
"""
from __future__ import annotations

ROUTER_INTERFACE_OWNER = "network:router_interface"

SYSTEM_PORT_OWNERS: frozenset[str] = frozenset({
    "network:dhcp",
    ROUTER_INTERFACE_OWNER,
    "network:router_gateway",
    "network:floatingip",
    "network:ha_router_replicated_interface",
})
