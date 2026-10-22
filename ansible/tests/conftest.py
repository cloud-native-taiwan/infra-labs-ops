"""Shared helpers for the ansible structure tests.

test_ceph_schema.py and test_live_host_sync.py still carry their own copies
of load_yaml; they can migrate here opportunistically.
"""

from collections.abc import Iterator
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template


def load_yaml(path: Path) -> object:
    with path.open() as handle:
        return yaml.safe_load(handle)


def iter_tasks(tasks: list[dict]) -> Iterator[dict]:
    """Yield every task, descending into block/rescue/always."""
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            yield from iter_tasks(task.get(key, []))


def trusted(variables: dict) -> dict:
    """Mark string vars as templates, as ansible-core 2.19+ does for vars
    loaded from a playbook or role (plain test strings are not templated)."""
    return {k: trust_as_template(v) if isinstance(v, str) else v for k, v in variables.items()}


def evaluate(expr: str, variables: dict) -> object:
    """Evaluate a bare Jinja expression (e.g. a task's `until`) like Ansible."""
    return Templar(loader=DataLoader(), variables=variables).evaluate_expression(
        trust_as_template(expr)
    )
