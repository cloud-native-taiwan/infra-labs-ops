"""Tests for roles/deploy-host-textfile: the deploy host's textfile-only
node_exporter, the task success recorder, and the seeded never-succeeded
metrics that kolla/config/prometheus/deploy-host-alerts.rules relies on.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from conftest import load_yaml, role_names

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
PLAYBOOKS = ANSIBLE_DIR / "playbooks"
ROLE_DIR = ANSIBLE_DIR / "roles/deploy-host-textfile"
RECORDER = ROLE_DIR / "files/deploy-host-task-success"
RULES = ANSIBLE_DIR.parent / "kolla/config/prometheus/deploy-host-alerts.rules"
METRIC = "deploy_host_task_last_success_timestamp_seconds"

# Playbook -> tasks it seeds. Every playbook whose jobs export to the textfile
# directory applies the role itself instead of relying on another having run.
EXPECTED_SEEDS = {
    "deploy-usage-reports.yml": [],
    "deploy-account-automation.yml": ["account_automation"],
    "setup-mariadb-backup.yml": ["mariadb_backup_full", "mariadb_backup_incremental"],
    "setup-cert-renewal.yml": ["cert_renewal"],
}


def _deploy_host_play(playbook: str) -> dict:
    (play,) = [p for p in load_yaml(PLAYBOOKS / playbook) if p["hosts"] == "deploy_host"]
    return play


def _seeds(play: dict) -> list[str]:
    for role in play.get("roles", []):
        if isinstance(role, dict) and role["role"] == "deploy-host-textfile":
            return role.get("vars", {}).get("deploy_host_textfile_seed_tasks", [])
    return []


class PlaybookWiringTests(unittest.TestCase):
    def test_each_exporting_playbook_applies_the_role(self) -> None:
        for playbook, seeds in EXPECTED_SEEDS.items():
            with self.subTest(playbook=playbook):
                play = _deploy_host_play(playbook)
                self.assertIn("deploy-host-textfile", role_names(play))
                self.assertEqual(_seeds(play), seeds)

    def test_node_exporter_setup_lives_only_in_the_role(self) -> None:
        for playbook in EXPECTED_SEEDS:
            with self.subTest(playbook=playbook):
                text = (PLAYBOOKS / playbook).read_text()
                self.assertNotIn("collector.textfile.directory", text)
                self.assertNotIn("name: prometheus-node-exporter", text)

    def test_every_seeded_task_has_an_alert(self) -> None:
        rules = RULES.read_text()
        for seeds in EXPECTED_SEEDS.values():
            for task in seeds:
                with self.subTest(task=task):
                    self.assertIn(f'{METRIC}{{task="{task}"}}', rules)


class RoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = load_yaml(ROLE_DIR / "defaults/main.yml")
        self.tasks = load_yaml(ROLE_DIR / "tasks/main.yml")

    def test_seed_never_overwrites_and_matches_recorder_header(self) -> None:
        (seed,) = [t for t in self.tasks if t["name"].startswith("Seed")]
        copy = seed["ansible.builtin.copy"]
        self.assertIs(copy["force"], False)
        self.assertEqual(copy["mode"], "0644")
        content = copy["content"].splitlines()
        self.assertEqual(content[-1], f'{METRIC}{{task="{{{{ item }}}}"}} 0')
        # node_exporter needs one HELP text per metric across every file.
        recorder_header = [
            line.split('echo "', 1)[1].rstrip('"').replace("$metric", METRIC)
            for line in RECORDER.read_text().splitlines()
            if line.strip().startswith('echo "# ')
        ]
        self.assertEqual(content[:2], recorder_header)

    def test_recorder_path_and_dir_match_the_script(self) -> None:
        text = RECORDER.read_text()
        self.assertIn(f":-{self.defaults['deploy_host_textfile_dir']}}}", text)
        self.assertTrue(os.access(RECORDER, os.X_OK), "recorder must be executable in git")

    def test_textfile_dir_keeps_container_ownership(self) -> None:
        (task,) = [t for t in self.tasks if t["name"].startswith("Create node_exporter textfile")]
        self.assertEqual(task["ansible.builtin.file"]["mode"], "0755")
        self.assertEqual(self.defaults["deploy_host_textfile_owner"], "10001")


class RecorderScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "DEPLOY_HOST_TEXTFILE_DIR": str(self.dir)}
        return subprocess.run(
            ["sh", str(RECORDER), *args], env=env, capture_output=True, text=True
        )

    def test_writes_world_readable_gauge_atomically(self) -> None:
        (self.dir / "task_cert_renewal.prom").write_text("old\n")
        result = self._run("cert_renewal")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.dir / "task_cert_renewal.prom"
        self.assertEqual(out.stat().st_mode & 0o777, 0o644)
        lines = out.read_text().splitlines()
        self.assertEqual(lines[1], f"# TYPE {METRIC} gauge")
        self.assertRegex(lines[2], rf'^{METRIC}\{{task="cert_renewal"\}} \d{{10}}$')
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["task_cert_renewal.prom"])

    def test_rejects_bad_task_names(self) -> None:
        for args in ((), ("a", "b"), ("",), ("Bad-Name",), ('x"} 1\nevil{a="',)):
            with self.subTest(args=args):
                self.assertEqual(self._run(*args).returncode, 2)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_task_names_in_contract_are_valid(self) -> None:
        for seeds in EXPECTED_SEEDS.values():
            for task in seeds:
                self.assertRegex(task, re.compile(r"^[a-z0-9_]+$"))


if __name__ == "__main__":
    unittest.main()
