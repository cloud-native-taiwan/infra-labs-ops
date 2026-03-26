#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = REPO_ROOT / "hosts"
PLAYBOOKS_DIR = REPO_ROOT / "playbooks"


def discover_playbooks() -> list[Path]:
    return sorted(PLAYBOOKS_DIR.glob("*.yml"))


def parse_inventory_groups() -> set[str]:
    groups: set[str] = set()
    hosts: set[str] = set()
    current_group = None

    for raw_line in INVENTORY_PATH.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_group = line[1:-1]
            groups.add(current_group)
            continue
        if current_group is None:
            raise SystemExit(f"Inventory host entry without group header: {raw_line}")
        hosts.add(line.split()[0])

    return groups | hosts | {"all"}


def find_hosts_targets(playbooks: list[Path]) -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {}
    pattern = re.compile(r"^\s*hosts:\s*(.+?)\s*$")
    for playbook in playbooks:
        matches = []
        for line in playbook.read_text().splitlines():
            match = pattern.match(line)
            if match:
                matches.append(match.group(1))
        targets[playbook.name] = matches
    return targets


def main() -> int:
    playbooks = discover_playbooks()
    valid_targets = parse_inventory_groups()
    missing = []

    for playbook_name, targets in find_hosts_targets(playbooks).items():
        for target in targets:
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
