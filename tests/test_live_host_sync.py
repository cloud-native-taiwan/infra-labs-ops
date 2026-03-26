import unittest
from collections import defaultdict
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOKS_DIR = REPO_ROOT / "playbooks"


def parse_inventory(path: Path) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    current_group = None

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_group = line[1:-1]
            groups.setdefault(current_group, [])
            continue
        if current_group is None:
            raise AssertionError(f"Host entry without group header: {raw_line}")
        groups[current_group].append(line)

    return groups


def load_yaml(path: Path):
    with path.open() as handle:
        return yaml.safe_load(handle)


class LiveHostSyncTests(unittest.TestCase):
    def test_inventory_contains_groups_only(self):
        inventory = parse_inventory(REPO_ROOT / "hosts")
        expected_groups = {
            "managed_hosts",
            "temporary",
            "arm",
            "controller",
            "compute",
            "ceph_bootstrap",
            "ceph_cluster",
            "pci_pass",
        }
        self.assertEqual(set(inventory), expected_groups)

        for members in inventory.values():
            for host_line in members:
                parts = host_line.split()
                # Only ansible_host= is allowed as inline var
                for part in parts[1:]:
                    self.assertTrue(
                        part.startswith("ansible_host="),
                        f"Unexpected inline var in hosts: {part} (use host_vars/ instead)",
                    )

    def test_host_vars_exist_for_every_host(self):
        inventory = parse_inventory(REPO_ROOT / "hosts")
        hosts = {
            line.split()[0]
            for members in inventory.values()
            for line in members
        }
        expected_hosts = {
            "openstack01",
            "openstack02",
            "openstack04",
            "openstack05",
            "openstack06",
            "arm01",
        }
        self.assertEqual(hosts, expected_hosts)

        for host in expected_hosts:
            self.assertTrue((REPO_ROOT / "host_vars" / f"{host}.yml").exists(), host)

    def test_openstack04_and_openstack06_host_facts(self):
        openstack04 = load_yaml(REPO_ROOT / "host_vars/openstack04.yml")
        self.assertEqual(openstack04["interface1"], "ens1f0np0")
        self.assertEqual(openstack04["interface2"], "ens1f1np1")

        openstack06 = load_yaml(REPO_ROOT / "host_vars/openstack06.yml")
        self.assertEqual(openstack06["node_num"], 6)
        self.assertEqual(openstack06["interface1"], "enp179s0f0np0")
        self.assertEqual(openstack06["interface2"], "enp179s0f1np1")

    def test_openstack05_runtime_interfaces_are_managed(self):
        openstack05 = load_yaml(REPO_ROOT / "host_vars/openstack05.yml")
        self.assertTrue(openstack05["network_manage"])
        self.assertEqual(openstack05["interface1"], "ens1f0np0")
        self.assertEqual(openstack05["interface2"], "ens1f1np1")

    def test_group_vars_keep_non_bbr_sysctls(self):
        all_vars = load_yaml(REPO_ROOT / "group_vars/all.yml")
        self.assertNotIn("net.core.default_qdisc", all_vars["sysctl_config"])
        base_tasks = (REPO_ROOT / "roles/base/tasks/main.yml").read_text()
        self.assertIn("Deploy sysctl tuning", base_tasks)

    def test_repo_owned_bbr_role_exists(self):
        bbr_defaults = load_yaml(REPO_ROOT / "roles/bbr/defaults/main.yml")
        bbr_tasks = (REPO_ROOT / "roles/bbr/tasks/main.yml").read_text()
        self.assertEqual(bbr_defaults["bbr_sysctl"]["net.core.default_qdisc"], "fq")
        self.assertEqual(bbr_defaults["bbr_sysctl"]["net.ipv4.tcp_congestion_control"], "bbr")
        self.assertIn("60-bbr.conf", bbr_tasks)

    def test_bootstrap_is_a_role_orchestrator(self):
        bootstrap = load_yaml(PLAYBOOKS_DIR / "bootstrap.yml")
        self.assertEqual(len(bootstrap), 2)

        managed_play = bootstrap[0]
        self.assertEqual(managed_play["hosts"], "managed_hosts")
        managed_roles = [
            role["role"] if isinstance(role, dict) else role
            for role in managed_play["roles"]
        ]
        self.assertEqual(
            managed_roles,
            ["base", "network", "mail", "kvm", "grub", "swap", "bbr", "openstack05_battlemage"],
        )
        self.assertNotIn("tasks", managed_play)

        ceph_play = bootstrap[1]
        self.assertEqual(ceph_play["hosts"], "ceph_bootstrap")
        ceph_roles = [
            role["role"] if isinstance(role, dict) else role
            for role in ceph_play["roles"]
        ]
        self.assertEqual(ceph_roles, ["ceph-bootstrap"])

    def test_swap_and_pci_pass_no_longer_edit_grub_directly(self):
        for playbook in ("swap.yml", "pci-pass.yml"):
            content = (PLAYBOOKS_DIR / playbook).read_text()
            self.assertNotIn("/etc/default/grub", content, playbook)
            self.assertNotIn("GRUB_CMDLINE", content, playbook)
            self.assertNotIn("update-grub", content, playbook)

    def test_sensitive_inputs_are_externalized(self):
        self.assertFalse((REPO_ROOT / "templates/authorized_keys").exists())
        base_tasks = (REPO_ROOT / "roles/base/tasks/main.yml").read_text()
        mail_tasks = (REPO_ROOT / "roles/mail/tasks/main.yml").read_text()
        readme = (REPO_ROOT / "README.en.md").read_text()
        ansible_cfg = (REPO_ROOT / "ansible.cfg").read_text()
        self.assertIn("ssh_authorized_keys_src", base_tasks)
        self.assertIn("mail_passwd_client_src", mail_tasks)
        self.assertIn("private/authorized_keys", readme)
        self.assertIn("private/passwd.client", readme)
        self.assertIn("inventory = ./hosts", ansible_cfg)

    def test_exporter_targets_managed_hosts_only(self):
        exporter = load_yaml(PLAYBOOKS_DIR / "exporter.yml")
        self.assertEqual(exporter[0]["hosts"], "managed_hosts")
        tasks = exporter[0]["tasks"]
        self.assertIn("bond0.1113", tasks[0]["ansible.builtin.command"])
        self.assertFalse(tasks[0]["failed_when"])

    def test_validation_scaffolding_exists(self):
        self.assertTrue((REPO_ROOT / ".ansible-lint").exists())
        self.assertTrue((REPO_ROOT / ".yamllint").exists())
        self.assertTrue((REPO_ROOT / "scripts/validate.sh").exists())

    def test_network_restart_is_rolled_with_delay(self):
        all_vars = load_yaml(REPO_ROOT / "group_vars/all.yml")
        handlers = (REPO_ROOT / "roles/network/handlers/main.yml").read_text()
        tasks = (REPO_ROOT / "roles/network/tasks/main.yml").read_text()
        readme = (REPO_ROOT / "README.en.md").read_text()
        self.assertEqual(all_vars["network_restart_delay_seconds"], 15)
        self.assertIn("throttle: 1", handlers)
        self.assertIn("Pause After Networking Restart", handlers)
        self.assertIn("Pause After Networking Restart", tasks)
        self.assertIn("15 second pause", readme)

    def test_ceph_bootstrap_documents_bookworm_on_trixie_choice(self):
        defaults = load_yaml(REPO_ROOT / "roles/ceph-bootstrap/defaults/main.yml")
        tasks = load_yaml(REPO_ROOT / "roles/ceph-bootstrap/tasks/main.yml")
        readme = (REPO_ROOT / "README.en.md").read_text()

        self.assertEqual(defaults["cephadm_release"], "tentacle")
        self.assertEqual(defaults["cephadm_version"], "20.2.0")
        self.assertEqual(defaults["cephadm_repo_codename"], "bookworm")
        repo_line = tasks[1]["ansible.builtin.apt_repository"]["repo"]
        self.assertIn("{{ cephadm_repo_codename }}", repo_line)
        self.assertIn("{{ cephadm_repo_url }}", repo_line)
        self.assertIn("bookworm", readme)
        self.assertIn("trixie", readme)
        self.assertIn("20.2.0", readme)

    def test_bond_template_renders_expected_values(self):
        env = Environment(
            loader=FileSystemLoader(str(REPO_ROOT / "roles/network/templates")),
            undefined=StrictUndefined,
        )
        template = env.get_template("bond0.j2")

        cases = {
            "openstack01": ("enp1s0f0np0", "enp1s0f1np1", 11),
            "openstack04": ("ens1f0np0", "ens1f1np1", 14),
            "openstack06": ("enp179s0f0np0", "enp179s0f1np1", 16),
            "arm01": ("enp1s0f0np0", "enp1s0f1np1", 31),
        }

        for host, (iface1, iface2, suffix) in cases.items():
            host_vars = load_yaml(REPO_ROOT / "host_vars" / f"{host}.yml")
            rendered = template.render(**host_vars)
            self.assertIn(f"slaves {iface1} {iface2}", rendered, host)
            self.assertIn(f"address 192.168.114.{suffix}/24", rendered, host)
            self.assertIn(f"address 192.168.113.{suffix}/24", rendered, host)

    def test_grub_iommu_is_runtime_detected(self):
        """IOMMU vendor flag is no longer in host_vars; the grub role
        detects it at runtime via ansible_facts['processor'].  Verify
        that host_vars carry the boolean toggle instead of a hardcoded
        vendor flag."""
        for host in ("openstack01", "openstack02", "openstack04", "openstack05", "openstack06"):
            hv = load_yaml(REPO_ROOT / "host_vars" / f"{host}.yml")
            self.assertTrue(hv.get("grub_iommu_enable"), host)
            flags = " ".join(hv["grub_cmdline_linux"])
            self.assertNotIn("intel_iommu", flags, host)
            self.assertNotIn("amd_iommu", flags, host)
            self.assertNotIn("iommu=pt", flags, host)

        for host in ("arm01",):
            hv = load_yaml(REPO_ROOT / "host_vars" / f"{host}.yml")
            self.assertFalse(hv.get("grub_iommu_enable", False), host)

    def test_grub_template_keeps_battlemage_flags_out_of_default(self):
        env = Environment(
            loader=FileSystemLoader(str(REPO_ROOT / "roles/grub/templates")),
            undefined=StrictUndefined,
        )
        template = env.get_template("default_grub.j2")

        openstack01 = load_yaml(REPO_ROOT / "host_vars/openstack01.yml")
        rendered01 = template.render(**openstack01)
        self.assertIn("zswap.enabled=1", rendered01)

        openstack05 = load_yaml(REPO_ROOT / "host_vars/openstack05.yml")
        rendered05 = template.render(**openstack05)
        self.assertIn('pci=realloc,big_root_window', rendered05)
        self.assertIn('xe.vram_bar_size=256', rendered05)
        default_line = next(
            line for line in rendered05.splitlines() if line.startswith("GRUB_CMDLINE_LINUX_DEFAULT")
        )
        self.assertNotIn("pci=realloc,big_root_window", default_line)
        self.assertNotIn("xe.vram_bar_size=256", default_line)


if __name__ == "__main__":
    unittest.main()
