"""Network changes must roll one host at a time with a health wait between.

The network role's handlers carry `throttle: 1`, but throttle only limits
concurrency inside one task: without `serial`, the linear strategy still runs
every host's networking restart back-to-back with no health check between
them. These tests pin the rollout contract structurally (no live hosts).
"""

import json
import unittest
from pathlib import Path

from conftest import evaluate, iter_tasks, load_yaml, role_names, trusted

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
PLAYBOOK_DIR = ANSIBLE_DIR / "playbooks"
NETWORK_DIR = ANSIBLE_DIR / "roles/network"
HEALTH_GATE_DIR = ANSIBLE_DIR / "roles/health-gate"


def plays_applying_network() -> list[tuple[str, dict]]:
    found = []
    for path in sorted(PLAYBOOK_DIR.glob("*.yml")):
        for play in load_yaml(path) or []:
            roles = role_names(play)
            included = [
                t.get(key, {}).get("name")
                for t in iter_tasks(play.get("tasks", []) + play.get("pre_tasks", []))
                for key in ("ansible.builtin.include_role", "ansible.builtin.import_role")
            ]
            if "network" in roles + included:
                found.append((path.name, play))
    return found


def network_tasks() -> list[dict]:
    return load_yaml(NETWORK_DIR / "tasks/main.yml")


def gate_includes(tasks: list[dict]) -> list[tuple[int, dict]]:
    return [
        (i, t) for i, t in enumerate(tasks)
        if t.get("ansible.builtin.include_role", {}).get("name") == "health-gate"
    ]


class NetworkPlaySerialTests(unittest.TestCase):
    def test_network_is_applied_somewhere(self) -> None:
        self.assertTrue(plays_applying_network(), "expected bootstrap.yml to apply network")

    def test_every_play_applying_network_is_serial_one(self) -> None:
        for name, play in plays_applying_network():
            with self.subTest(playbook=name, play=play.get("name")):
                self.assertEqual(play.get("serial"), 1)
                self.assertEqual(play.get("order"), "inventory")
                # A failed gate must stop the whole run, not let later plays
                # carry on against hosts whose rollout never happened.
                self.assertIs(play.get("any_errors_fatal"), True)

    def test_network_play_applies_only_the_network_role(self) -> None:
        """serial: 1 is paid only for the network step; routine roles keep
        running in parallel in their own plays."""
        for name, play in plays_applying_network():
            with self.subTest(playbook=name):
                self.assertEqual(role_names(play), ["network"])


class NetworkRoleGateTests(unittest.TestCase):
    def test_serial_guard_rejects_parallel_batches(self) -> None:
        """Even if some future play forgets serial: 1, the role refuses to
        restart networking while other hosts share its batch."""
        guard = next(
            t for t in network_tasks()
            if "ansible_play_batch" in str(t.get("ansible.builtin.assert", {}))
        )
        expr = guard["ansible.builtin.assert"]["that"][0]
        self.assertTrue(evaluate(expr, {"ansible_play_batch": ["openstack06"]}))
        self.assertFalse(evaluate(expr, {"ansible_play_batch": ["openstack06", "openstack05"]}))
        self.assertIn("network_bond0_pending is changed", str(guard["when"]))

    def test_pending_change_is_detected_without_writing(self) -> None:
        tasks = {t["name"]: t for t in network_tasks()}
        probe = next(t for t in tasks.values() if t.get("register") == "network_bond0_pending")
        self.assertIs(probe["check_mode"], True)
        deploy = tasks["Deploy bond0 template"]
        self.assertEqual(probe["ansible.builtin.template"]["src"], deploy["ansible.builtin.template"]["src"])
        self.assertEqual(probe["ansible.builtin.template"]["dest"], deploy["ansible.builtin.template"]["dest"])

    def test_gate_before_restart_and_wait_after(self) -> None:
        tasks = network_tasks()
        names = [t.get("name") for t in tasks]
        deploy = names.index("Deploy bond0 template")
        flush = next(i for i, t in enumerate(tasks) if t.get("ansible.builtin.meta") == "flush_handlers")
        gates = gate_includes(tasks)
        self.assertEqual(len(gates), 2, "one pre-restart gate, one post-restart wait")
        (pre_i, pre), (post_i, post) = gates
        self.assertLess(pre_i, deploy)
        self.assertLess(deploy, flush)
        self.assertLess(flush, post_i)
        for task in (pre, post):
            when = " ".join(task["when"])
            self.assertIn("network_health_gate_enabled", when)
            self.assertIn("not ansible_check_mode", when)
        self.assertIn("network_bond0_pending is changed", " ".join(pre["when"]))
        self.assertIn("network_bond0_deployed is changed", " ".join(post["when"]))
        # Post-restart is a wait for convergence, not a fail-fast snapshot.
        self.assertIs(post["vars"]["health_gate_galera_wait_for_sync"], True)
        self.assertIs(post["vars"]["health_gate_ceph_wait_for_health"], True)
        # Hazards are consulted before the restart (openstack01 bond0).
        self.assertNotIn("health_gate_consult_hazards", pre.get("vars", {}))

    def test_gate_defaults_on(self) -> None:
        defaults = load_yaml(NETWORK_DIR / "defaults/main.yml")
        self.assertIs(defaults["network_health_gate_enabled"], True)

    def test_handlers_wait_has_timeout(self) -> None:
        handlers = {h["name"]: h for h in load_yaml(NETWORK_DIR / "handlers/main.yml")}
        wait = handlers["Wait For Connection After Network Restart"]
        self.assertGreater(wait["ansible.builtin.wait_for_connection"]["timeout"], 0)


class CephWaitModeTests(unittest.TestCase):
    """health_gate_ceph_wait_for_health turns the Ceph health query into a
    poll that waits for an acceptable status instead of failing on the first
    reading (used after a network restart, when OSD heartbeats recover)."""

    def _health_task(self) -> dict:
        return next(
            t for t in load_yaml(HEALTH_GATE_DIR / "tasks/ceph.yml")
            if t["name"] == "Check Ceph cluster health"
        )

    def _done(self, status: str, wait: bool, rc: int = 0) -> bool:
        defaults = load_yaml(HEALTH_GATE_DIR / "defaults/main.yml")
        task = self._health_task()
        result = evaluate(task["until"], {
            **trusted(defaults),
            **trusted(task.get("vars", {})),
            "_health_gate_ceph_health": {
                "rc": rc, "stdout": json.dumps({"status": status, "checks": {}}),
            },
            "health_gate_ceph_wait_for_health": wait,
            "health_gate_ceph_allow_warn": False,
        })
        self.assertIsInstance(result, bool)
        return result

    def test_default_only_retries_failed_query(self) -> None:
        self.assertTrue(self._done("HEALTH_WARN", wait=False))
        self.assertFalse(self._done("HEALTH_OK", wait=False, rc=1))

    def test_wait_mode_polls_until_acceptable(self) -> None:
        self.assertFalse(self._done("HEALTH_WARN", wait=True))
        self.assertFalse(self._done("HEALTH_ERR", wait=True))
        self.assertTrue(self._done("HEALTH_OK", wait=True))
        defaults = load_yaml(HEALTH_GATE_DIR / "defaults/main.yml")
        self.assertIs(defaults["health_gate_ceph_wait_for_health"], False)

    def test_wait_mode_retries_ok_to_stop(self) -> None:
        task = next(
            t for t in load_yaml(HEALTH_GATE_DIR / "tasks/ceph.yml")
            if t["name"] == "Check ceph osd ok-to-stop for the target host's OSDs"
        )
        self.assertIn("health_gate_ceph_wait_for_health", str(task["retries"]))
        self.assertIn("_health_gate_ceph_ok_to_stop.rc == 0", task["until"])


if __name__ == "__main__":
    unittest.main()
