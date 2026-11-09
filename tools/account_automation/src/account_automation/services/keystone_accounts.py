"""Domain-scoped Keystone lookups and the tool's project ownership tag.

Lab accounts live in the same Keystone domain as ``admin``, the ``service``
project and the service users, so two rules keep the tool off objects it did
not create:

* Look up users, projects and groups only by exact name *within* the configured
  domain. openstacksdk's ``find_*()`` first tries GET-by-ID, which ignores
  ``domain_id``, so a requested username equal to an existing ID or name could
  resolve to a foreign object.
* The tool owns account ``name`` iff the same-name project in the domain carries
  ``MANAGED_PROJECT_TAG`` (set when the tool creates it). The same-name user and
  group are touched only when that owned project exists. Anything else raises
  ``UnmanagedAccountError``, which the processors record as a row failure --
  except deletion with no same-name project or group, a no-op that leaves any
  same-name user untouched (``account_to_delete``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any


LOGGER = logging.getLogger(__name__)

MANAGED_PROJECT_TAG = "managed-by:account-automation"


class UnmanagedAccountError(RuntimeError):
    """A same-name Keystone object exists that this tool cannot prove it owns."""


def find_user(conn: Any, name: str, domain_id: str) -> Any | None:
    return _find_one(conn.identity.users, "user", name, domain_id)


def find_project(conn: Any, name: str, domain_id: str) -> Any | None:
    return _find_one(conn.identity.projects, "project", name, domain_id)


def find_group(conn: Any, name: str, domain_id: str) -> Any | None:
    return _find_one(conn.identity.groups, "group", name, domain_id)


def is_managed(project: Any) -> bool:
    return MANAGED_PROJECT_TAG in (getattr(project, "tags", None) or ())


def _lookup_account(conn: Any, name: str, domain_id: str) -> tuple[Any, Any, Any]:
    """Return the same-name (user, project, group), each or None. No ownership check."""
    return (
        find_user(conn, name, domain_id),
        find_project(conn, name, domain_id),
        find_group(conn, name, domain_id),
    )


def owned_account(conn: Any, name: str, domain_id: str) -> tuple[Any, Any, Any]:
    """Like ``_lookup_account``, but raise unless the tool owns what exists."""
    user, project, group = _lookup_account(conn, name, domain_id)
    _require_owned(name, project=project, user=user, group=group)
    return user, project, group


def account_to_delete(conn: Any, name: str, domain_id: str) -> tuple[Any, Any, Any] | None:
    """Like ``owned_account``, but None when there is no project or group to delete.

    When not None, the project is always present and tagged.

    A same-name user with neither is left untouched: it is one a previous delete
    retained for its other roles (e.g. a retry after a failed sheet write), or
    one that was never ours.
    """
    user, project, group = _lookup_account(conn, name, domain_id)
    if project is None and group is None:
        if user is not None:
            LOGGER.warning(
                "Same-name user %s left untouched: no project or group to delete; "
                "delete it by hand if it is a lab account",
                name,
            )
        return None
    _require_owned(name, project=project, user=user, group=group)
    return user, project, group


def _require_owned(name: str, *, project: Any | None, user: Any | None, group: Any | None) -> None:
    """Raise ``UnmanagedAccountError`` unless the tool may touch these objects.

    Passing ``None`` for an object means it is absent (or not touched by the
    caller). An account where nothing exists yet is fine to create.
    """
    if project is None:
        strays = [kind for kind, obj in (("user", user), ("group", group)) if obj is not None]
        if not strays:
            return
        reason = f"a same-name {' and '.join(strays)} exists but no same-name project does"
    elif is_managed(project):
        return
    else:
        reason = f"the same-name project is not tagged {MANAGED_PROJECT_TAG}"

    message = (
        f"Refusing to touch OpenStack account {name!r}: {reason}. It was not created by "
        "account automation; see 'Ownership tag' in the tool README."
    )
    LOGGER.error(message)
    raise UnmanagedAccountError(message)


def _find_one(
    list_fn: Callable[..., Iterable[Any]],
    kind: str,
    name: str,
    domain_id: str,
) -> Any | None:
    # Keystone's name filter (and name uniqueness) may be case-insensitive under
    # the DB collation. A case variant ("alice" for "Alice") is someone else's
    # object that would also block creating ours, so refuse rather than ignore it.
    matches = list(list_fn(name=name, domain_id=domain_id))
    if any(obj.name != name for obj in matches):
        message = f"Refusing OpenStack name {name!r}: a case-variant {kind} exists in the domain"
        LOGGER.error(message)
        raise UnmanagedAccountError(message)
    if len(matches) > 1:
        raise LookupError(f"Multiple OpenStack {kind}s named {name!r} in domain")
    return matches[0] if matches else None
