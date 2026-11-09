"""Rolling-reboot gate contract (playbooks/reboot.yml + health-gate role).

Pins the fixes from the 2026-11 audit without touching live hosts:
Kolla-aware container baseline that also runs on a resumed campaign, OVN
re-check (chassis + NB/SB raft) after every OVN host's reboot, Ceph mon/osd
ok-to-stop, per-attempt timeouts, noout only after GO, and a Ceph health
comparison that a MON_DOWN/OSD_DOWN warning cannot slip through.
"""

import json
import unittest
from pathlib import Path

from conftest import evaluate, iter_tasks, load_yaml, trusted

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
REBOOT_PLAYBOOK = ANSIBLE_DIR / "playbooks/reboot.yml"
CONTAINER_TASKS = ANSIBLE_DIR / "playbooks/tasks/reboot-containers.yml"
ROLE_DIR = ANSIBLE_DIR / "roles/health-gate"
COMMAND_MODULES = ("ansible.builtin.command", "ansible.builtin.shell")


def reboot_play() -> dict:
    return load_yaml(REBOOT_PLAYBOOK)[0]


def reboot_tasks() -> dict[str, dict]:
    return {t["name"]: t for t in iter_tasks(reboot_play()["tasks"])}


def role_defaults() -> dict:
    return load_yaml(ROLE_DIR / "defaults/main.yml")


def role_tasks(name: str) -> dict[str, dict]:
    return {t["name"]: t for t in load_yaml(ROLE_DIR / "tasks" / f"{name}.yml")}


UNITS = [
    "kolla-nova_compute-container.service enabled enabled",
    "kolla-ovn_controller-container.service enabled enabled",
    "kolla-fluentd-container.service enabled enabled",
]
INSPECT = [
    "/nova_compute no",
    "/ovn_controller no",
    "/fluentd no",
    "/gpu_exporter always",  # non-Kolla, persistent via docker restart policy
    "/eager_turing no",  # ephemeral check container: no unit, no policy
    "/neutron_ovn_metadata_agent_haproxy_ovnmeta-1234 no",
]


class ContainerBaselineTests(unittest.TestCase):
    def _vars(self) -> dict:
        return {
            **trusted(reboot_play()["vars"]),
            "reboot_kolla_units_raw": {"stdout_lines": UNITS},
            "reboot_running_containers_raw": {"stdout_lines": INSPECT},
        }

    def test_kolla_containers_with_restart_policy_no_are_counted(self) -> None:
        """Kolla runs containers with restart policy "no" under
        kolla-<name>-container.service units; the old filter kept only
        restart-policy containers, so the 2026-10-22 baseline was 1."""
        baseline = evaluate("reboot_persistent_running", self._vars())
        self.assertEqual(
            sorted(baseline), ["fluentd", "gpu_exporter", "nova_compute", "ovn_controller"]
        )

    def test_unit_parse(self) -> None:
        units = evaluate("reboot_kolla_unit_containers", self._vars())
        self.assertEqual(sorted(units), ["fluentd", "nova_compute", "ovn_controller"])
        self.assertEqual(
            evaluate("reboot_kolla_unit_containers", {
                **trusted(reboot_play()["vars"]), "reboot_kolla_units_raw": {"stdout_lines": []},
            }),
            [],
        )

    def test_restart_policy_filter_removed_from_shell(self) -> None:
        self.assertNotIn('ne .HostConfig.RestartPolicy.Name "no"', REBOOT_PLAYBOOK.read_text())

    def test_units_listed_and_sanity_checked_for_every_host(self) -> None:
        tasks = reboot_tasks()
        top = [t["name"] for t in reboot_play()["tasks"]]
        listing = next(n for n in top if tasks[n].get("register") == "reboot_kolla_units_raw")
        argv = tasks[listing]["ansible.builtin.command"]["argv"]
        self.assertIn("kolla-*-container.service", argv)
        self.assertIn("--state=enabled", argv)
        guard = next(
            t for t in tasks.values()
            if "reboot_kolla_unit_containers | length > 0" in str(t.get("ansible.builtin.assert", ""))
        )
        self.assertIn("compute", str(guard["when"]))

    def test_resumed_run_verifies_containers_on_already_rebooted_host(self) -> None:
        """A host rebooted by an aborted earlier run is skipped as
        already-current; its container check must still run."""
        top = reboot_play()["tasks"]
        includes = [
            t for t in iter_tasks(top)
            if t.get("ansible.builtin.include_tasks") == "tasks/reboot-containers.yml"
        ]
        self.assertEqual(len(includes), 2, "post-reboot and resume paths")
        resume = next(t for t in includes if t.get("when") == "reboot_old_kernel == reboot_target_kernel")
        self.assertIn("reboot_kolla_unit_containers", resume["vars"]["reboot_expected_containers"])
        post = next(t for t in includes if t is not resume)
        self.assertIn("reboot_pre_containers", post["vars"]["reboot_expected_containers"])
        names = [t["name"] for t in top]
        self.assertLess(names.index(resume["name"]), names.index("Log skipped host"))

    def test_container_task_file_uses_expected_set(self) -> None:
        text = CONTAINER_TASKS.read_text()
        self.assertIn("reboot_expected_containers", text)
        self.assertNotIn("reboot_pre_containers", text)

    def test_container_assert_rejects_a_failed_query(self) -> None:
        """An empty expected set (Ceph-only host on resume) must not pass
        when the docker query itself failed or timed out."""
        tasks = {t["name"]: t for t in load_yaml(CONTAINER_TASKS)}
        that = tasks["Assert expected container set is running"]["ansible.builtin.assert"]["that"]

        def ok(raw: dict, expected: list[str], running: list[str]) -> bool:
            env = {"reboot_post_containers_raw": raw, "reboot_expected_containers": expected,
                   "reboot_post_containers": running}
            return all(evaluate(cond, env) for cond in that)

        self.assertTrue(ok({"rc": 0}, [], []))
        self.assertTrue(ok({"rc": 0}, ["nova_compute"], ["nova_compute"]))
        self.assertFalse(ok({"failed": True, "msg": "Timed out"}, [], []))
        self.assertFalse(ok({"rc": 0}, ["nova_compute"], []))


class FlagAndHandlerTests(unittest.TestCase):
    def test_force_handlers(self) -> None:
        self.assertIs(reboot_play()["force_handlers"], True)

    def test_ceph_flags_set_only_after_operator_go(self) -> None:
        names = list(reboot_tasks())
        go = names.index("Assert operator approved this host reboot")
        self.assertLess(go, names.index("Mark Ceph flags as set"))
        self.assertLess(go, names.index("Set Ceph noout and norebalance flags with ceph osd set"))
        self.assertLess(
            names.index("Set Ceph noout and norebalance flags with ceph osd set"),
            names.index("Reboot into pending kernel"),
        )


class TimeoutTests(unittest.TestCase):
    def test_every_command_in_reboot_has_a_timeout(self) -> None:
        tasks = list(iter_tasks(reboot_play()["tasks"])) + load_yaml(CONTAINER_TASKS)
        for task in tasks:
            if any(m in task for m in COMMAND_MODULES):
                with self.subTest(task=task["name"]):
                    self.assertIn("timeout", task)

    def test_every_health_gate_command_has_a_timeout(self) -> None:
        for path in sorted((ROLE_DIR / "tasks").glob("*.yml")):
            for task in load_yaml(path):
                if any(m in task for m in COMMAND_MODULES):
                    with self.subTest(file=path.name, task=task["name"]):
                        self.assertIn("timeout", task)
        self.assertGreater(role_defaults()["health_gate_command_timeout_seconds"], 0)


class OvnRegateTests(unittest.TestCase):
    def test_post_reboot_regate_covers_ovn_on_every_ovn_host(self) -> None:
        regate = next(
            t for t in iter_tasks(reboot_play()["tasks"])
            if t.get("ansible.builtin.include_role", {}).get("name") == "health-gate"
            and t.get("vars", {}).get("health_gate_galera_wait_for_sync")
        )
        self.assertNotIn("health_gate_ovn_enabled", regate["vars"])
        self.assertIn("compute", str(regate["when"]))
        self.assertIn("controller", str(regate["vars"]["health_gate_galera_enabled"]))
        self.assertIn("controller", str(regate["vars"]["health_gate_rabbitmq_enabled"]))

    def test_raft_check_wired_and_on_by_default(self) -> None:
        main = load_yaml(ROLE_DIR / "tasks/main.yml")
        include = next(t for t in main if t.get("ansible.builtin.include_tasks") == "ovn_raft.yml")
        self.assertIn("health_gate_ovn_raft_enabled", str(include["when"]))
        self.assertIs(role_defaults()["health_gate_ovn_raft_enabled"], True)
        dbs = {d["db"] for d in role_defaults()["health_gate_ovn_raft_dbs"]}
        self.assertEqual(dbs, {"OVN_Northbound", "OVN_Southbound"})

    def _raft_ok(self, lines: list[str], size: int = 3) -> bool:
        result = evaluate("health_gate_ovn_raft_healthy", {
            **trusted(role_defaults()),
            "_health_gate_ovn_raft_lines": lines,
            "health_gate_ovn_raft_expected_size": size,
        })
        self.assertIsInstance(result, bool)
        return result

    def test_raft_status_predicate(self) -> None:
        healthy = [
            "1a2b",
            "Name: OVN_Northbound",
            "Cluster ID: 3c4d (3c4d5e6f-0000-0000-0000-000000000000)",
            "Server ID: 1a2b (1a2b0000-0000-0000-0000-000000000000)",
            "Address: tcp:10.0.0.11:6643",
            "Status: cluster member",
            "Role: follower",
            "Term: 7",
            "Leader: 5e6f",
            "Vote: 5e6f",
            "",
            "Connections: ->5e6f ->7a8b <-5e6f <-7a8b",
            "Disconnections: 0",
            "Servers:",
            "    1a2b (1a2b at tcp:10.0.0.11:6643) (self)",
            "    5e6f (5e6f at tcp:10.0.0.12:6643) last msg 120 ms ago",
            "    7a8b (7a8b at tcp:10.0.0.14:6643)",
        ]
        self.assertTrue(self._raft_ok(healthy))
        leader = [ln.replace("Role: follower", "Role: leader").replace("Leader: 5e6f", "Leader: self")
                  for ln in healthy]
        self.assertTrue(self._raft_ok(leader))
        self.assertFalse(self._raft_ok([ln.replace("Leader: 5e6f", "Leader: unknown") for ln in healthy]))
        self.assertFalse(self._raft_ok([ln.replace("Role: follower", "Role: candidate") for ln in healthy]))
        self.assertFalse(self._raft_ok(
            [ln.replace("cluster member", "disconnected from the cluster (election timeout)")
             for ln in healthy]
        ))
        self.assertFalse(self._raft_ok(healthy[:-1]))  # a member dropped out
        self.assertFalse(self._raft_ok([]))
        # Partial partition: this member has lost its link to 7a8b (in
        # either direction) although 7a8b is still a configured server, so
        # losing the leader next could break quorum.
        for conns in ("Connections: ->5e6f ->7a8b <-5e6f",
                      "Connections: ->5e6f <-5e6f <-7a8b",
                      "Connections: ->5e6f <-5e6f"):
            lines = [conns if ln.startswith("Connections:") else ln for ln in healthy]
            self.assertFalse(self._raft_ok(lines), conns)
        self.assertFalse(self._raft_ok([ln for ln in healthy if not ln.startswith("Connections:")]))

    def test_raft_query_loops_every_controller_and_both_dbs(self) -> None:
        task = next(iter(role_tasks("ovn_raft").values()))
        self.assertIn("groups[health_gate_controller_group]", task["loop"])
        self.assertIn("health_gate_ovn_raft_dbs", task["loop"])
        self.assertIn("cluster/status", task["ansible.builtin.command"]["argv"])
        self.assertIn("health_gate_ovn_raft_healthy", task["until"])

    def test_connection_status_waits_for_connected(self) -> None:
        probe = role_tasks("ovn")["Probe ovn-controller SB connection on every expected chassis host"]
        self.assertIn("'connected'", probe["until"])


class CephGateTests(unittest.TestCase):
    def _acceptable(self, status: str, checks: list[str], allow_warn: bool) -> bool:
        report = {"status": status, "checks": {c: {"severity": "HEALTH_WARN"} for c in checks}}
        result = evaluate("health_gate_ceph_report_acceptable", {
            **trusted(role_defaults()),
            "_health_gate_ceph_report": report,
            "health_gate_ceph_allow_warn": allow_warn,
        })
        self.assertIsInstance(result, bool)
        return result

    def test_allow_warn_never_admits_a_down_daemon(self) -> None:
        self.assertTrue(self._acceptable("HEALTH_OK", [], allow_warn=False))
        self.assertFalse(self._acceptable("HEALTH_WARN", ["OSDMAP_FLAGS"], allow_warn=False))
        self.assertTrue(self._acceptable("HEALTH_WARN", ["OSDMAP_FLAGS"], allow_warn=True))
        for code in ("MON_DOWN", "OSD_DOWN", "OSD_HOST_DOWN", "PG_DEGRADED", "PG_AVAILABILITY"):
            self.assertFalse(
                self._acceptable("HEALTH_WARN", ["OSDMAP_FLAGS", code], allow_warn=True), code
            )
        self.assertFalse(self._acceptable("HEALTH_ERR", [], allow_warn=True))

    def test_baseline_check_codes_recorded(self) -> None:
        parse = role_tasks("ceph")["Store Ceph health status and check codes"]["ansible.builtin.set_fact"]
        self.assertIn("health_gate_ceph_checks", parse)

    def test_mon_ok_to_stop(self) -> None:
        tasks = role_tasks("ceph")
        listing = tasks["List OSDs and monitors per host from the cluster map"]
        self.assertEqual(listing["ansible.builtin.command"]["argv"][-4:], ["node", "ls", "--format", "json"])
        parse = tasks["Parse OSD and monitor ids on the target host"]["ansible.builtin.set_fact"]
        nodes = json.dumps({"osd": {"openstack02": [3, 4]}, "mon": {"openstack02": ["openstack02"]}})
        for key, want_02, want_06 in (("health_gate_target_osds", [3, 4], []),
                                       ("health_gate_target_mons", ["openstack02"], [])):
            for host, want in (("openstack02", want_02), ("openstack06", want_06)):
                got = evaluate(parse[key].strip().strip("{} "), {
                    "_health_gate_ceph_nodes": {"stdout": nodes}, "health_gate_target_host": host,
                })
                self.assertEqual(got, want, (key, host))
        check = tasks["Check ceph mon ok-to-stop for the target host's monitors"]
        self.assertIn("'mon', 'ok-to-stop'", check["ansible.builtin.command"]["argv"])
        self.assertIn("health_gate_target_mons | length > 0", check["when"])
        assertion = tasks["Assert the target host's monitors are ok-to-stop"]
        that = assertion["ansible.builtin.assert"]["that"]
        self.assertIn("_health_gate_ceph_mon_ok_to_stop.rc | default(1) == 0", that)

    def test_post_reboot_health_rejects_new_warnings(self) -> None:
        """A HEALTH_WARN baseline (allow_warn) must not let a MON_DOWN or
        OSD_DOWN raised by the reboot pass the 'no worse than before' wait."""
        play = reboot_play()
        task = reboot_tasks()["Wait for Ceph health to return to the pre-reboot level"]

        def passes(now: str, checks: list[str], baseline: list[str]) -> bool:
            body = {
                "health": {"status": now, "checks": {c: {} for c in checks}},
                "pgmap": {"num_pgs": 1, "pgs_by_state": [{"state_name": "active+clean", "count": 1}]},
            }
            result = evaluate(task["until"], {
                **trusted(play["vars"]), **trusted(task.get("vars", {})),
                "reboot_ceph_status_raw": {"rc": 0, "stdout": json.dumps(body)},
                "health_gate_ceph_status": "HEALTH_WARN" if baseline else "HEALTH_OK",
                "health_gate_ceph_checks": baseline,
            })
            self.assertIsInstance(result, bool)
            return result

        self.assertTrue(passes("HEALTH_WARN", ["POOL_NO_REDUNDANCY"], ["POOL_NO_REDUNDANCY"]))
        self.assertTrue(passes("HEALTH_OK", [], ["POOL_NO_REDUNDANCY"]))
        self.assertFalse(passes("HEALTH_WARN", ["POOL_NO_REDUNDANCY", "MON_DOWN"], ["POOL_NO_REDUNDANCY"]))
        self.assertFalse(passes("HEALTH_WARN", ["OSD_DOWN"], ["POOL_NO_REDUNDANCY"]))
        self.assertFalse(passes("HEALTH_WARN", ["MON_DOWN"], []))
        # A mon-only host (no OSDs) still waits for its mon to rejoin.
        when = task["when"]
        self.assertTrue(evaluate(when, {"health_gate_target_osds": [],
                                        "health_gate_target_mons": ["openstack02"]}))
        self.assertFalse(evaluate(when, {"health_gate_target_osds": [],
                                         "health_gate_target_mons": []}))

    def test_unit_listing_tolerates_no_match(self) -> None:
        listing = next(
            t for t in iter_tasks(reboot_play()["tasks"])
            if t.get("register") == "reboot_kolla_units_raw"
        )
        for rc, failed in ((0, False), (1, False), (4, True)):
            self.assertIs(
                evaluate(listing["failed_when"], {"reboot_kolla_units_raw": {"rc": rc}}), failed
            )


if __name__ == "__main__":
    unittest.main()
