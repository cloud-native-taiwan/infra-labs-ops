"""Tests for the control-plane-alert-collector role and its collector script.

Exercises the script's pure parsers and fail-safe behaviour (no docker needed)
and asserts the role's structural contract, mirroring test_health_gate.py.
"""

import importlib.util
import unittest
from pathlib import Path

from conftest import load_yaml

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
ROLE_DIR = ANSIBLE_DIR / "roles/control-plane-alert-collector"
SCRIPT = ROLE_DIR / "files/control_plane_alert_collector.py"
HEALTH_GATE_DEFAULTS = ANSIBLE_DIR / "roles/health-gate/defaults/main.yml"


def _load_collector():
    spec = importlib.util.spec_from_file_location("cpa_collector", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cpa = _load_collector()


class CollectorParserTests(unittest.TestCase):
    def test_rabbitmq_clean_cluster(self) -> None:
        self.assertEqual(
            cpa.parse_rabbitmq('{"running_nodes":["a","b","c"],"partitions":[]}'),
            (3, 0),
        )

    def test_rabbitmq_partitioned_view(self) -> None:
        # The partition_handling=ignore case: a minority node sees fewer running
        # nodes and a non-empty partitions list.
        self.assertEqual(
            cpa.parse_rabbitmq('{"running_nodes":["a"],"partitions":["b"]}'),
            (1, 1),
        )

    def test_rabbitmq_schema_change_refuses(self) -> None:
        # A formatter schema change must raise, not pass vacuously healthy.
        with self.assertRaises(ValueError):
            cpa.parse_rabbitmq('{"nodes":["a","b","c"]}')

    def test_ovn_chassis_all_live(self) -> None:
        expected = ["openstack01", "openstack02", "openstack04", "openstack05"]
        self.assertEqual(
            cpa.parse_ovn_chassis(
                "openstack01\nopenstack02\nopenstack04\nopenstack05\n", expected
            ),
            (0, 0),
        )

    def test_ovn_chassis_missing_and_stale(self) -> None:
        expected = ["openstack01", "openstack02", "openstack04", "openstack05"]
        # openstack05 missing; openstack99 is a stale row.
        self.assertEqual(
            cpa.parse_ovn_chassis(
                "openstack01\nopenstack02\nopenstack04\nopenstack99\n", expected
            ),
            (1, 1),
        )

    def test_ovn_connection_exact_match(self) -> None:
        self.assertTrue(cpa.parse_ovn_connection("connected\n"))
        # "not connected" contains the substring -- must not pass.
        self.assertFalse(cpa.parse_ovn_connection("not connected\n"))


class CollectorFailSafeTests(unittest.TestCase):
    def test_failed_check_emits_failure_not_healthy(self) -> None:
        out = cpa.collect_rabbitmq("openstack01", "no_such_container", 3)
        self.assertEqual(out, ['cpa_rabbitmq_check_failed{node="openstack01"} 1'])

    def test_build_metrics_skips_unconfigured_checks(self) -> None:
        # A compute-only host: only the local ovn-controller probe + last-run.
        env = {"CPA_NODE": "openstack05", "CPA_OVN_CONTROLLER_CONTAINER": "ovn_controller"}
        body = "\n".join(line for line in cpa.build_metrics(env) if not line.startswith("#"))
        self.assertNotIn("cpa_rabbitmq", body)
        self.assertNotIn("cpa_ovn_chassis", body)
        self.assertIn("cpa_ovn_controller", body)
        self.assertIn("cpa_collector_last_run_timestamp_seconds", body)

    def test_self_check_passes(self) -> None:
        self.assertEqual(cpa.self_check(), 0)


class CollectorRoleStructureTests(unittest.TestCase):
    def test_role_layout(self) -> None:
        for rel in (
            "defaults/main.yml",
            "tasks/main.yml",
            "handlers/main.yml",
            "templates/control-plane-alert-collector.service.j2",
            "templates/control-plane-alert-collector.timer.j2",
            "files/control_plane_alert_collector.py",
            "README.md",
            "README.en.md",
        ):
            self.assertTrue((ROLE_DIR / rel).exists(), f"missing {rel}")

    def test_defaults_mirror_health_gate_container_names(self) -> None:
        """R10: container knobs must match health-gate so the two stay in sync."""
        defaults = load_yaml(ROLE_DIR / "defaults/main.yml")
        hg = load_yaml(HEALTH_GATE_DEFAULTS)
        self.assertEqual(defaults["cpa_rabbitmq_container"], hg["health_gate_rabbitmq_container"])
        self.assertEqual(defaults["cpa_ovn_sb_container"], hg["health_gate_ovn_sb_container"])
        self.assertEqual(
            defaults["cpa_ovn_controller_container"], hg["health_gate_ovn_controller_container"]
        )

    def test_service_template_is_host_aware(self) -> None:
        text = (ROLE_DIR / "templates/control-plane-alert-collector.service.j2").read_text()
        for var in (
            "CPA_NODE",
            "CPA_OUTPUT_FILE",
            "CPA_EXPECTED_CHASSIS",
            "CPA_RABBITMQ_EXPECTED_SIZE",
            "CPA_OVN_CONTROLLER_CONTAINER",
        ):
            self.assertIn(var, text, var)
        # RabbitMQ / OVN SB checks are gated on controller-group membership.
        self.assertIn("in groups[cpa_controller_group]", text)
        self.assertIn("CPA_RABBITMQ_CONTAINER", text)
        self.assertIn("CPA_OVN_SB_CONTAINER", text)

    def test_tasks_self_test_before_enabling_timer(self) -> None:
        text = (ROLE_DIR / "tasks/main.yml").read_text()
        self.assertIn("--check", text, "must self-test the script before scheduling")
        self.assertIn("control-plane-alert-collector.timer", text)

    def test_no_emojis_in_role_files(self) -> None:
        for path in ROLE_DIR.rglob("*"):
            if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
                continue
            text = path.read_text()
            self.assertTrue(
                all(ord(ch) < 0x1F000 for ch in text),
                f"emoji-range character found in {path.name}",
            )


if __name__ == "__main__":
    unittest.main()
