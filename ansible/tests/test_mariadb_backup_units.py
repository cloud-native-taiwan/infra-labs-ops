"""Render tests for the deploy-host systemd units (setup-mariadb-backup.yml,
setup-cert-renewal.yml).

kolla-ansible must run as the deploy user (its SSH key is the one authorized on
the fleet) with the vault password file, from the deploy-host kolla venv. Every
unit records its success for deploy-host-alerts.rules from a root-run
ExecStartPost, which systemd runs only after ExecStart succeeded.
"""

import unittest
from pathlib import Path

from conftest import load_yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
PLAYBOOKS = ANSIBLE_DIR / "playbooks"
SERVICES = ("mariadb-backup-full.service", "mariadb-backup-incremental.service")


def _play(playbook: str, hosts: str) -> dict:
    (play,) = [p for p in load_yaml(PLAYBOOKS / playbook) if p["hosts"] == hosts]
    return play


def _render(unit: str, playbook: str = "setup-mariadb-backup.yml") -> dict[str, list[str]]:
    env = Environment(
        loader=FileSystemLoader(PLAYBOOKS / "templates"), undefined=StrictUndefined
    )
    variables = {"ansible_user": "deployer"}
    group_vars = load_yaml(ANSIBLE_DIR / "group_vars/deploy_host.yml")
    play_vars = _play(playbook, "deploy_host")["vars"]
    for key, value in {**group_vars, **play_vars}.items():
        if key != "ansible_user":
            variables[key] = env.from_string(str(value)).render(variables)
    directives: dict[str, list[str]] = {}
    for line in env.get_template(f"{unit}.j2").render(variables).splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            directives.setdefault(key, []).append(value)
    return directives


class MariadbBackupUnitTests(unittest.TestCase):
    def test_runs_as_deploy_user_with_vault_file_from_venv(self):
        for unit in SERVICES:
            directives = _render(unit)
            self.assertEqual(directives.get("User"), ["deployer"], unit)
            (exec_start,) = directives["ExecStart"]
            self.assertTrue(
                exec_start.startswith("/home/deployer/venv/bin/kolla-ansible "), unit
            )
            self.assertIn(
                "--vault-password-file /etc/kolla/ansible_vault_pass", exec_start, unit
            )

    def test_records_success_as_root_only_after_exec_start(self):
        recorder = load_yaml(ANSIBLE_DIR / "roles/deploy_host_textfile/vars/main.yml")[
            "deploy_host_textfile_recorder_bin"
        ]
        units = {
            ("setup-mariadb-backup.yml", "mariadb-backup-full.service"): "mariadb_backup_full",
            ("setup-mariadb-backup.yml", "mariadb-backup-incremental.service"): "mariadb_backup_incremental",
            ("setup-cert-renewal.yml", "cert-renewal.service"): "cert_renewal",
        }
        for (playbook, unit), task in units.items():
            directives = _render(unit, playbook)
            # `+` = full privileges despite User=; the textfile dir is not
            # writable by the deploy user.
            self.assertEqual(directives.get("ExecStartPost"), [f"+{recorder} {task}"], unit)
            self.assertEqual(directives.get("Type"), ["oneshot"], unit)

    def test_removes_stale_units_from_first_controller(self):
        play = _play(
            "setup-mariadb-backup.yml", "{{ groups['controller'][0] }}:!deploy_host"
        )
        (remove,) = [t for t in play["tasks"] if "ansible.builtin.file" in t]
        self.assertEqual(
            set(remove["loop"]),
            {
                "mariadb-backup-full.timer",
                "mariadb-backup-incremental.timer",
                "mariadb-backup-full.service",
                "mariadb-backup-incremental.service",
            },
        )

    def test_verify_tuning_checks_deploy_host(self):
        plays = load_yaml(PLAYBOOKS / "verify-tuning.yml")
        (play,) = [p for p in plays if p["name"] == "Verify MariaDB backup timers"]
        self.assertEqual(play["hosts"], "deploy_host")


if __name__ == "__main__":
    unittest.main()
