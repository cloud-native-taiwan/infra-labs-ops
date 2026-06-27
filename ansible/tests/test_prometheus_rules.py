"""Tests for the control-plane Prometheus alert rules.

Mirrors the pytest-over-YAML idiom of test_kolla_structure.py / test_health_gate.py:
parse the rule and test YAML on disk and assert the contract without touching a
live host. When `promtool` is available it additionally runs the upstream rule
linter and the unit-test cases; when it is not, those two checks skip (the rule
files are not used anywhere else in the repo, so promtool is an optional dev/CI
dependency, called out in the plan's U1 verification).
"""

import shutil
import subprocess
import unittest
from pathlib import Path

from conftest import load_yaml

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ANSIBLE_DIR.parent
RULES_FILE = REPO_ROOT / "kolla/config/prometheus/control-plane-alerts.rules"
TEST_DIR = ANSIBLE_DIR / "tests/prometheus_rules"
TEST_FILE = TEST_DIR / "control-plane-alerts.test.yml"

ALLOWED_SEVERITIES = {"info", "warning", "critical"}


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


class ControlPlaneRuleStructureTests(unittest.TestCase):
    """Structural contract -- runs without promtool."""

    def test_rules_file_exists_and_parses(self) -> None:
        self.assertTrue(RULES_FILE.exists(), "control-plane-alerts.rules missing")
        rules = load_yaml(RULES_FILE)
        self.assertIsInstance(rules, dict)
        self.assertIn("groups", rules)
        self.assertTrue(rules["groups"], "rule file defines no groups")

    def test_every_rule_is_well_formed(self) -> None:
        rules = load_yaml(RULES_FILE)
        for group in rules["groups"]:
            self.assertIn("name", group, "group missing a name")
            self.assertTrue(group.get("rules"), f"group {group.get('name')} has no rules")
            for rule in group["rules"]:
                alert = rule.get("alert")
                self.assertTrue(alert, f"rule in {group['name']} missing alert name")
                self.assertTrue(rule.get("expr"), f"{alert} missing expr")
                # R8: every alert carries a for: debounce.
                self.assertIn("for", rule, f"{alert} missing a for: debounce")
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
        rules = load_yaml(RULES_FILE)
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
        self.assertTrue(TEST_FILE.exists(), "control-plane-alerts.test.yml missing")
        test_doc = load_yaml(TEST_FILE)
        self.assertIn("tests", test_doc)
        rule_names = _rule_alertnames(load_yaml(RULES_FILE))
        tested = _tested_alertnames(test_doc)
        self.assertTrue(tested, "test file references no alerts")
        unknown = tested - rule_names
        self.assertFalse(unknown, f"tests reference unknown alerts (typo?): {unknown}")

    def test_every_rule_has_a_test(self) -> None:
        """R11: each alert must have at least one promtool case so a
        never-firing rule is caught before an outage, not during one."""
        rule_names = _rule_alertnames(load_yaml(RULES_FILE))
        tested = _tested_alertnames(load_yaml(TEST_FILE))
        untested = rule_names - tested
        self.assertFalse(untested, f"alerts with no promtool test: {untested}")


class ControlPlanePromtoolTests(unittest.TestCase):
    """promtool-backed checks -- skipped when promtool is not installed."""

    def setUp(self) -> None:
        if shutil.which("promtool") is None:
            self.skipTest("promtool not on PATH; install Prometheus tooling to run rule unit tests")

    def test_promtool_check_rules(self) -> None:
        result = subprocess.run(
            ["promtool", "check", "rules", str(RULES_FILE)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_promtool_unit_tests_pass(self) -> None:
        # Run from the test dir so the test file's relative rule_files resolves.
        result = subprocess.run(
            ["promtool", "test", "rules", TEST_FILE.name],
            cwd=TEST_DIR, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
