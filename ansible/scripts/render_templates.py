#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_VARS_DIR = REPO_ROOT / "host_vars"


def load_host_vars(host: str) -> dict:
    with (HOST_VARS_DIR / f"{host}.yml").open() as handle:
        return yaml.safe_load(handle)


def main() -> int:
    network_env = Environment(
        loader=FileSystemLoader(str(REPO_ROOT / "roles/network/templates")),
        undefined=StrictUndefined,
    )
    grub_env = Environment(
        loader=FileSystemLoader(str(REPO_ROOT / "roles/grub/templates")),
        undefined=StrictUndefined,
    )

    bond_template = network_env.get_template("bond0.j2")
    grub_template = grub_env.get_template("default_grub.j2")

    for host_file in sorted(HOST_VARS_DIR.glob("*.yml")):
        host = host_file.stem
        host_vars = load_host_vars(host)

        if host_vars.get("network_manage", True):
            rendered_bond = bond_template.render(**host_vars)
            if "slaves " not in rendered_bond:
                raise SystemExit(f"{host}: bond template did not render correctly")

        if host_vars.get("manage_grub", True):
            rendered_grub = grub_template.render(**host_vars)
            if "GRUB_CMDLINE_LINUX=" not in rendered_grub:
                raise SystemExit(f"{host}: grub template did not render correctly")

    print("Template rendering checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
