"""Shared helpers for the ansible structure tests.

test_ceph_schema.py and test_live_host_sync.py still carry their own copies
of load_yaml; they can migrate here opportunistically.
"""

from pathlib import Path

import yaml


def load_yaml(path: Path) -> object:
    with path.open() as handle:
        return yaml.safe_load(handle)
