"""Full-cluster power contract (cluster-shutdown.yml / cluster-startup.yml).

Pins the parts a dry run cannot exercise: the interface/peer parsing behind
the post-switch-swap mesh ping, the in-OSD-down check, unsetting only the
Ceph flags the shutdown added, and the ordering that keeps Nova from seeing
guests stop.
"""

import json
import unittest
from pathlib import Path

from conftest import evaluate, iter_tasks, load_yaml, trusted

PLAYBOOKS = Path(__file__).resolve().parents[1] / "playbooks"


def play(name: str) -> dict:
    return load_yaml(PLAYBOOKS / name)[0]


def task_names(name: str) -> list[str]:
    return [t["name"] for t in iter_tasks(play(name)["tasks"])]


def shared_vars() -> dict:
    return load_yaml(PLAYBOOKS / "vars/cluster-power.yml")


IP_JSON = [
    {"ifname": "lo", "mtu": 65536, "operstate": "UNKNOWN",
     "addr_info": [{"local": "127.0.0.1", "prefixlen": 8}]},
    {"ifname": "eno1", "mtu": 1500, "operstate": "UP",
     "addr_info": [{"local": "192.168.0.21", "prefixlen": 24}]},
    {"ifname": "bond0", "mtu": 9000, "operstate": "UP",
     "addr_info": [{"family": "inet", "local": "192.168.114.11", "prefixlen": 24,
                    "label": "bond0"},
                   {"family": "inet6", "local": "fe80::1", "prefixlen": 64}]},
    {"ifname": "bond0.1113", "mtu": 9000, "operstate": "UP",
     "addr_info": [{"family": "inet", "local": "192.168.113.11", "prefixlen": 24,
                    "label": "bond0.1113"},
                   {"family": "inet", "local": "192.168.113.252", "prefixlen": 32,
                    "label": "bond0.1113"}]},
    {"ifname": "bond0.2116", "mtu": 9000, "operstate": "UP",
     "addr_info": [{"family": "inet6", "local": "fe80::2", "prefixlen": 64}]},
    {"ifname": "bond01", "mtu": 1500, "operstate": "UP",
     "addr_info": [{"family": "inet", "local": "10.9.9.9", "prefixlen": 24,
                    "label": "bond01"}]},
]


class StartupParsingTests(unittest.TestCase):
    def _vars(self) -> dict:
        return {**trusted(play("cluster-startup.yml")["vars"]),
                "cluster_power_ip_raw": {"stdout": json.dumps(IP_JSON)}}

    def test_bond0_interfaces_include_unnumbered_vlans(self) -> None:
        ifaces = evaluate("cluster_power_parsed_ifaces", self._vars())
        self.assertEqual([i["ifname"] for i in ifaces], ["bond0", "bond0.1113", "bond0.2116"])

    def test_host_addresses_skip_vips_and_ipv6(self) -> None:
        addrs = evaluate("cluster_power_host_addrs", self._vars())
        self.assertEqual([a["local"] for a in addrs], ["192.168.114.11", "192.168.113.11"])

    def test_peer_addresses_by_interface(self) -> None:
        variables = self._vars()
        variables["hostvars"] = {"openstack01": {
            "cluster_power_addrs": evaluate("cluster_power_host_addrs", variables)}}
        variables["item"] = ["openstack01", {"ifname": "bond0.1113"}]
        self.assertEqual(evaluate("cluster_power_peer_addrs", variables), ["192.168.113.11"])
        variables["item"] = ["openstack01", {"ifname": "bond0.2116"}]
        self.assertEqual(evaluate("cluster_power_peer_addrs", variables), [])

    def test_ping_failures_include_results_without_rc(self) -> None:
        results = [
            {"item": ["a", {}], "rc": 0},
            {"item": ["b", {}], "rc": 1},
            {"item": ["c", {}], "failed": True, "msg": "timeout"},
            {"item": ["d", {}], "skipped": True},
        ]
        variables = {**self._vars(), "cluster_power_ping": {"results": results}}
        failed = evaluate("cluster_power_ping_failed", variables)
        self.assertEqual(sorted(r["item"][0] for r in failed), ["b", "c"])

    def test_vms_missing_with_several_vms_in_any_order(self) -> None:
        """Order and whitespace of the post-boot virsh list must not matter."""
        variables = {
            **self._vars(),
            "inventory_hostname": "openstack02",
            "cluster_power_state": {"vms": {"openstack02": ["i-1", "i-2", "i-3"]}},
            "cluster_power_vms_raw": {"stdout_lines": ["i-3 ", "i-1", "i-2", ""]},
        }
        self.assertEqual(evaluate("cluster_power_vms_missing", variables), [])
        variables["cluster_power_vms_raw"] = {"stdout_lines": ["i-2"]}
        self.assertEqual(sorted(evaluate("cluster_power_vms_missing", variables)), ["i-1", "i-3"])

    def test_in_osd_down_ignores_out_osds(self) -> None:
        dump = {"osds": [
            {"osd": 0, "up": 1, "in": 1},
            {"osd": 8, "up": 0, "in": 0},  # drained before the shutdown
            {"osd": 9, "up": 0, "in": 1},  # did not come back
        ]}
        variables = {**self._vars(), "cluster_power_osd_dump_raw": {"stdout": json.dumps(dump)}}
        self.assertEqual(evaluate("cluster_power_in_osds_down", variables), [9])


class ShutdownStateTests(unittest.TestCase):
    def test_state_vms_from_compute_hosts(self) -> None:
        task = next(t for t in iter_tasks(play("cluster-shutdown.yml")["tasks"])
                    if t["name"] == "Add the running VMs to the state record")
        expr = task["ansible.builtin.set_fact"]["cluster_power_state"].strip()[2:-2]
        variables = {
            "cluster_power_state": {"vms": {}, "silence_id": ""},
            "groups": {"compute": ["openstack01", "openstack05"]},
            "hostvars": {
                "openstack01": {"cluster_power_vms_raw": {"stdout_lines": ["i-1 ", ""]}},
                "openstack05": {"cluster_power_vms_raw": {"stdout_lines": []}},
            },
        }
        state = evaluate(expr, variables)
        self.assertEqual(json.loads(json.dumps(state["vms"])),
                         {"openstack01": ["i-1"], "openstack05": []})


class StartupGateTests(unittest.TestCase):
    def _allow_warn(self, flags: list[str]) -> bool:
        task = next(t for t in iter_tasks(play("cluster-startup.yml")["tasks"])
                    if t["name"] == "Wait for Ceph health")
        expr = task["vars"]["health_gate_ceph_allow_warn"].strip()[2:-2]
        variables = {**trusted(play("cluster-startup.yml")["vars"]),
                     "cluster_power_state": {"preexisting_ceph_flags": flags}}
        return evaluate(expr, variables)

    def test_permanent_flags_do_not_allow_warn(self) -> None:
        """sortbitwise and friends are always set; they must not let a
        RECENT_CRASH through the startup Ceph gate."""
        self.assertFalse(self._allow_warn(["sortbitwise", "recovery_deletes",
                                           "purged_snapdirs", "pglog_hardlimit"]))
        self.assertTrue(self._allow_warn(["sortbitwise", "noout"]))


class StartupUnitTests(unittest.TestCase):
    def test_unit_names_kept_whole(self) -> None:
        variables = {**trusted(shared_vars()), **trusted(play("cluster-startup.yml")["vars"]),
                     "cluster_power_kolla_units_raw": {"stdout_lines": [
                         "kolla-neutron_server-container.service enabled enabled",
                         "kolla-nova_compute-container.service enabled enabled",
                         "other.service enabled enabled"]}}
        # nova_compute is a held unit: released by its own gated step.
        self.assertEqual(evaluate("cluster_power_kolla_units", variables),
                         ["kolla-neutron_server-container.service"])


class CephFlagTests(unittest.TestCase):
    def test_no_nodown_or_pause(self) -> None:
        """nodown would hide an OSD that never returns from the startup wait."""
        flags = shared_vars()["cluster_power_ceph_flags"]
        self.assertIn("noout", flags)
        self.assertFalse({"nodown", "pause"} & set(flags))

    def test_startup_unsets_only_flags_the_shutdown_added(self) -> None:
        task = next(t for t in iter_tasks(play("cluster-startup.yml")["tasks"])
                    if t["name"].startswith("Unset the Ceph flags"))
        self.assertIn("difference(cluster_power_state.preexisting_ceph_flags)", task["loop"])


class OrderingTests(unittest.TestCase):
    def test_both_plays_stop_every_host_on_any_failure(self) -> None:
        for name in ("cluster-shutdown.yml", "cluster-startup.yml"):
            self.assertTrue(play(name)["any_errors_fatal"], name)

    def test_shutdown_order(self) -> None:
        names = task_names("cluster-shutdown.yml")
        order = [
            "Assert operator approved the shutdown",
            "Create an Alertmanager silence for every alert",
            "Write the state file",
            "Fence the API (stop and disable haproxy)",
            "Stop and disable held Kolla units until startup re-enables them",
            "Record running VMs with virsh list --name --state-running",
            "Rewrite the state file with the VMs",
            "Ask every active guest to shut down with virsh shutdown",
            "Assert no guest is active",
            "Assert no host dropped out of the play",
            "Set Ceph flags with ceph osd set",
            "Re-check Ceph before stopping MariaDB",
            "Stop MariaDB one controller at a time",
            "Assert operator approved power-off",
            "Power off",
        ]
        self.assertEqual(order, sorted(order, key=names.index))

    def test_held_units(self) -> None:
        """Shutdown stops haproxy first (fences the API) and releases in phases."""
        held = shared_vars()["cluster_power_held_units"]
        self.assertEqual(
            [(h["unit"], h["release"]) for h in held],
            [("kolla-haproxy-container.service", "api"),
             ("kolla-nova_compute-container.service", "compute"),
             ("kolla-octavia_health_manager-container.service", "after_vms")],
        )

    def test_startup_order(self) -> None:
        names = task_names("cluster-startup.yml")
        order = [
            "Assert every peer answered at full MTU",
            "Wait for every in OSD to be up",
            "Unset the Ceph flags the shutdown added with ceph osd unset",
            "Wait for Ceph health",
            "Run kolla-ansible mariadb-recovery on the deploy host",
            "Re-enable MariaDB units",
            "Clear failed state of Kolla units",
            "Start every enabled Kolla unit",
            "Gate on Galera, RabbitMQ and OVN",
            "Release the API (haproxy)",
            "Run OpenStack API smoke from the deploy host with openstack endpoint list",
            "Verify every enabled Kolla container is running",
            "Release nova_compute",
            "Assert the recorded VMs are running",
            "Wait for amphorae to boot before releasing the remaining units",
            "Enable and start units held until the VMs are back",
            "Assert every held unit is running again",
            "Archive the state file so the next shutdown starts fresh",
        ]
        self.assertEqual(order, sorted(order, key=names.index))


if __name__ == "__main__":
    unittest.main()
