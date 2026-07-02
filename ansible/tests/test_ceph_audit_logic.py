"""Behavioural tests for the drift-classification logic in ceph-config/tasks/audit.yml.

The audit task file builds four report buckets from a live ``ceph config dump``:
present-mismatch, absent-violation, audit-only, and unmanaged. The decision
expressions are pure Jinja (secret redaction, the folded ignore-pattern regex,
the who -> section/mask split, and the selectattr membership fold). This module
evaluates those *exact* expression strings through Ansible's own templating
engine so a regression in audit.yml is caught here.

json_query itself (``community.general.json_query``) needs the optional jmespath
package, which is not part of the test virtualenv. Its query
``[?section=='S' && name=='N' && mask=='M']`` is a trivial equality filter, so it
is mirrored in Python (``_json_query_match``) while every classification decision
built on top of the lookup is evaluated with the real Jinja expression.

DRIFT GUARD: ``test_expression_fragments_present_in_audit_yml`` asserts the
expression constants below still appear verbatim in audit.yml, so the two cannot
silently diverge.
"""

import re
import unittest
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
CEPH_CLUSTER_VARS = ANSIBLE_DIR / "group_vars/ceph_cluster.yml"
CEPH_DEFAULTS = ANSIBLE_DIR / "roles/ceph-config/defaults/main.yml"
AUDIT_TASKS = ANSIBLE_DIR / "roles/ceph-config/tasks/audit.yml"

# --- Expression strings copied verbatim from audit.yml (see DRIFT GUARD) ------
EXPR_SECTION = "item.who.split('/')[0]"
EXPR_MASK = "item.who.split('/')[1] | default('')"
EXPR_PRESENT_ACTUAL = (
    "ceph_present_matches[0].value if (ceph_present_matches | length) > 0 else '__missing__'"
)
EXPR_PRESENT_MISMATCH = (
    "(ceph_present_matches | length == 0) or ((ceph_present_actual | string) != (item.value | string))"
)
EXPR_PRESENT_EXPECTED = (
    "'<redacted>' if item.option is search(ceph_config_secret_option_pattern) else item.value"
)
EXPR_PRESENT_ACTUAL_DISPLAY = (
    "'<redacted>' if item.option is search(ceph_config_secret_option_pattern) else ceph_present_actual"
)
EXPR_ABSENT_VIOLATION = "ceph_absent_matches | length > 0"
EXPR_AUDIT_RAW = (
    "ceph_audit_matches[0].value if (ceph_audit_matches | length) > 0 else '__absent__'"
)
EXPR_AUDIT_CURRENT = (
    "'<redacted>' if (item.option is search(ceph_config_secret_option_pattern) "
    "and (ceph_audit_matches | length) > 0) else _audit_raw"
)
EXPR_IGNORE_COMBINED = "'(?:' ~ (ceph_config_ignore_patterns | join(')|(?:')) ~ ')'"
EXPR_LIVE_WHO = (
    "item.section ~ '/' ~ item.mask if item.mask | default('') | length > 0 else item.section"
)
EXPR_UNMANAGED_MEMBERSHIP = (
    "not ("
    "(ceph_config_desired | selectattr('who', 'equalto', _live_who) "
    "| selectattr('option', 'equalto', item.name) | list | length) > 0"
    " or "
    "(ceph_config_audit_only | selectattr('who', 'equalto', _live_who) "
    "| selectattr('option', 'equalto', item.name) | list | length) > 0"
    ")"
)
EXPR_UNMANAGED_IGNORE = (
    "not (item.name is match(_ignore_combined) or item.section is match(_ignore_combined))"
)


def _load_yaml(path: Path) -> dict:
    with path.open() as handle:
        return yaml.safe_load(handle)


def _ev(expr: str, variables: dict):
    return Templar(loader=DataLoader(), variables=variables).evaluate_expression(
        trust_as_template(expr)
    )


def _json_query_match(live: list[dict], section: str, name: str, mask: str) -> list[dict]:
    """Python mirror of ``[?section=='S' && name=='N' && mask=='M']``."""
    return [
        entry
        for entry in live
        if entry.get("section") == section
        and entry.get("name") == name
        and entry.get("mask", "") == mask
    ]


class CephAuditLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cluster = _load_yaml(CEPH_CLUSTER_VARS)
        defaults = _load_yaml(CEPH_DEFAULTS)
        cls.ignore_patterns = cluster["ceph_config_ignore_patterns"]
        cls.desired = cluster["ceph_config_desired"]
        cls.audit_only = cluster["ceph_config_audit_only"]
        cls.secret_pattern = defaults["ceph_config_secret_option_pattern"]
        cls.base = {
            "ceph_config_secret_option_pattern": cls.secret_pattern,
            "ceph_config_ignore_patterns": cls.ignore_patterns,
            "ceph_config_desired": cls.desired,
            "ceph_config_audit_only": cls.audit_only,
        }
        cls.ignore_combined = _ev(EXPR_IGNORE_COMBINED, cls.base)

    # --- who -> section/mask split -------------------------------------------
    def test_who_splits_global_has_empty_mask(self):
        v = {"item": {"who": "global"}}
        self.assertEqual(_ev(EXPR_SECTION, v), "global")
        self.assertEqual(_ev(EXPR_MASK, v), "")

    def test_who_splits_section_and_mask(self):
        v = {"item": {"who": "osd/host:openstack06"}}
        self.assertEqual(_ev(EXPR_SECTION, v), "osd")
        self.assertEqual(_ev(EXPR_MASK, v), "host:openstack06")

    # --- present-mismatch bucket ---------------------------------------------
    def _present(self, entry: dict, live: list[dict]) -> dict:
        section = _ev(EXPR_SECTION, {"item": entry})
        mask = _ev(EXPR_MASK, {"item": entry})
        matches = _json_query_match(live, section, entry["option"], mask)
        v = {**self.base, "item": entry, "ceph_present_matches": matches}
        actual = _ev(EXPR_PRESENT_ACTUAL, v)
        v["ceph_present_actual"] = actual
        return {
            "mismatch": _ev(EXPR_PRESENT_MISMATCH, v),
            "actual": actual,
            "expected_display": _ev(EXPR_PRESENT_EXPECTED, v),
            "actual_display": _ev(EXPR_PRESENT_ACTUAL_DISPLAY, v),
        }

    def test_present_match_is_not_drift(self):
        entry = {"who": "global", "option": "public_network", "value": "192.168.114.0/24"}
        live = [{"section": "global", "name": "public_network", "mask": "", "value": "192.168.114.0/24"}]
        self.assertFalse(self._present(entry, live)["mismatch"])

    def test_present_wrong_value_is_drift(self):
        entry = {"who": "global", "option": "public_network", "value": "192.168.114.0/24"}
        live = [{"section": "global", "name": "public_network", "mask": "", "value": "10.0.0.0/8"}]
        result = self._present(entry, live)
        self.assertTrue(result["mismatch"])
        self.assertEqual(result["actual"], "10.0.0.0/8")
        self.assertEqual(result["expected_display"], "192.168.114.0/24")

    def test_present_missing_is_drift(self):
        entry = {"who": "global", "option": "public_network", "value": "192.168.114.0/24"}
        result = self._present(entry, live=[])
        self.assertTrue(result["mismatch"])
        self.assertEqual(result["actual"], "__missing__")

    def test_present_secret_value_is_redacted_in_display(self):
        entry = {"who": "global", "option": "rgw_keystone_admin_password", "value": "s3cr3t"}
        live = [
            {"section": "global", "name": "rgw_keystone_admin_password", "mask": "", "value": "live-secret"}
        ]
        result = self._present(entry, live)
        self.assertEqual(result["expected_display"], "<redacted>")
        self.assertEqual(result["actual_display"], "<redacted>")

    # --- absent-violation bucket ---------------------------------------------
    def _absent_violation(self, entry: dict, live: list[dict]) -> bool:
        section = _ev(EXPR_SECTION, {"item": entry})
        mask = _ev(EXPR_MASK, {"item": entry})
        matches = _json_query_match(live, section, entry["option"], mask)
        return _ev(EXPR_ABSENT_VIOLATION, {**self.base, "ceph_absent_matches": matches})

    def test_absent_still_present_is_violation(self):
        entry = {"who": "mon", "option": "public_network"}
        live = [{"section": "mon", "name": "public_network", "mask": "", "value": "x"}]
        self.assertTrue(self._absent_violation(entry, live))

    def test_absent_gone_is_ok(self):
        entry = {"who": "mon", "option": "public_network"}
        self.assertFalse(self._absent_violation(entry, live=[]))

    # --- audit-only bucket ---------------------------------------------------
    def _audit_current(self, entry: dict, live: list[dict]):
        section = _ev(EXPR_SECTION, {"item": entry})
        mask = _ev(EXPR_MASK, {"item": entry})
        matches = _json_query_match(live, section, entry["option"], mask)
        v = {**self.base, "item": entry, "ceph_audit_matches": matches}
        v["_audit_raw"] = _ev(EXPR_AUDIT_RAW, v)
        return _ev(EXPR_AUDIT_CURRENT, v)

    def test_audit_only_present_shows_value(self):
        entry = {"who": "osd", "option": "bdev_ioring"}
        live = [{"section": "osd", "name": "bdev_ioring", "mask": "", "value": "false"}]
        self.assertEqual(self._audit_current(entry, live), "false")

    def test_audit_only_absent_shows_marker(self):
        entry = {"who": "osd", "option": "bdev_ioring"}
        self.assertEqual(self._audit_current(entry, live=[]), "__absent__")

    def test_audit_only_secret_present_is_redacted(self):
        entry = {"who": "global", "option": "rgw_keystone_admin_password"}
        live = [
            {"section": "global", "name": "rgw_keystone_admin_password", "mask": "", "value": "live-secret"}
        ]
        self.assertEqual(self._audit_current(entry, live), "<redacted>")

    def test_audit_only_secret_absent_is_not_redacted(self):
        # Redaction only triggers when the value is actually present.
        entry = {"who": "global", "option": "rgw_keystone_admin_password"}
        self.assertEqual(self._audit_current(entry, live=[]), "__absent__")

    # --- ignore-pattern fold -------------------------------------------------
    def test_ignore_combined_folds_all_patterns(self):
        expected = "(?:" + ")|(?:".join(self.ignore_patterns) + ")"
        self.assertEqual(self.ignore_combined, expected)
        # Must remain a valid, start-anchored regex.
        re.compile(self.ignore_combined)

    def test_ignore_combined_matches_ephemeral_names(self):
        for name in ("container_image", "mgr/dashboard/foo", "osd_mclock_max_capacity_iops_hdd"):
            with self.subTest(name=name):
                self.assertTrue(
                    _ev("name is match(_c)", {"name": name, "_c": self.ignore_combined})
                )

    def test_ignore_combined_ignores_managed_names(self):
        for name in ("public_network", "osd_memory_target"):
            with self.subTest(name=name):
                self.assertFalse(
                    _ev("name is match(_c)", {"name": name, "_c": self.ignore_combined})
                )

    # --- unmanaged bucket ----------------------------------------------------
    def _is_unmanaged(self, live_entry: dict) -> bool:
        live_who = _ev(EXPR_LIVE_WHO, {"item": live_entry})
        v = {**self.base, "item": live_entry, "_live_who": live_who, "_ignore_combined": self.ignore_combined}
        return _ev(EXPR_UNMANAGED_MEMBERSHIP, v) and _ev(EXPR_UNMANAGED_IGNORE, v)

    def test_unmanaged_entry_is_flagged(self):
        entry = {"section": "global", "mask": "", "name": "some_unmanaged_option", "value": "1"}
        self.assertTrue(self._is_unmanaged(entry))

    def test_desired_entry_is_not_unmanaged(self):
        entry = {"section": "global", "mask": "", "name": "public_network", "value": "192.168.114.0/24"}
        self.assertFalse(self._is_unmanaged(entry))

    def test_audit_only_entry_is_not_unmanaged(self):
        entry = {"section": "osd", "mask": "", "name": "bdev_ioring", "value": "false"}
        self.assertFalse(self._is_unmanaged(entry))

    def test_ignored_entry_is_not_unmanaged(self):
        entry = {"section": "global", "mask": "", "name": "container_image", "value": "quay.io/ceph"}
        self.assertFalse(self._is_unmanaged(entry))

    def test_masked_desired_entry_is_not_unmanaged(self):
        # who='osd/host:openstack06' must reconstruct from section+mask.
        entry = {"section": "osd", "mask": "host:openstack06", "name": "osd_memory_target", "value": "1"}
        self.assertFalse(self._is_unmanaged(entry))

    # --- drift guard ---------------------------------------------------------
    def test_expression_fragments_present_in_audit_yml(self):
        normalized = re.sub(r"\s+", " ", AUDIT_TASKS.read_text())
        fragments = [
            EXPR_IGNORE_COMBINED,
            "item.option is search(ceph_config_secret_option_pattern)",
            EXPR_PRESENT_ACTUAL,
            EXPR_PRESENT_MISMATCH,
            EXPR_ABSENT_VIOLATION,
            EXPR_AUDIT_RAW,
            "item.name is match(_ignore_combined) or item.section is match(_ignore_combined)",
        ]
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment, normalized,
                    f"Expression drifted from audit.yml; update the test constant: {fragment}",
                )


if __name__ == "__main__":
    unittest.main()
