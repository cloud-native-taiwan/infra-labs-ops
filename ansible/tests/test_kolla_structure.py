import unittest
from pathlib import Path

import yaml
from ansible.inventory.manager import InventoryManager
from ansible.parsing.dataloader import DataLoader

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ANSIBLE_DIR.parent
KOLLA_DIR = REPO_ROOT / "kolla"


def inventory_groups(path: Path) -> dict[str, list[str]]:
    """Return group -> host list using Ansible's own inventory parser.

    Ansible expands range patterns (``openstack[01:02]``) and resolves
    ``:children`` / ``:vars`` sections natively, so no hand-rolled INI parsing
    is needed.
    """
    inventory = InventoryManager(loader=DataLoader(), sources=[str(path)])
    return inventory.get_groups_dict()


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
        groups = inventory_groups(multinode_path)
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
        """Verify OpenStack nodes in kolla/multinode match Kolla-managed hosts in Ansible inventory.

        Both sides are defined as the union of the control(ler) and compute groups.
        Ansible's implicit ``all`` group cannot stand in for kolla's OpenStack
        nodes because it also absorbs the deploy host ([deployment] localhost)
        and the Ceph-only node ([non-openstack] openstack06).
        """
        kolla_groups = inventory_groups(KOLLA_DIR / "multinode")
        kolla_hosts = set(kolla_groups.get("control", [])) | set(
            kolla_groups.get("compute", [])
        )

        ansible_groups = inventory_groups(ANSIBLE_DIR / "hosts")
        # Kolla-managed hosts are those in controller or compute groups
        kolla_managed = set(ansible_groups.get("controller", [])) | set(
            ansible_groups.get("compute", [])
        )

        # Every OpenStack node in kolla/multinode should appear in Ansible inventory
        missing_from_ansible = kolla_hosts - kolla_managed
        self.assertFalse(
            missing_from_ansible,
            f"Hosts in kolla/multinode control/compute but not in hosts controller/compute: "
            f"{missing_from_ansible}. Update both inventories when adding/removing hosts.",
        )

        # Every Kolla-managed Ansible host should appear in kolla/multinode
        missing_from_kolla = kolla_managed - kolla_hosts
        self.assertFalse(
            missing_from_kolla,
            f"Hosts in hosts controller/compute but not in kolla/multinode control/compute: "
            f"{missing_from_kolla}. Update both inventories when adding/removing hosts.",
        )


if __name__ == "__main__":
    unittest.main()
