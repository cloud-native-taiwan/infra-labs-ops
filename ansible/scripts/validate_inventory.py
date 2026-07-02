#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

import yaml
from ansible.inventory.manager import InventoryManager
from ansible.parsing.dataloader import DataLoader


REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = REPO_ROOT / "hosts"
PLAYBOOKS_DIR = REPO_ROOT / "playbooks"

# localhost is an implicit host Ansible always provides; targets that contain a
# Jinja expression are resolved at runtime and cannot be validated statically.
IMPLICIT_TARGETS = {"localhost"}


def discover_playbooks() -> list[Path]:
    return sorted(PLAYBOOKS_DIR.glob("*.yml"))


def parse_inventory_groups() -> set[str]:
    """Return every valid ``hosts:`` target: group names, host names, and 'all'.

    Uses Ansible's own inventory parser so ``:children`` and ``:vars`` sections
    are interpreted correctly (a hand-rolled INI reader treats those headers as
    literal group names).
    """
    inventory = InventoryManager(loader=DataLoader(), sources=[str(INVENTORY_PATH)])
    return set(inventory.groups) | set(inventory.hosts) | IMPLICIT_TARGETS


def find_hosts_targets(playbooks: list[Path]) -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {}
    for playbook in playbooks:
        plays = yaml.safe_load(playbook.read_text()) or []
        targets[playbook.name] = [
            str(play["hosts"]).strip()
            for play in plays
            if isinstance(play, dict) and "hosts" in play
        ]
    return targets


def main() -> int:
    playbooks = discover_playbooks()
    valid_targets = parse_inventory_groups()
    missing = []

    for playbook_name, targets in find_hosts_targets(playbooks).items():
        for target in targets:
            if "{{" in target:
                continue
            if target not in valid_targets:
                missing.append((playbook_name, target))

    if missing:
        for playbook_name, target in missing:
            print(f"{playbook_name}: undefined hosts target {target}", file=sys.stderr)
        return 1

    print("Inventory targets validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
