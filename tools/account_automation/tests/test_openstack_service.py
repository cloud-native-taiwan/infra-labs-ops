from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import openstack.exceptions
import pytest

from account_automation.services.openstack_service import OpenStackServiceImpl
from account_automation.services.rgw_admin import RgwBucket


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
    # Swift: default to object-store not available (no endpoint)
    conn.object_store.get_endpoint.side_effect = Exception("service not found")
    conn.session = MagicMock()
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

    def test_deletes_routers_after_clearing_routes_gateway_and_removing_interfaces(
        self, service, mock_conn,
    ):
        static_route = {"destination": "10.0.0.0/8", "nexthop": "192.168.1.1"}
        router = SimpleNamespace(id="r1", name="my-router", routes=[static_route])
        port = SimpleNamespace(id="p1", device_owner="network:router_interface")
        mock_conn.network.routers.return_value = [router]
        # First call is from _purge_routers (interface listing),
        # subsequent calls are from _delete_resources("ports", ...)
        mock_conn.network.ports.side_effect = [[port], []]

        service._purge_project_resources("proj-1", "alice")

        assert mock_conn.network.update_router.call_count == 2
        mock_conn.network.update_router.assert_has_calls([
            call(router, routes=[]),
            call(router, external_gateway_info=None),
        ])
        mock_conn.network.remove_interface_from_router.assert_called_once_with(
            router, port_id="p1",
        )
        mock_conn.network.delete_router.assert_called_once_with("r1")

    def test_router_without_static_routes_skips_route_clear(self, service, mock_conn):
        router = SimpleNamespace(id="r1", name="my-router", routes=[])
        mock_conn.network.routers.return_value = [router]
        mock_conn.network.ports.return_value = []

        service._purge_project_resources("proj-1", "alice")

        mock_conn.network.update_router.assert_called_once_with(
            router, external_gateway_info=None,
        )
        mock_conn.network.delete_router.assert_called_once_with("r1")

    def test_router_gateway_cleared_even_if_route_clear_fails(self, service, mock_conn):
        static_route = {"destination": "0.0.0.0/0", "nexthop": "103.122.117.254"}
        router = SimpleNamespace(id="r1", name="my-router", routes=[static_route])
        mock_conn.network.routers.return_value = [router]
        mock_conn.network.ports.return_value = []
        mock_conn.network.update_router.side_effect = [
            RuntimeError("routes update failed"),
            None,
        ]

        service._purge_project_resources("proj-1", "alice")

        assert mock_conn.network.update_router.call_count == 2
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


class TestSafeUserDeletion:
    @pytest.fixture()
    def alice_user(self):
        return SimpleNamespace(id="user-1", name="alice")

    @pytest.fixture()
    def alice_project(self):
        return SimpleNamespace(id="proj-1", name="alice")

    @pytest.fixture(autouse=True)
    def _no_group(self, mock_conn):
        mock_conn.identity.find_group.return_value = None

    def test_user_deleted_when_no_other_project_roles(
        self, service, mock_conn, alice_user, alice_project,
    ):
        target_role = SimpleNamespace(id="member-role")
        mock_conn.identity.find_user.return_value = alice_user
        mock_conn.identity.find_project.return_value = alice_project
        mock_conn.identity.role_assignments_filter.return_value = [target_role]
        mock_conn.identity.role_assignments.return_value = []

        service.delete_user_and_project("alice")

        mock_conn.identity.unassign_project_role_from_user.assert_called_once_with(
            alice_project,
            alice_user,
            "member-role",
        )
        mock_conn.identity.delete_user.assert_called_once_with(
            alice_user,
            ignore_missing=False,
        )

    def test_user_retained_when_has_other_project_roles(
        self, service, mock_conn, alice_user, alice_project,
    ):
        target_role = SimpleNamespace(id="member-role")
        other_assignment = SimpleNamespace(
            role={"id": "other-role"},
            scope={"project": {"id": "proj-2"}},
        )
        mock_conn.identity.find_user.return_value = alice_user
        mock_conn.identity.find_project.return_value = alice_project
        mock_conn.identity.role_assignments_filter.return_value = [target_role]
        mock_conn.identity.role_assignments.return_value = [other_assignment]

        service.delete_user_and_project("alice")

        mock_conn.identity.unassign_project_role_from_user.assert_called_once_with(
            alice_project,
            alice_user,
            "member-role",
        )
        mock_conn.identity.delete_user.assert_not_called()
        mock_conn.identity.delete_project.assert_called_once_with(
            alice_project,
            ignore_missing=False,
        )

    def test_user_absent_skips_deletion(
        self, service, mock_conn, alice_project, caplog,
    ):
        mock_conn.identity.find_user.return_value = None
        mock_conn.identity.find_project.return_value = alice_project

        with caplog.at_level(logging.INFO):
            service.delete_user_and_project("alice")

        mock_conn.identity.delete_user.assert_not_called()
        assert "OpenStack user already absent for username=alice" in caplog.text
        mock_conn.identity.delete_project.assert_called_once_with(
            alice_project,
            ignore_missing=False,
        )

    def test_user_roles_removed_from_target_project(
        self, service, mock_conn, alice_user, alice_project,
    ):
        role_one = SimpleNamespace(id="member-role")
        role_two = SimpleNamespace(id="lb-role")
        mock_conn.identity.find_user.return_value = alice_user
        mock_conn.identity.find_project.return_value = alice_project
        mock_conn.identity.role_assignments_filter.return_value = [role_one, role_two]
        mock_conn.identity.role_assignments.return_value = []

        service.delete_user_and_project("alice")

        mock_conn.identity.unassign_project_role_from_user.assert_has_calls([
            call(alice_project, alice_user, "member-role"),
            call(alice_project, alice_user, "lb-role"),
        ])
        assert mock_conn.identity.unassign_project_role_from_user.call_count == 2
        mock_conn.identity.delete_user.assert_called_once_with(
            alice_user,
            ignore_missing=False,
        )

    def test_role_removal_continues_on_not_found(
        self, service, mock_conn, alice_user, alice_project,
    ):
        stale_role = SimpleNamespace(id="stale-role")
        mock_conn.identity.find_user.return_value = alice_user
        mock_conn.identity.find_project.return_value = alice_project
        mock_conn.identity.role_assignments_filter.return_value = [stale_role]
        mock_conn.identity.role_assignments.return_value = []
        mock_conn.identity.unassign_project_role_from_user.side_effect = (
            openstack.exceptions.NotFoundException()
        )

        service.delete_user_and_project("alice")

        mock_conn.identity.unassign_project_role_from_user.assert_called_once()
        mock_conn.identity.delete_user.assert_called_once_with(
            alice_user,
            ignore_missing=False,
        )

    def test_domain_scoped_roles_ignored_for_retention_check(
        self, service, mock_conn, alice_user, alice_project,
    ):
        target_role = SimpleNamespace(id="member-role")
        domain_assignment = SimpleNamespace(
            role={"id": "domain-role"},
            scope={"domain": {"id": "domain-1"}},
        )
        mock_conn.identity.find_user.return_value = alice_user
        mock_conn.identity.find_project.return_value = alice_project
        mock_conn.identity.role_assignments_filter.return_value = [target_role]
        mock_conn.identity.role_assignments.return_value = [domain_assignment]

        service.delete_user_and_project("alice")

        mock_conn.identity.delete_user.assert_called_once_with(
            alice_user,
            ignore_missing=False,
        )

    def test_user_with_no_project_and_no_roles_deleted(
        self, service, mock_conn, alice_user,
    ):
        mock_conn.identity.find_user.return_value = alice_user
        mock_conn.identity.find_project.return_value = None
        mock_conn.identity.role_assignments.return_value = []

        service.delete_user_and_project("alice")

        mock_conn.identity.delete_user.assert_called_once_with(
            alice_user,
            ignore_missing=False,
        )
        mock_conn.identity.delete_project.assert_not_called()

    def test_user_with_no_project_but_other_roles_retained(
        self, service, mock_conn, alice_user,
    ):
        other_assignment = SimpleNamespace(
            role={"id": "member-role"},
            scope={"project": {"id": "proj-2"}},
        )
        mock_conn.identity.find_user.return_value = alice_user
        mock_conn.identity.find_project.return_value = None
        mock_conn.identity.role_assignments.return_value = [other_assignment]

        service.delete_user_and_project("alice")

        mock_conn.identity.delete_user.assert_not_called()
        mock_conn.identity.delete_project.assert_not_called()


class TestPreviewDeleteUserHasOtherRoles:
    def test_true_when_other_project_assignment(self, service, mock_conn):
        user = SimpleNamespace(id="user-1", name="alice")
        project = SimpleNamespace(id="proj-1")
        other_assignment = SimpleNamespace(
            scope={"project": {"id": "proj-2"}},
        )
        mock_conn.identity.find_user.return_value = user
        mock_conn.identity.find_project.return_value = project
        mock_conn.identity.find_group.return_value = None
        mock_conn.identity.role_assignments.return_value = [other_assignment]

        result = service.preview_delete("alice")

        assert result.user_has_other_roles is True

    def test_false_when_only_target_project(self, service, mock_conn):
        user = SimpleNamespace(id="user-1", name="alice")
        project = SimpleNamespace(id="proj-1")
        target_assignment = SimpleNamespace(
            scope={"project": {"id": "proj-1"}},
        )
        mock_conn.identity.find_user.return_value = user
        mock_conn.identity.find_project.return_value = project
        mock_conn.identity.find_group.return_value = None
        mock_conn.identity.role_assignments.return_value = [target_assignment]

        result = service.preview_delete("alice")

        assert result.user_has_other_roles is False

    def test_false_when_user_not_found(self, service, mock_conn):
        project = SimpleNamespace(id="proj-1")
        mock_conn.identity.find_user.return_value = None
        mock_conn.identity.find_project.return_value = project
        mock_conn.identity.find_group.return_value = None

        result = service.preview_delete("alice")

        assert result.user_has_other_roles is False


def _enable_rgw(service: OpenStackServiceImpl) -> MagicMock:
    """Attach a mock RgwAdminClient to service and return it."""
    mock_rgw = MagicMock()
    service._rgw = mock_rgw
    return mock_rgw


class TestRgwPurge:
    def test_deletes_buckets_via_rgw_admin(self, service, mock_conn):
        mock_rgw = _enable_rgw(service)
        mock_rgw.list_user_buckets.return_value = [
            RgwBucket(name="bucket-a", num_objects=3, size_bytes=2048),
            RgwBucket(name="bucket-b", num_objects=0, size_bytes=0),
        ]

        service._purge_object_storage("proj-1", "alice")

        mock_rgw.list_user_buckets.assert_called_once_with("proj-1")
        mock_rgw.delete_bucket.assert_has_calls([call("bucket-a"), call("bucket-b")])

    def test_skips_when_rgw_not_configured(self, service, mock_conn):
        # _rgw is None by default (no RGW URL in config)
        assert service._rgw is None
        service._purge_object_storage("proj-1", "alice")
        mock_conn.session.delete.assert_not_called()

    def test_continues_on_bucket_delete_failure(self, service, mock_conn):
        mock_rgw = _enable_rgw(service)
        mock_rgw.list_user_buckets.return_value = [
            RgwBucket(name="bad", num_objects=1, size_bytes=100),
            RgwBucket(name="good", num_objects=0, size_bytes=0),
        ]
        mock_rgw.delete_bucket.side_effect = [RuntimeError("delete failed"), None]

        service._purge_object_storage("proj-1", "alice")

        assert mock_rgw.delete_bucket.call_count == 2

    def test_no_buckets_skips_delete(self, service, mock_conn):
        mock_rgw = _enable_rgw(service)
        mock_rgw.list_user_buckets.return_value = []

        service._purge_object_storage("proj-1", "alice")

        mock_rgw.delete_bucket.assert_not_called()


class TestRgwPreview:
    def test_buckets_included_in_preview(self, service, mock_conn):
        mock_rgw = _enable_rgw(service)
        mock_rgw.list_user_buckets.return_value = [
            RgwBucket(name="data", num_objects=5, size_bytes=2048),
        ]
        user = SimpleNamespace(id="user-1", name="alice")
        project = SimpleNamespace(id="proj-1")
        mock_conn.identity.find_user.return_value = user
        mock_conn.identity.find_project.return_value = project
        mock_conn.identity.find_group.return_value = None
        mock_conn.identity.role_assignments.return_value = []

        result = service.preview_delete("alice")

        assert len(result.object_containers) == 1
        assert result.object_containers[0].name == "data"
        assert "5 objects" in result.object_containers[0].extra

    def test_preview_empty_when_rgw_not_configured(self, service, mock_conn):
        user = SimpleNamespace(id="user-1", name="alice")
        project = SimpleNamespace(id="proj-1")
        mock_conn.identity.find_user.return_value = user
        mock_conn.identity.find_project.return_value = project
        mock_conn.identity.find_group.return_value = None
        mock_conn.identity.role_assignments.return_value = []

        result = service.preview_delete("alice")

        assert result.object_containers == ()
