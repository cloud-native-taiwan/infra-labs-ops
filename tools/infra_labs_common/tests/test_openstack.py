from __future__ import annotations

from infra_labs_common.openstack import ROUTER_INTERFACE_OWNER, SYSTEM_PORT_OWNERS


def test_router_interface_owner_value() -> None:
    assert ROUTER_INTERFACE_OWNER == "network:router_interface"


def test_system_port_owners_membership() -> None:
    assert ROUTER_INTERFACE_OWNER in SYSTEM_PORT_OWNERS
    assert frozenset(
        {
            "network:dhcp",
            "network:router_interface",
            "network:router_gateway",
            "network:floatingip",
            "network:ha_router_replicated_interface",
        }
    ) == SYSTEM_PORT_OWNERS


def test_system_port_owners_is_immutable() -> None:
    assert isinstance(SYSTEM_PORT_OWNERS, frozenset)
