"""Tests for the control-plane-alert-collector role and its collector script.

Exercises the script's pure parsers and fail-safe behaviour (no docker needed)
and asserts the role's structural contract, mirroring test_health_gate.py.
"""

import calendar
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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


def _uwsgi_line(request: str, status: str, ts: str = "Sat Jul  4 07:13:09 2026") -> str:
    return (
        f"[pid: 22|app: 0|req: 2274227/11368997] 192.168.113.14 () "
        f"{{36 vars in 461 bytes}} [{ts}] {request} => generated 109 bytes "
        f"in 38 msecs (HTTP/1.1 {status}) 6 headers in 262 bytes (1 switches on core 0)"
    )


# UTC epoch for Sat Jul  4 07:15:00 2026 (uwsgi logs are UTC on this fleet).
_NOW = calendar.timegm(time.strptime("Sat Jul  4 07:15:00 2026", "%a %b %d %H:%M:%S %Y"))


class KeystoneAuthFailureTests(unittest.TestCase):
    def test_counts_in_window_401_posts(self) -> None:
        lines = [_uwsgi_line("POST /v3/auth/tokens", "401")] * 3
        self.assertEqual(cpa.count_keystone_auth_failures(lines, _NOW, 600), 3)

    def test_excludes_401_bytes_false_positive(self) -> None:
        # A successful POST whose byte count happens to be 401 must not match:
        # the regex anchors the status inside its parentheses.
        line = _uwsgi_line("POST /v3/auth/tokens", "201").replace(
            "109 bytes", "401 bytes"
        )
        self.assertEqual(cpa.count_keystone_auth_failures([line], _NOW, 600), 0)

    def test_excludes_get_and_non_401(self) -> None:
        lines = [
            _uwsgi_line("GET /v3/auth/tokens", "401"),  # token validation noise
            _uwsgi_line("POST /v3/auth/tokens", "201"),
            _uwsgi_line("POST /v3/users", "401"),
        ]
        self.assertEqual(cpa.count_keystone_auth_failures(lines, _NOW, 600), 0)

    def test_excludes_out_of_window_timestamps(self) -> None:
        lines = [
            _uwsgi_line("POST /v3/auth/tokens", "401", ts="Sat Jul  4 07:00:00 2026"),
            _uwsgi_line("POST /v3/auth/tokens", "401", ts="Sat Jul  4 07:10:00 2026"),
        ]
        self.assertEqual(cpa.count_keystone_auth_failures(lines, _NOW, 600), 1)

    def test_query_string_variant_also_matches(self) -> None:
        # Some SDK clients request POST /v3/auth/tokens?nocatalog -- a spray
        # using it must still count.
        line = _uwsgi_line("POST /v3/auth/tokens?nocatalog", "401")
        self.assertEqual(cpa.count_keystone_auth_failures([line], _NOW, 600), 1)

    def test_http_1_0_status_also_matches(self) -> None:
        line = _uwsgi_line("POST /v3/auth/tokens", "401").replace("HTTP/1.1", "HTTP/1.0")
        self.assertEqual(cpa.count_keystone_auth_failures([line], _NOW, 600), 1)

    def test_excludes_future_timestamps(self) -> None:
        # Clock skew / malformed future timestamps must not inflate the count.
        line = _uwsgi_line("POST /v3/auth/tokens", "401", ts="Sat Jul  4 08:00:00 2026")
        self.assertEqual(cpa.count_keystone_auth_failures([line], _NOW, 600), 0)

    def test_missing_log_emits_check_failed(self) -> None:
        out = cpa.collect_keystone_auth("openstack01", "/no/such/log", 600, 20000)
        self.assertEqual(out, ['cpa_keystone_auth_check_failed{node="openstack01"} 1'])

    def test_readable_log_emits_count_and_clean_check(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as handle:
            handle.write(_uwsgi_line("POST /v3/auth/tokens", "401") + "\n")
            handle.write(_uwsgi_line("POST /v3/auth/tokens", "201") + "\n")
            path = handle.name
        with mock.patch.object(cpa.time, "time", return_value=_NOW):
            out = cpa.collect_keystone_auth("openstack01", path, 600, 20000)
        self.assertEqual(
            out,
            [
                'cpa_keystone_auth_failures_recent{node="openstack01"} 1',
                'cpa_keystone_auth_check_failed{node="openstack01"} 0',
            ],
        )

    def _write_log(self, path, lines) -> None:
        path.write_text("".join(line + "\n" for line in lines))

    def test_rotation_fallback_reads_rotated_file(self) -> None:
        # Right after logrotate the current file does not cover the window;
        # in-window failures in <log>.1 must still count.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "keystone-uwsgi.log"
            self._write_log(log, [])  # freshly rotated, empty
            self._write_log(
                Path(tmp) / "keystone-uwsgi.log.1",
                [_uwsgi_line("POST /v3/auth/tokens", "401")] * 2,
            )
            with mock.patch.object(cpa.time, "time", return_value=_NOW):
                out = cpa.collect_keystone_auth("openstack01", str(log), 600, 20000)
        self.assertEqual(
            out,
            [
                'cpa_keystone_auth_failures_recent{node="openstack01"} 2',
                'cpa_keystone_auth_check_failed{node="openstack01"} 0',
            ],
        )

    def test_saturated_tail_emits_count_and_check_failed(self) -> None:
        # Tail saturated AND its oldest line still in-window: the window is
        # not fully covered, so the count (a floor) must not be trusted alone.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "keystone-uwsgi.log"
            self._write_log(log, [_uwsgi_line("POST /v3/auth/tokens", "401")] * 5)
            with mock.patch.object(cpa.time, "time", return_value=_NOW):
                out = cpa.collect_keystone_auth("openstack01", str(log), 600, 5)
        self.assertEqual(
            out,
            [
                'cpa_keystone_auth_failures_recent{node="openstack01"} 5',
                'cpa_keystone_auth_check_failed{node="openstack01"} 1',
            ],
        )

    def test_unrecognized_format_emits_check_failed(self) -> None:
        # Format drift must never read as a trusted 0.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "keystone-uwsgi.log"
            self._write_log(log, ["some totally different log format", "another line"])
            out = cpa.collect_keystone_auth("openstack01", str(log), 600, 20000)
        self.assertEqual(out, ['cpa_keystone_auth_check_failed{node="openstack01"} 1'])

    def test_invalid_env_values_emit_check_failed(self) -> None:
        # Bad overrides (non-int, zero, negative) surface as check_failed, not
        # a crash or a trusted zero-line scan.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "keystone-uwsgi.log"
            self._write_log(log, [_uwsgi_line("POST /v3/auth/tokens", "401")])
            for window, tail in (("nope", "20000"), ("0", "20000"), ("600", "-1")):
                out = cpa.collect_keystone_auth("openstack01", str(log), window, tail)
                self.assertEqual(
                    out, ['cpa_keystone_auth_check_failed{node="openstack01"} 1'],
                    (window, tail),
                )


class CollectorFailSafeTests(unittest.TestCase):
    def test_failed_check_emits_failure_not_healthy(self) -> None:
        out = cpa.collect_rabbitmq("openstack01", "no_such_container", 3)
        self.assertEqual(out, ['cpa_rabbitmq_check_failed{node="openstack01"} 1'])

    def test_build_metrics_skips_unconfigured_checks(self) -> None:
        # A compute-only host runs only the local ovn-controller probe + last-run.
        # Patch run_cmd so this validates routing on the success path, not a live
        # docker exec (the fail-safe path has its own test above).
        env = {"CPA_NODE": "openstack05", "CPA_OVN_CONTROLLER_CONTAINER": "ovn_controller"}
        with mock.patch.object(cpa, "run_cmd", return_value="connected\n"):
            body = "\n".join(line for line in cpa.build_metrics(env) if not line.startswith("#"))
        self.assertNotIn("cpa_rabbitmq", body)
        self.assertNotIn("cpa_ovn_chassis", body)
        self.assertIn('cpa_ovn_controller_connected{node="openstack05"} 1', body)
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
        # Keystone log check is controller-only (the log exists only there).
        self.assertIn("CPA_KEYSTONE_LOG", text)
        # Minimal hardening on a root unit that execs into privileged containers.
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("PrivateTmp=true", text)

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
