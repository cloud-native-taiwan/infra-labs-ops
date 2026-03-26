import re
import unittest
from collections import defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
KOLLA_DIR = REPO_ROOT / "kolla"

_RANGE_RE = re.compile(r"\[(\d+):(\d+)\]")


def expand_host_pattern(pattern: str) -> list[str]:
    """Expand Ansible range patterns like 'openstack[01:02]' into individual hostnames."""
    match = _RANGE_RE.search(pattern)
    if not match:
        return [pattern]
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    start_str, end_str = match.group(1), match.group(2)
    width = len(start_str)
    return [f"{prefix}{i:0{width}d}{suffix}" for i in range(int(start_str), int(end_str) + 1)]


def parse_ini_inventory(path: Path) -> dict[str, list[str]]:
    """Parse an INI-format Ansible inventory, returning group -> host list."""
    groups: dict[str, list[str]] = defaultdict(list)
    current_group = None
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_group = line[1:-1]
            groups.setdefault(current_group, [])
            continue
        if current_group is not None:
            raw_host = line.split()[0]
            groups[current_group].extend(expand_host_pattern(raw_host))
    return groups


class KollaStructureTests(unittest.TestCase):
    """Smoke tests verifying kolla/ directory structure after merge."""

    def test_globals_yml_exists_and_parses(self):
        globals_path = KOLLA_DIR / "globals.yml"
        self.assertTrue(globals_path.exists(), "kolla/globals.yml missing")
        with globals_path.open() as f:
            data = yaml.safe_load(f)
        self.assertIsInstance(data, dict)
        self.assertIn("kolla_base_distro", data)
        self.assertIn("openstack_release", data)

    def test_multinode_exists_and_has_expected_groups(self):
        multinode_path = KOLLA_DIR / "multinode"
        self.assertTrue(multinode_path.exists(), "kolla/multinode missing")
        groups = parse_ini_inventory(multinode_path)
        for required_group in ("control", "compute", "network", "storage", "monitoring"):
            self.assertIn(
                required_group, groups,
                f"kolla/multinode missing required group [{required_group}]",
            )

    def test_config_has_expected_service_directories(self):
        config_dir = KOLLA_DIR / "config"
        self.assertTrue(config_dir.is_dir(), "kolla/config/ missing")
        expected_services = {"nova", "cinder", "glance", "neutron", "prometheus", "designate"}
        actual_dirs = {d.name for d in config_dir.iterdir() if d.is_dir()}
        missing = expected_services - actual_dirs
        self.assertFalse(missing, f"kolla/config/ missing service dirs: {missing}")

    def test_kolla_hosts_match_ansible_inventory(self):
        """Verify hosts in kolla/multinode [all] match Kolla-managed hosts in Ansible inventory."""
        multinode_groups = parse_ini_inventory(KOLLA_DIR / "multinode")
        kolla_hosts = set(multinode_groups.get("all", []))

        ansible_groups = parse_ini_inventory(REPO_ROOT / "hosts")
        # Kolla-managed hosts are those in controller or compute groups
        kolla_managed = set(ansible_groups.get("controller", [])) | set(
            ansible_groups.get("compute", [])
        )

        # Every host in kolla/multinode [all] should appear in Ansible inventory
        missing_from_ansible = kolla_hosts - kolla_managed
        self.assertFalse(
            missing_from_ansible,
            f"Hosts in kolla/multinode [all] but not in hosts controller/compute: "
            f"{missing_from_ansible}. Update both inventories when adding/removing hosts.",
        )

        # Every Kolla-managed Ansible host should appear in kolla/multinode [all]
        missing_from_kolla = kolla_managed - kolla_hosts
        self.assertFalse(
            missing_from_kolla,
            f"Hosts in hosts controller/compute but not in kolla/multinode [all]: "
            f"{missing_from_kolla}. Update both inventories when adding/removing hosts.",
        )
