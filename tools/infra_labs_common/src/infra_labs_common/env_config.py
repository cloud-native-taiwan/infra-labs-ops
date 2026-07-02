"""Environment-variable readers shared by the tools' config loaders.

All operational tools read configuration from ``INFRA_LABS_``-prefixed
environment variables with the same empty-string-is-absent semantics. Each tool
keeps its own ``AppConfig`` dataclass and ``load_config`` (which calls
``load_dotenv`` and applies tool-specific validation); these primitives provide
the shared read/coerce behavior.
"""
from __future__ import annotations

from os import getenv

ENV_PREFIX = "INFRA_LABS_"


def is_empty(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _read(name: str) -> str | None:
    """Read ``INFRA_LABS_<name>``, treating unset and blank as absent."""
    value = getenv(f"{ENV_PREFIX}{name}")
    return None if is_empty(value) else value


def get_required(name: str) -> str:
    value = _read(name)
    if value is None:
        raise ValueError(f"{ENV_PREFIX}{name}")
    return value


def get_optional(name: str, default: str) -> str:
    value = _read(name)
    return default if value is None else value


def get_int(name: str, default: int) -> int:
    raw = _read(name)
    return default if raw is None else int(raw)


def get_bool(name: str, default: bool) -> bool:
    raw = _read(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {ENV_PREFIX}{name}: {raw}")


def get_field(name: str, require_all: bool) -> str:
    return get_required(name) if require_all else get_optional(name, "")
