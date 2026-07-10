"""Structure tests for the health-gate role and per-host hazards metadata.

Mirrors the pytest-over-YAML idiom used by test_ceph_schema.py and
test_live_host_sync.py: parse the YAML/structure on disk and assert the
contract, without touching any live host.
"""

import unittest
from pathlib import Path

from conftest import load_yaml

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ANSIBLE_DIR.parent
ROLE_DIR = ANSIBLE_DIR / "roles/health-gate"
HOST_VARS_DIR = ANSIBLE_DIR / "host_vars"
UPGRADE_PLAYBOOK = ANSIBLE_DIR / "playbooks/upgrade.yml"
REBOOT_PLAYBOOK = ANSIBLE_DIR / "playbooks/reboot.yml"
NETWORK_TASKS = ANSIBLE_DIR / "roles/network/tasks/main.yml"

# Hosts that must carry hazard metadata (every physical managed host).
MANAGED_HOSTS = (
    "openstack06",
    "openstack05",
    "openstack04",
    "openstack02",
    "openstack01",
)


def parse_inventory_group(path: Path, group_name: str) -> list[str]:
    members = []
    current_group = None

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_group = line[1:-1]
            continue
        if current_group == group_name:
            members.append(line.split()[0])

    return members


class HealthGateRoleStructureTests(unittest.TestCase):
    def test_role_has_expected_task_files(self) -> None:
        for name in ("main", "hazards", "galera", "rabbitmq", "ceph", "ovn"):
            task_file = ROLE_DIR / "tasks" / f"{name}.yml"
            self.assertTrue(task_file.exists(), f"missing tasks/{name}.yml")

    def test_role_has_defaults_and_bilingual_readme(self) -> None:
        self.assertTrue((ROLE_DIR / "defaults/main.yml").exists())
        self.assertTrue((ROLE_DIR / "README.md").exists(), "zh-TW README missing")
        self.assertTrue((ROLE_DIR / "README.en.md").exists(), "English README missing")

    def test_defaults_define_all_check_toggles_defaulting_on(self) -> None:
        defaults = load_yaml(ROLE_DIR / "defaults/main.yml")
        for toggle in (
            "health_gate_galera_enabled",
            "health_gate_rabbitmq_enabled",
            "health_gate_ceph_enabled",
            "health_gate_ovn_enabled",
        ):
            self.assertIn(toggle, defaults, toggle)
            self.assertTrue(defaults[toggle], f"{toggle} must default to true")

    def test_defaults_define_retry_and_hazard_knobs(self) -> None:
        defaults = load_yaml(ROLE_DIR / "defaults/main.yml")
        for key in (
            "health_gate_retries",
            "health_gate_delay_seconds",
            "health_gate_consult_hazards",
            "health_gate_ack_hazards",
            "health_gate_target_host",
        ):
            self.assertIn(key, defaults, key)
        # Fail-closed: acknowledging hazards must be opt-in, not the default.
        self.assertFalse(defaults["health_gate_ack_hazards"])
        self.assertTrue(defaults["health_gate_consult_hazards"])

    def test_each_check_guarded_by_its_toggle_in_main(self) -> None:
        main = load_yaml(ROLE_DIR / "tasks/main.yml")
        includes = {
            task["ansible.builtin.include_tasks"]: task.get("when", "")
            for task in main
            if "ansible.builtin.include_tasks" in task
        }
        expected = {
            "galera.yml": "health_gate_galera_enabled",
            "rabbitmq.yml": "health_gate_rabbitmq_enabled",
            "ceph.yml": "health_gate_ceph_enabled",
            "ovn.yml": "health_gate_ovn_enabled",
            "hazards.yml": "health_gate_consult_hazards",
        }
        for include_file, toggle in expected.items():
            self.assertIn(include_file, includes, include_file)
            self.assertIn(toggle, includes[include_file], include_file)

    def test_every_check_file_uses_a_failing_assert(self) -> None:
        """Gates must fail closed: each check file carries an assert task."""
        for name in ("galera", "rabbitmq", "ceph", "ovn", "hazards"):
            text = (ROLE_DIR / "tasks" / f"{name}.yml").read_text()
            self.assertIn(
                "ansible.builtin.assert",
                text,
                f"{name}.yml must assert (fail closed), not just report",
            )
            self.assertIn("fail_msg", text, f"{name}.yml assert needs a reason")

    def test_rabbitmq_check_loops_over_every_node(self) -> None:
        """partition_handling=ignore can mask split-brain from one node's view,
        so the RabbitMQ check must query every cluster node, not just one."""
        text = (ROLE_DIR / "tasks/rabbitmq.yml").read_text()
        self.assertIn("delegate_to", text)
        self.assertIn("loop: \"{{ groups[health_gate_controller_group] }}\"", text)

    def test_ceph_check_uses_ok_to_stop_and_bootstrap_host(self) -> None:
        text = (ROLE_DIR / "tasks/ceph.yml").read_text()
        self.assertIn("ok-to-stop", text)
        self.assertIn("cephadm", text)
        self.assertIn("health_gate_ceph_host", text)

    def test_ceph_health_warn_refused_by_default(self) -> None:
        """Fail closed: accepting HEALTH_WARN must be an explicit opt-in."""
        defaults = load_yaml(ROLE_DIR / "defaults/main.yml")
        self.assertIs(defaults["health_gate_ceph_allow_warn"], False)

    def test_galera_parse_avoids_regex_and_defaults_unsafe(self) -> None:
        """Escape sequences inside a YAML block scalar reach Jinja
        un-unescaped on ansible-core 2.19+, so regex_search patterns with
        backslashes silently match nothing (and crash on the capture-group
        form). The parse must use split-into-dict, and every .get() must
        default to a value the Synced/Primary/ON/size assert rejects."""
        text = (ROLE_DIR / "tasks/galera.yml").read_text()
        self.assertNotIn("regex_search(", text)
        self.assertIn("map('split')", text)
        for key, unsafe in [
            ("WSREP_LOCAL_STATE_COMMENT", "'UNKNOWN'"),
            ("WSREP_CLUSTER_SIZE", "'0'"),
            ("WSREP_CLUSTER_STATUS", "'UNKNOWN'"),
            ("WSREP_READY", "'OFF'"),
        ]:
            self.assertIn(f".get('{key}', {unsafe})", text)

    def test_ovn_check_probes_controller_liveness(self) -> None:
        """A Chassis row outlives a dead ovn-controller, so the gate must also
        ask each daemon for its SB connection status (exact-match 'connected',
        since 'not connected' contains the substring)."""
        text = (ROLE_DIR / "tasks/ovn.yml").read_text()
        self.assertIn("connection-status", text)
        self.assertIn("(item.stdout | trim) == 'connected'", text)

    def test_no_emojis_in_role_files(self) -> None:
        for path in ROLE_DIR.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text()
            self.assertTrue(
                all(ord(ch) < 0x1F000 for ch in text),
                f"emoji-range character found in {path.name}",
            )


class HazardSchemaTests(unittest.TestCase):
    def _hazards_for(self, host: str) -> list:
        data = load_yaml(HOST_VARS_DIR / f"{host}.yml")
        return data.get("host_hazards", [])

    def test_every_managed_host_has_hazards(self) -> None:
        for host in MANAGED_HOSTS:
            hazards = self._hazards_for(host)
            self.assertTrue(hazards, f"{host} must define host_hazards")
            self.assertIsInstance(hazards, list, host)

    def test_hazard_entries_have_required_keys(self) -> None:
        required = ("key", "reason", "source")
        for host in MANAGED_HOSTS:
            for index, entry in enumerate(self._hazards_for(host)):
                with self.subTest(host=host, index=index, key=entry.get("key")):
                    self.assertIsInstance(entry, dict)
                    for key in required:
                        self.assertIn(key, entry, f"{host}[{index}] missing {key}")
                        self.assertTrue(str(entry[key]).strip(), f"{host}[{index}] empty {key}")

    def test_hazard_keys_unique_per_host(self) -> None:
        for host in MANAGED_HOSTS:
            keys = [e["key"] for e in self._hazards_for(host)]
            self.assertEqual(len(keys), len(set(keys)), f"duplicate hazard key in {host}")

    def test_openstack01_bond0_blocks_unattended_reboot(self) -> None:
        hazards = {e["key"]: e for e in self._hazards_for("openstack01")}
        self.assertIn("bond0_boot_failure", hazards)
        self.assertTrue(hazards["bond0_boot_failure"].get("blocks_unattended"))
        self.assertIn("troubleshooting", hazards["bond0_boot_failure"]["source"])

    def test_openstack04_carries_numa_history(self) -> None:
        hazards = {e["key"]: e for e in self._hazards_for("openstack04")}
        self.assertIn("numa_local_oom_history", hazards)
        self.assertIn("numa-oom", hazards["numa_local_oom_history"]["source"])

    def test_canary_tiers_order_06_first_01_last(self) -> None:
        """Canary ordering must place the pure-Ceph node (06) first and the
        cephadm/mgr bootstrap host (01) last."""
        tiers = {}
        for host in MANAGED_HOSTS:
            for entry in self._hazards_for(host):
                if "canary_tier" in entry:
                    tiers[host] = entry["canary_tier"]
        for host in MANAGED_HOSTS:
            self.assertIn(host, tiers, f"{host} missing a canary_tier hazard")
        self.assertEqual(min(tiers, key=tiers.get), "openstack06")
        self.assertEqual(max(tiers, key=tiers.get), "openstack01")

    def test_managed_hosts_inventory_follows_canary_tier_order(self) -> None:
        """reboot.yml uses inventory order, so managed_hosts must be sorted by
        host_vars canary_tier."""
        inventory_order = parse_inventory_group(ANSIBLE_DIR / "hosts", "managed_hosts")
        tiers = {}
        for host in inventory_order:
            for entry in self._hazards_for(host):
                if "canary_tier" in entry:
                    tiers[host] = entry["canary_tier"]

        self.assertEqual(list(MANAGED_HOSTS), inventory_order)
        self.assertEqual(set(tiers), set(inventory_order))
        self.assertEqual(inventory_order, sorted(inventory_order, key=tiers.__getitem__))

    def test_blocking_hazards_only_use_boolean_true(self) -> None:
        for host in MANAGED_HOSTS:
            for entry in self._hazards_for(host):
                if "blocks_unattended" in entry:
                    self.assertIsInstance(
                        entry["blocks_unattended"], bool,
                        f"{host} blocks_unattended must be a bool",
                    )


class UpgradeWiringTests(unittest.TestCase):
    def test_upgrade_invokes_health_gate_before_apt(self) -> None:
        play = load_yaml(UPGRADE_PLAYBOOK)[0]
        self.assertEqual(play["serial"], 1)
        tasks = play["tasks"]
        gate_index = next(
            i for i, t in enumerate(tasks)
            if t.get("ansible.builtin.include_role", {}).get("name") == "health-gate"
        )
        apt_index = next(
            i for i, t in enumerate(tasks)
            if "ansible.builtin.apt" in t and t["ansible.builtin.apt"].get("upgrade") == "dist"
        )
        self.assertLess(gate_index, apt_index, "health gate must run before the apt upgrade")


class RollingRebootPlaybookTests(unittest.TestCase):
    def test_reboot_playbook_is_serial_inventory_ordered_and_attended(self) -> None:
        play = load_yaml(REBOOT_PLAYBOOK)[0]
        self.assertEqual(play["hosts"], "managed_hosts")
        self.assertEqual(play["serial"], 1)
        self.assertEqual(play["order"], "inventory")

        text = REBOOT_PLAYBOOK.read_text()
        self.assertIn("ansible.builtin.include_role", text)
        self.assertIn("name: health-gate", text)
        self.assertIn("ansible.builtin.pause", text)
        self.assertIn("Operator go/no-go", text)
        self.assertIn("ansible.builtin.reboot", text)

    def test_reboot_playbook_covers_planned_safety_checks(self) -> None:
        text = REBOOT_PLAYBOOK.read_text()
        for expected in (
            "uname -r",
            "virsh list --name --state-running",
            "ceph osd set",
            "ceph osd unset",
            "noout",
            "norebalance",
            "wait_for_connection",
            "bond0_boot_failure",
            "ip -j link show bond0",
            "docker ps",
            "openstack endpoint list",
            "reports/rolling-reboot-2026-07-09.log",
        ):
            self.assertIn(expected, text)


class ArmInventoryTests(unittest.TestCase):
    def test_arm_hosts_fully_decommissioned(self) -> None:
        """arm01 was decommissioned; arm03 never existed (fabricated during
        the 2026-07-09 cleanup and removed). No ARM host may linger in
        inventory or host_vars: the health-gate OVN check expects a live
        Chassis for every compute member, so a dead inventory entry blocks
        every campaign."""
        inventory = (ANSIBLE_DIR / "hosts").read_text()
        for host in ("arm01", "arm03"):
            self.assertNotIn(host, inventory)
            self.assertFalse((HOST_VARS_DIR / f"{host}.yml").exists())


class NetworkRefusalTests(unittest.TestCase):
    def test_network_refuses_on_nic_name_mismatch(self) -> None:
        """The network role must assert live NIC names against inventory
        before templating bond0 (structural check, not message wording)."""
        tasks = load_yaml(NETWORK_TASKS)
        nic_asserts = [
            t for t in tasks
            if "ansible.builtin.assert" in t
            and "ansible_facts.interfaces" in str(t["ansible.builtin.assert"].get("that", ""))
        ]
        self.assertTrue(nic_asserts, "network role must assert NIC names exist")
        self.assertTrue(
            all(t["ansible.builtin.assert"].get("fail_msg") for t in nic_asserts),
            "NIC assert needs a fail_msg reason",
        )


if __name__ == "__main__":
    unittest.main()
