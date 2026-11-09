"""Tests for the drift-only selection in ceph-config/tasks/apply.yml.

apply.yml loops over the audit's drift lists (ceph_config_present_mismatches,
ceph_config_absent_violations) instead of every desired entry. The mismatch
records redact `expected` for secret-named options, so the value to set is
looked up again in ceph_config_desired by (who, option).

DRIFT GUARD: the expression and loop sources below must appear verbatim in apply.yml.
"""

import unittest
from pathlib import Path

from conftest import evaluate

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
APPLY_TASKS = ANSIBLE_DIR / "roles/ceph-config/tasks/apply.yml"

EXPR_APPLY_VALUE = (
    "(ceph_config_desired | selectattr('who', 'equalto', item.who) "
    "| selectattr('option', 'equalto', item.option) | first).value"
)
LOOP_PRESENT = 'loop: "{{ ceph_config_present_mismatches }}"'
LOOP_ABSENT = 'loop: "{{ ceph_config_absent_violations }}"'


class CephApplyLogicTests(unittest.TestCase):
    def test_fragments_present_in_apply_yml(self) -> None:
        text = APPLY_TASKS.read_text()
        for fragment in (EXPR_APPLY_VALUE, LOOP_PRESENT, LOOP_ABSENT):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_value_lookup_ignores_redacted_expected(self) -> None:
        desired = [
            {
                "who": "global",
                "option": "rgw_keystone_admin_password",
                "state": "present",
                "value": "real",
            },
            {
                "who": "osd/host:openstack06",
                "option": "rgw_keystone_admin_password",
                "state": "present",
                "value": "other",
            },
        ]
        mismatch = {
            "who": "global",
            "option": "rgw_keystone_admin_password",
            "expected": "<redacted>",
        }
        value = evaluate(
            EXPR_APPLY_VALUE, {"ceph_config_desired": desired, "item": mismatch}
        )
        self.assertEqual(value, "real")


if __name__ == "__main__":
    unittest.main()
