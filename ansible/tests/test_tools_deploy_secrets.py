"""Structure tests for how the tools deploy playbooks ship secrets.

Covers deploy-account-automation.yml and deploy-usage-reports.yml with their
docker-compose.yml files. Nothing here can be exercised live, so the contract
is asserted on the YAML:

- the code rsync (`--delete`) never touches remote-only state (secrets/,
  .env, usage_reports state/), and its exclude list only ever grows;
- secrets are mounted as a directory, not as single files: Ansible's copy
  replaces a file by rename (new inode) and a single-file bind mount keeps
  the old inode, so a rotated secret would never reach the container;
- a changed .env or secret recreates the container, and an unchanged one
  does not;
- the secrets directory and files stay readable by the container uid only.
"""

import shlex
import unittest
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template
from conftest import evaluate, load_yaml, trusted

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ANSIBLE_DIR.parent
PLAYBOOKS = ANSIBLE_DIR / "playbooks"
CONTAINER_UID = "10001"

# tool -> (playbook, remote-only paths rsync must never delete)
TOOLS = {
    "account_automation": ("deploy-account-automation.yml", (".env", "secrets")),
    "usage_reports": ("deploy-usage-reports.yml", (".env", "secrets", "state")),
}

# Excludes each rsync carried at d746ca8. The list may only grow: dropping or
# narrowing one would let --delete remove a remote path it spares today.
BASELINE_EXCLUDES = {
    "account_automation": {
        ".venv", ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", ".coverage", ".env",
    },
    "usage_reports": {
        ".venv", ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", ".coverage", "*.egg-info", ".env", "secrets", "state",
        "vendor",
    },
}


def _play(tool: str) -> dict:
    (play,) = load_yaml(PLAYBOOKS / TOOLS[tool][0])
    return play


def _task(play: dict, name: str) -> dict:
    (task,) = [t for t in play["tasks"] if t.get("name") == name]
    return task


def _compose_service(tool: str) -> dict:
    compose = load_yaml(REPO_ROOT / "tools" / tool / "docker-compose.yml")
    (service,) = compose["services"].values()
    return service


def _render(template: str, variables: dict) -> str:
    return Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(template)
    )


def _rsync_tokens(tool: str) -> list[str]:
    play = _play(tool)
    variables = {
        **trusted(play["vars"]),
        "playbook_dir": str(PLAYBOOKS),
        "inventory_hostname": "deploy-host",
    }
    task = _task(play, "Sync tool source to deploy host")
    return shlex.split(_render(task["ansible.builtin.shell"], variables))


def _excludes(tokens: list[str]) -> set[str]:
    return {t.removeprefix("--exclude=") for t in tokens if t.startswith("--exclude=")}


def _copy_tasks(play: dict) -> tuple[dict, dict]:
    """The .env copy task and the secret-files copy task."""
    copies = [t for t in play["tasks"] if "ansible.builtin.copy" in t]
    (env_copy,) = [t for t in copies if t["ansible.builtin.copy"]["dest"].endswith("/.env")]
    (secret_copy,) = [t for t in copies if "/secrets/" in t["ansible.builtin.copy"]["dest"]]
    return env_copy, secret_copy


class ToolsDeploySecretsTests(unittest.TestCase):
    def test_rsync_spares_remote_only_paths(self):
        for tool, (_, remote_only) in TOOLS.items():
            tokens = _rsync_tokens(tool)
            self.assertIn("--delete", tokens, tool)
            self.assertNotIn("--delete-excluded", tokens, tool)
            excludes = _excludes(tokens)
            for path in remote_only:
                self.assertTrue(
                    excludes & {path, f"/{path}", f"{path}/", f"/{path}/"},
                    f"{tool}: rsync --delete would remove remote {path}",
                )

    def test_rsync_excludes_only_grow(self):
        for tool, baseline in BASELINE_EXCLUDES.items():
            missing = baseline - _excludes(_rsync_tokens(tool))
            self.assertFalse(missing, f"{tool}: rsync dropped excludes {missing}")

    def test_secrets_mounted_as_directory_read_only(self):
        for tool in TOOLS:
            volumes = _compose_service(tool)["volumes"]
            self.assertIn("./secrets:/secrets:ro", volumes, tool)
            single_files = [v for v in volumes if v.split(":")[0].startswith("./secrets/")]
            self.assertFalse(single_files, f"{tool}: single-file secret mounts {single_files}")

    def test_every_secret_path_the_container_reads_is_deployed(self):
        for tool in TOOLS:
            env = _compose_service(tool)["environment"]
            wanted = {Path(v).name for v in env.values() if str(v).startswith("/secrets/")}
            _, secret_copy = _copy_tasks(_play(tool))
            self.assertTrue(wanted, tool)
            self.assertEqual(wanted, set(secret_copy["loop"]), tool)

    def test_secret_modes_restrict_to_container_uid(self):
        for tool in TOOLS:
            play = _play(tool)
            secrets_dir = _task(play, "Create remote secrets directory")["ansible.builtin.file"]
            self.assertEqual(
                (secrets_dir["owner"], secrets_dir["group"], secrets_dir["mode"]),
                (CONTAINER_UID, CONTAINER_UID, "0700"),
                tool,
            )
            env_copy, secret_copy = _copy_tasks(play)
            self.assertEqual(env_copy["ansible.builtin.copy"]["mode"], "0600", tool)
            files = secret_copy["ansible.builtin.copy"]
            self.assertEqual(
                (files["owner"], files["group"], files["mode"]),
                (CONTAINER_UID, CONTAINER_UID, "0600"),
                tool,
            )
            for task in (env_copy, secret_copy):
                self.assertTrue(task.get("no_log"), tool)

    def test_recreate_only_when_env_or_secrets_changed(self):
        for tool in TOOLS:
            play = _play(tool)
            env_copy, secret_copy = _copy_tasks(play)
            compose = _task(play, "Pull image from GHCR and deploy via docker compose")
            self.assertEqual(compose["environment"]["IMAGE_TAG"], "{{ tools_image_tag }}", tool)
            for env_changed in (False, True):
                for secret_changed in (False, True):
                    cmd = _render(compose["ansible.builtin.command"]["cmd"], {
                        env_copy["register"]: {"changed": env_changed},
                        secret_copy["register"]: {"changed": secret_changed},
                    })
                    case = f"{tool} env={env_changed} secret={secret_changed}"
                    self.assertTrue(cmd.startswith("docker compose up -d --pull always"), case)
                    self.assertEqual(
                        "--force-recreate" in cmd.split(), env_changed or secret_changed, case
                    )
            forced = _render(compose["ansible.builtin.command"]["cmd"], {
                env_copy["register"]: {"changed": False},
                secret_copy["register"]: {"changed": False},
                "tools_force_recreate": "true",
            })
            self.assertIn("--force-recreate", forced.split(), tool)

    def test_compose_change_detection_still_sees_recreate(self):
        for tool in TOOLS:
            compose = _task(_play(tool), "Pull image from GHCR and deploy via docker compose")
            self.assertTrue(
                evaluate(compose["changed_when"], {"compose_result": {"stderr": "Recreated"}}),
                tool,
            )


if __name__ == "__main__":
    unittest.main()
