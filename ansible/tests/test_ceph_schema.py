import re
import unittest
from pathlib import Path

import yaml


ANSIBLE_DIR = Path(__file__).resolve().parents[1]
CEPH_CLUSTER_VARS = ANSIBLE_DIR / "group_vars/ceph_cluster.yml"
CEPHADM_SPECS_DIR = ANSIBLE_DIR / "files/cephadm/specs"


def load_yaml(path: Path) -> object:
    with path.open() as handle:
        return yaml.safe_load(handle)


class CephSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ceph_cluster = load_yaml(CEPH_CLUSTER_VARS)

    def test_desired_required_keys(self) -> None:
        desired = self.ceph_cluster["ceph_config_desired"]
        self.assertTrue(isinstance(desired, list), "ceph_config_desired must be a list")

        required_keys = ("who", "option", "state", "reason")
        for index, entry in enumerate(desired):
            self.assertTrue(isinstance(entry, dict), f"ceph_config_desired[{index}] must be a dict")
            with self.subTest(index=index, who=entry.get("who"), option=entry.get("option")):
                for key in required_keys:
                    self.assertIn(key, entry)
                if entry["state"] == "present":
                    self.assertIn("value", entry)

    def test_desired_valid_state(self) -> None:
        desired = self.ceph_cluster["ceph_config_desired"]

        for index, entry in enumerate(desired):
            with self.subTest(index=index, who=entry.get("who"), option=entry.get("option")):
                self.assertIn(entry["state"], {"present", "absent"})

    def test_desired_no_duplicate_who_option(self) -> None:
        desired = self.ceph_cluster["ceph_config_desired"]
        seen: set[tuple[str, str]] = set()

        for index, entry in enumerate(desired):
            key = (str(entry["who"]), str(entry["option"]))
            with self.subTest(index=index, who=key[0], option=key[1]):
                self.assertTrue(key not in seen, f"Duplicate ceph_config_desired entry for {key}")
                seen.add(key)

    def test_desired_no_secret_like_options(self) -> None:
        desired = self.ceph_cluster["ceph_config_desired"]
        secret_like_pattern = re.compile(r"(^|[_/])(password|secret|key)([_/]|$)", re.IGNORECASE)

        for index, entry in enumerate(desired):
            option = str(entry["option"])
            with self.subTest(index=index, who=entry.get("who"), option=option):
                if option.startswith("rgw_keystone_"):
                    continue
                self.assertTrue(
                    secret_like_pattern.search(option) is None,
                    f"Secret-like option should not be committed to ceph_config_desired: {option}",
                )

    def test_cephadm_specs_parse(self) -> None:
        spec_files = sorted(CEPHADM_SPECS_DIR.glob("*.yml"))
        self.assertTrue(spec_files, "Expected cephadm spec files in ansible/files/cephadm/specs/")

        for spec_path in spec_files:
            with self.subTest(spec=spec_path.name):
                data = load_yaml(spec_path)
                self.assertTrue(isinstance(data, dict), f"{spec_path.name} must parse to a mapping")
                self.assertIn("service_type", data)
                self.assertIn("placement", data)

    def test_ignore_patterns_compile(self) -> None:
        ignore_patterns = self.ceph_cluster["ceph_config_ignore_patterns"]
        self.assertTrue(ignore_patterns, "ceph_config_ignore_patterns must not be empty")

        for index, pattern in enumerate(ignore_patterns):
            with self.subTest(index=index, pattern=pattern):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    self.fail(f"Invalid regex in ceph_config_ignore_patterns[{index}]: {pattern} ({exc})")

    def test_desired_who_splits_into_section_mask(self) -> None:
        """Verify who values parse correctly into section/mask for ceph config dump matching.

        Live ceph config dump uses 'section' + 'mask' fields, not 'who'.
        E.g., who='osd/host:openstack04' must split to section='osd', mask='host:openstack04'.
        Values without '/' must have empty mask (section='global', mask='').
        """
        desired = self.ceph_cluster["ceph_config_desired"]
        safe_chars = re.compile(r"^[a-zA-Z0-9._:\-]+$")

        for index, entry in enumerate(desired):
            who = str(entry["who"])
            parts = who.split("/", 1)
            section = parts[0]
            mask = parts[1] if len(parts) > 1 else ""
            with self.subTest(index=index, who=who):
                self.assertTrue(len(section) > 0, f"Empty section in who={who}")
                self.assertTrue(
                    safe_chars.match(section),
                    f"Section contains unsafe characters: {section}",
                )
                if mask:
                    self.assertTrue(
                        safe_chars.match(mask),
                        f"Mask contains unsafe characters: {mask}",
                    )
                self.assertNotIn("'", who, f"Single quote in who={who} would break json_query")
                self.assertNotIn("'", str(entry["option"]), f"Single quote in option would break json_query")

    def test_audit_only_required_keys(self) -> None:
        audit_only = self.ceph_cluster["ceph_config_audit_only"]
        self.assertTrue(isinstance(audit_only, list), "ceph_config_audit_only must be a list")

        required_keys = ("who", "option", "note")
        for index, entry in enumerate(audit_only):
            self.assertTrue(isinstance(entry, dict), f"ceph_config_audit_only[{index}] must be a dict")
            with self.subTest(index=index, who=entry.get("who"), option=entry.get("option")):
                for key in required_keys:
                    self.assertIn(key, entry)


if __name__ == "__main__":
    unittest.main()
