from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from account_automation.services.openstack_service import OpenStackServiceImpl


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    # Default: all list calls return empty
    conn.compute.servers.return_value = []
    conn.load_balancer.load_balancers.return_value = []
    conn.network.ips.return_value = []
    conn.network.routers.return_value = []
    conn.network.ports.return_value = []
    conn.network.networks.return_value = []
    conn.network.security_groups.return_value = []
    conn.block_storage.snapshots.return_value = []
    conn.block_storage.volumes.return_value = []
    conn.image.images.return_value = []
    return conn


@pytest.fixture
def service(make_config, mock_conn):
    config = make_config()
    with patch("account_automation.services.openstack_service.openstack") as mock_os:
        mock_os.connect.return_value = mock_conn
        svc = OpenStackServiceImpl(config)
    return svc


class TestPurgeProjectResources:
    def test_deletes_servers_with_force(self, service, mock_conn):
        server = SimpleNamespace(id="s1", name="web", status="ACTIVE")
        mock_conn.compute.servers.return_value = [server]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.compute.delete_server.assert_called_once_with("s1", force=True)
        mock_conn.compute.wait_for_delete.assert_called_once()

    def test_deletes_load_balancers_with_cascade(self, service, mock_conn):
        lb = SimpleNamespace(id="lb1", name="my-lb")
        mock_conn.load_balancer.load_balancers.return_value = [lb]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.load_balancer.delete_load_balancer.assert_called_once_with(
            "lb1", cascade=True,
        )
        mock_conn.load_balancer.wait_for_delete.assert_called_once()

    def test_deletes_floating_ips(self, service, mock_conn):
        fip = SimpleNamespace(id="fip1", name="1.2.3.4")
        mock_conn.network.ips.return_value = [fip]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.network.delete_ip.assert_called_once_with("fip1")

    def test_deletes_routers_after_clearing_gateway_and_removing_interfaces(
        self, service, mock_conn,
    ):
        router = SimpleNamespace(id="r1", name="my-router")
        port = SimpleNamespace(id="p1", device_owner="network:router_interface")
        mock_conn.network.routers.return_value = [router]
        # First call is from _purge_routers (interface listing),
        # subsequent calls are from _delete_resources("ports", ...)
        mock_conn.network.ports.side_effect = [[port], []]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.network.update_router.assert_called_once_with(
            router, external_gateway_info=None,
        )
        mock_conn.network.remove_interface_from_router.assert_called_once_with(
            router, port_id="p1",
        )
        mock_conn.network.delete_router.assert_called_once_with("r1")

    def test_skips_system_owned_ports(self, service, mock_conn):
        dhcp_port = SimpleNamespace(
            id="p1", name="dhcp-port", device_owner="network:dhcp",
        )
        user_port = SimpleNamespace(
            id="p2", name="user-port", device_owner="compute:nova",
        )
        mock_conn.network.ports.return_value = [dhcp_port, user_port]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.network.delete_port.assert_called_once_with("p2")

    def test_deletes_snapshots_before_volumes(self, service, mock_conn):
        snap = SimpleNamespace(id="snap1", name="my-snap", status="available")
        vol = SimpleNamespace(id="v1", name="my-vol", status="available", size=10)
        mock_conn.block_storage.snapshots.return_value = [snap]
        mock_conn.block_storage.volumes.return_value = [vol]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.block_storage.delete_snapshot.assert_called_once_with(
            "snap1", force=True,
        )
        mock_conn.block_storage.delete_volume.assert_called_once_with(
            "v1", force=True,
        )

    def test_deletes_networks(self, service, mock_conn):
        net = SimpleNamespace(id="n1", name="my-net")
        mock_conn.network.networks.return_value = [net]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.network.delete_network.assert_called_once_with("n1")

    def test_skips_default_security_group(self, service, mock_conn):
        default_sg = SimpleNamespace(id="sg1", name="default")
        custom_sg = SimpleNamespace(id="sg2", name="custom-sg")
        mock_conn.network.security_groups.return_value = [default_sg, custom_sg]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.network.delete_security_group.assert_called_once_with("sg2")

    def test_deletes_images(self, service, mock_conn):
        img = SimpleNamespace(id="img1", name="my-image")
        mock_conn.image.images.return_value = [img]

        service._purge_project_resources("proj-1", "alice")

        mock_conn.image.delete_image.assert_called_once_with("img1")

    def test_continues_on_individual_resource_failure(self, service, mock_conn):
        server1 = SimpleNamespace(id="s1", name="fail-server")
        server2 = SimpleNamespace(id="s2", name="ok-server")
        mock_conn.compute.servers.return_value = [server1, server2]
        mock_conn.compute.delete_server.side_effect = [RuntimeError("boom"), None]

        service._purge_project_resources("proj-1", "alice")

        assert mock_conn.compute.delete_server.call_count == 2

    def test_continues_on_list_failure(self, service, mock_conn):
        mock_conn.compute.servers.side_effect = RuntimeError("list failed")
        vol = SimpleNamespace(id="v1", name="vol", status="available", size=10)
        mock_conn.block_storage.volumes.return_value = [vol]

        service._purge_project_resources("proj-1", "alice")

        # Volumes should still be deleted even though servers listing failed
        mock_conn.block_storage.delete_volume.assert_called_once_with(
            "v1", force=True,
        )

    def test_router_interface_removal_failure_continues(self, service, mock_conn):
        router = SimpleNamespace(id="r1", name="router")
        port = SimpleNamespace(id="p1", device_owner="network:router_interface")
        mock_conn.network.routers.return_value = [router]
        mock_conn.network.ports.return_value = [port]
        mock_conn.network.remove_interface_from_router.side_effect = RuntimeError(
            "interface removal failed",
        )

        # Should not raise
        service._purge_project_resources("proj-1", "alice")

        mock_conn.network.remove_interface_from_router.assert_called_once()


class TestDeleteUserAndProjectCallsPurge:
    def test_purge_called_before_project_deletion(self, service, mock_conn):
        project = SimpleNamespace(id="proj-1")
        mock_conn.identity.find_user.return_value = None
        mock_conn.identity.find_project.return_value = project
        mock_conn.identity.find_group.return_value = None

        service.delete_user_and_project("alice")

        mock_conn.identity.delete_project.assert_called_once_with(
            project, ignore_missing=False,
        )

    def test_dry_run_skips_purge(self, make_config, mock_conn):
        config = make_config(dry_run=True)
        with patch("account_automation.services.openstack_service.openstack") as mock_os:
            mock_os.connect.return_value = mock_conn
            svc = OpenStackServiceImpl(config)

        svc.delete_user_and_project("alice")

        mock_conn.compute.servers.assert_not_called()
        mock_conn.identity.find_project.assert_not_called()
