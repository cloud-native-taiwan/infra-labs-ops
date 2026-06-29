"""Tests for the Prometheus alert rule files under kolla/config/prometheus/.

Mirrors the pytest-over-YAML idiom of test_kolla_structure.py / test_health_gate.py:
parse the rule and test YAML on disk and assert the contract without touching a
live host. When `promtool` is available it additionally runs the upstream rule
linter and the unit-test cases; when it is not, those two checks skip (the rule
files are not used anywhere else in the repo, so promtool is an optional dev/CI
dependency, called out in the plan's U1 verification).

Only the locally-authored rule files are governed by the R7/R11 contract below
(warn-first severities, a promtool case per alert). The kolla-shipped
ceph-alerts.rules / node-exporter-alerts.rules ship their own criticals and no
unit tests, so they are out of scope -- add a new file to GOVERNED_RULES (and
drop a matching <name>.test.yml in) to bring it under the contract.
"""

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
)
RULE_FILES = tuple(RULES_DIR / name for name in GOVERNED_RULES)

ALLOWED_SEVERITIES = {"info", "warning", "critical"}


def _test_file_for(rules_file: Path) -> Path:
    return TEST_DIR / f"{rules_file.stem}.test.yml"


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
        for rules_file in RULE_FILES:
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


class PromtoolTests(unittest.TestCase):
    """promtool-backed checks -- skipped when promtool is not installed."""

    def setUp(self) -> None:
        if shutil.which("promtool") is None:
            self.skipTest("promtool not on PATH; install Prometheus tooling to run rule unit tests")

    def test_promtool_check_rules(self) -> None:
        for rules_file in RULE_FILES:
            with self.subTest(rules=rules_file.name):
                result = subprocess.run(
                    ["promtool", "check", "rules", str(rules_file)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_promtool_unit_tests_pass(self) -> None:
        # Run from the test dir so each test file's relative rule_files resolves.
        for rules_file in RULE_FILES:
            test_file = _test_file_for(rules_file)
            with self.subTest(test=test_file.name):
                result = subprocess.run(
                    ["promtool", "test", "rules", test_file.name],
                    cwd=TEST_DIR, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
