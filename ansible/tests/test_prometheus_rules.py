"""Tests for the Prometheus alert rule files under kolla/config/prometheus/.

Mirrors the pytest-over-YAML idiom of test_kolla_structure.py / test_health_gate.py:
parse the rule and test YAML on disk and assert the contract without touching a
live host. When `promtool` is available it additionally runs the upstream rule
linter and the unit-test cases; when it is not, those two checks skip (the rule
files are not used anywhere else in the repo, so promtool is an optional dev/CI
dependency, called out in the plan's U1 verification).

Only the locally-authored rule files are governed by the R7/R11 contract below
(warn-first severities, a promtool case per alert). The upstream-derived
ceph-alerts.rules / node-exporter-alerts.rules ship their own criticals, so
they are out of that contract; they are in PINNED_RULES instead, whose
<name>.test.yml pins only the rules fixed locally. Add a new file to
GOVERNED_RULES (and drop a matching <name>.test.yml in) to bring it under the
full contract.

DeadRuleLintTests runs over every *.rules file, promtool or not, and rejects
patterns that can never match on this deployment (see its docstrings).
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from conftest import load_yaml

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ANSIBLE_DIR.parent
RULES_DIR = REPO_ROOT / "kolla/config/prometheus"
TEST_DIR = ANSIBLE_DIR / "tests/prometheus_rules"

# Rule files we author and hold to the warn-first / tested-alert contract.
GOVERNED_RULES = (
    "control-plane-alerts.rules",
    "libvirt-alerts.rules",
    "blackbox-alerts.rules",
    "watchdog-alerts.rules",
    "period-reconcile-alerts.rules",
)
RULE_FILES = tuple(RULES_DIR / name for name in GOVERNED_RULES)

# Upstream-derived rule files with partial promtool coverage: promtool must
# accept the file and its test file must pass. Each maps to the alerts fixed
# locally; the explicit set (deliberately repeating the test files' alertnames)
# stops a deleted case from passing silently when promtool is absent.
PINNED_RULES = {
    "node-exporter-alerts.rules": frozenset({
        "HostMemoryUnderMemoryPressure", "HostUnusualDiskReadLatency",
        "HostUnusualDiskWriteLatency", "HostOomKillDetected",
        "HostEdacCorrectableErrorsDetected", "HostNetworkInterfaceSaturated",
        "HostOutOfInodes", "HostInodesWillFillIn24Hours",
    }),
    "ceph-alerts.rules": frozenset({
        "CephMgrPrometheusModuleInactive", "CephNodeNetworkPacketDrops",
        "CephNodeNetworkPacketErrors",
    }),
}
PROMTOOL_RULE_FILES = RULE_FILES + tuple(RULES_DIR / name for name in PINNED_RULES)
ALL_RULE_FILES = tuple(sorted(RULES_DIR.glob("*.rules")))

ALLOWED_SEVERITIES = {"info", "warning", "critical"}

GLOBALS_FILE = REPO_ROOT / "kolla/globals.yml"
# Kolla's default prometheus_scrape_interval is 60s
# (kolla-ansible ansible/group_vars/all/prometheus.yml); globals.yml may override.
DEFAULT_SCRAPE_INTERVAL = "60s"
# Scrape job names a literal job="..." matcher may use: Kolla's
# prometheus.yml.j2 jobs plus our kolla/config/prometheus/prometheus.yml.d ones.
KOLLA_SCRAPE_JOBS = frozenset({
    "alertmanager", "blackbox_exporter", "cadvisor", "ceph_mgr_exporter",
    "elasticsearch_exporter", "etcd", "fluentd", "haproxy",
    "ironic_prometheus_exporter", "libvirt_exporter", "memcached", "mysqld",
    "network_exporter", "node", "openstack_exporter", "prometheus", "proxysql",
    "rabbitmq", "valkey_exporter",
})
_RANGE_FUNC = re.compile(
    r"\b(?:rate|irate|increase|delta|idelta|deriv|predict_linear|changes|resets)"
    r"\(\s*[A-Za-z_:][\w:]*\s*(?:\{[^}]*\})?\s*\[([^\]:]+)\]"
)
_DURATION_PART = re.compile(r"(\d+)(ms|s|m|h|d|w|y)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
_JOB_LITERAL = re.compile(r'\bjob\s*=\s*"([^"]*)"')
_JOB_NAME = re.compile(r"job_name:\s*['\"]?([\w-]+)")

# Selectors that match nothing on this deployment, with the reason.
DEAD_PATTERNS = {
    r'mountpoint\s*=~?\s*"/rootfs': "node_exporter runs with --path.rootfs /host; the host root is mountpoint=\"/\"",
    r"\bnode_reboot_required\b": "nothing exports node_reboot_required",
    r"\bnode_systemd_": "the node_exporter systemd collector is not enabled",
}


def _test_file_for(rules_file: Path) -> Path:
    return TEST_DIR / f"{rules_file.stem}.test.yml"


def _alert_exprs(rules_file: Path) -> list[tuple[str, str]]:
    """(rule name, expr) for every alerting and recording rule in the file."""
    return [
        (rule.get("alert") or rule.get("record"), str(rule.get("expr", "")))
        for group in load_yaml(rules_file)["groups"]
        for rule in group["rules"]
    ]


def _scrape_jobs() -> set[str]:
    # prometheus.yml.d files are Jinja templates, not plain YAML: grep job_name.
    jobs = set(KOLLA_SCRAPE_JOBS)
    for extra in sorted((RULES_DIR / "prometheus.yml.d").glob("*.yml")):
        jobs.update(_JOB_NAME.findall(extra.read_text()))
    return jobs


def _duration_seconds(duration: str) -> float:
    """Seconds in a PromQL duration such as 5m or 1m30s."""
    return sum(int(n) * _UNIT_SECONDS[unit] for n, unit in _DURATION_PART.findall(duration))


def _rule_alertnames(rules: dict) -> set[str]:
    names: set[str] = set()
    for group in rules["groups"]:
        for rule in group["rules"]:
            if "alert" in rule:
                names.add(rule["alert"])
    return names


def _tested_alertnames(test_doc: dict) -> set[str]:
    names: set[str] = set()
    for case in test_doc["tests"]:
        for assertion in case.get("alert_rule_test", []):
            names.add(assertion["alertname"])
    return names


class RuleStructureTests(unittest.TestCase):
    """Structural contract -- runs without promtool, across every rule file."""

    def test_rule_files_exist(self) -> None:
        self.assertTrue(RULE_FILES, f"no *.rules files under {RULES_DIR}")
        for rules_file in RULE_FILES:
            with self.subTest(rules=rules_file.name):
                self.assertTrue(rules_file.exists(), f"{rules_file} listed in GOVERNED_RULES but missing on disk")

    def test_each_rules_file_parses(self) -> None:
        for rules_file in RULE_FILES:
            with self.subTest(rules=rules_file.name):
                rules = load_yaml(rules_file)
                self.assertIsInstance(rules, dict)
                self.assertIn("groups", rules)
                self.assertTrue(rules["groups"], "rule file defines no groups")

    def test_every_rule_is_well_formed(self) -> None:
        for rules_file in RULE_FILES:
            rules = load_yaml(rules_file)
            for group in rules["groups"]:
                with self.subTest(rules=rules_file.name, group=group.get("name")):
                    self.assertIn("name", group, "group missing a name")
                    self.assertTrue(group.get("rules"), f"group {group.get('name')} has no rules")
                    for rule in group["rules"]:
                        # recording rules carry `record` instead of `alert`/severity.
                        if "record" in rule and "alert" not in rule:
                            self.assertTrue(rule.get("expr"), f"recording rule {rule['record']} missing expr")
                            continue
                        alert = rule.get("alert")
                        self.assertTrue(alert, f"rule in {group['name']} missing alert name")
                        expr = rule.get("expr", "")
                        self.assertTrue(expr, f"{alert} missing expr")
                        # R8: every alert is debounced. A for: clause is the usual
                        # form; a `time() - <ts> > N` expr carries an N-second
                        # debounce in the threshold itself, so for those a for:
                        # would only double-count.
                        self.assertTrue(
                            "for" in rule or "time() -" in expr,
                            f"{alert} has no for: debounce and no time()-based threshold",
                        )
                        # R6/R7: severity label present and routable.
                        severity = rule.get("labels", {}).get("severity")
                        self.assertIn(
                            severity, ALLOWED_SEVERITIES,
                            f"{alert} severity {severity!r} not in {ALLOWED_SEVERITIES}",
                        )
                        self.assertTrue(
                            rule.get("annotations", {}).get("summary"),
                            f"{alert} missing annotations.summary",
                        )

    def test_warn_first_no_critical_yet(self) -> None:
        """R7: alerts ship at warning; promotion to critical is a later,
        soak-gated label edit documented in the runbook. Guard against an
        accidental critical landing before the soak."""
        for rules_file in RULE_FILES:
            with self.subTest(rules=rules_file.name):
                rules = load_yaml(rules_file)
                criticals = [
                    rule["alert"]
                    for group in rules["groups"]
                    for rule in group["rules"]
                    if rule.get("labels", {}).get("severity") == "critical"
                ]
                self.assertEqual(
                    criticals, [],
                    f"these alerts ship critical before the soak: {criticals} "
                    "(promote via the runbook checklist, not at first landing)",
                )

    def test_test_file_parses_and_targets_real_alerts(self) -> None:
        for rules_file in PROMTOOL_RULE_FILES:
            test_file = _test_file_for(rules_file)
            with self.subTest(rules=rules_file.name):
                self.assertTrue(test_file.exists(), f"{test_file.name} missing")
                test_doc = load_yaml(test_file)
                self.assertIn("tests", test_doc)
                rule_names = _rule_alertnames(load_yaml(rules_file))
                tested = _tested_alertnames(test_doc)
                self.assertTrue(tested, "test file references no alerts")
                unknown = tested - rule_names
                self.assertFalse(unknown, f"tests reference unknown alerts (typo?): {unknown}")

    def test_every_rule_has_a_test(self) -> None:
        """R11: each alert must have at least one promtool case so a
        never-firing rule is caught before an outage, not during one."""
        for rules_file in RULE_FILES:
            test_file = _test_file_for(rules_file)
            with self.subTest(rules=rules_file.name):
                rule_names = _rule_alertnames(load_yaml(rules_file))
                tested = _tested_alertnames(load_yaml(test_file))
                untested = rule_names - tested
                self.assertFalse(untested, f"alerts with no promtool test: {untested}")

    def test_pinned_alerts_keep_their_cases(self) -> None:
        for name, pinned in PINNED_RULES.items():
            with self.subTest(rules=name):
                tested = _tested_alertnames(load_yaml(_test_file_for(RULES_DIR / name)))
                self.assertFalse(pinned - tested, "pinned alerts lost their promtool case")


class DeadRuleLintTests(unittest.TestCase):
    """Rejects rule exprs that can never fire here -- runs without promtool,
    across every *.rules file (governed, pinned, or neither)."""

    def test_rule_files_found(self) -> None:
        self.assertTrue(ALL_RULE_FILES, f"no *.rules under {RULES_DIR}")

    def test_counter_windows_span_two_scrapes(self) -> None:
        """rate()/increase()/... need >= 2 samples in the window, so a window
        under two scrape intervals (e.g. [1m] at Kolla's 60s) never returns a
        value. 4x is the comfortable choice; 2x is the hard floor."""
        interval = (load_yaml(GLOBALS_FILE) or {}).get("prometheus_scrape_interval", DEFAULT_SCRAPE_INTERVAL)
        floor = 2 * _duration_seconds(interval)
        self.assertGreater(floor, 0, f"cannot parse prometheus_scrape_interval {interval!r}")
        for rules_file in ALL_RULE_FILES:
            short = [
                f"{name}: {m.group(0)}"
                for name, expr in _alert_exprs(rules_file)
                for m in _RANGE_FUNC.finditer(expr)
                if _duration_seconds(m.group(1)) < floor
            ]
            with self.subTest(rules=rules_file.name):
                self.assertEqual(short, [], f"range windows below {floor}s")

    def test_no_selectors_that_match_nothing(self) -> None:
        for rules_file in ALL_RULE_FILES:
            exprs = _alert_exprs(rules_file)
            for pattern, reason in DEAD_PATTERNS.items():
                with self.subTest(rules=rules_file.name, pattern=pattern):
                    hits = [name for name, expr in exprs if re.search(pattern, expr)]
                    self.assertEqual(hits, [], reason)

    def test_job_matchers_name_real_scrape_jobs(self) -> None:
        """A literal job="..." that is not a scrape job matches nothing
        (upstream Ceph rules say job="ceph"; Kolla's is ceph_mgr_exporter)."""
        jobs = _scrape_jobs()
        for rules_file in ALL_RULE_FILES:
            unknown = [
                f"{name}: job={job!r}"
                for name, expr in _alert_exprs(rules_file)
                for job in _JOB_LITERAL.findall(expr)
                if job not in jobs
            ]
            with self.subTest(rules=rules_file.name):
                self.assertEqual(unknown, [], f"not a scrape job; known: {sorted(jobs)}")


class PromtoolTests(unittest.TestCase):
    """promtool-backed checks -- skipped when promtool is not installed."""

    def setUp(self) -> None:
        if shutil.which("promtool") is None:
            self.skipTest("promtool not on PATH; install Prometheus tooling to run rule unit tests")

    def test_promtool_check_rules(self) -> None:
        for rules_file in PROMTOOL_RULE_FILES:
            with self.subTest(rules=rules_file.name):
                result = subprocess.run(
                    ["promtool", "check", "rules", str(rules_file)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_promtool_unit_tests_pass(self) -> None:
        # Run from the test dir so each test file's relative rule_files resolves.
        for rules_file in PROMTOOL_RULE_FILES:
            test_file = _test_file_for(rules_file)
            with self.subTest(test=test_file.name):
                result = subprocess.run(
                    ["promtool", "test", "rules", test_file.name],
                    cwd=TEST_DIR, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
