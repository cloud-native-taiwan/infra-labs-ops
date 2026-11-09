"""One-off backfill of the ownership tag onto projects created before it existed.

The source of truth for "the tool provisioned this account" is the Google Sheet:
a row the tool moved past APPROVED (ACTIVE onward, not yet DELETED). A row alone
is not enough -- before the tag existed, a requested name colliding with an
existing project was silently reused and marked ACTIVE -- so a project is tagged
only when it also has the fingerprint the tool leaves: a valid, non-reserved
username, a same-name project and a same-name user in the domain, a user email
equal to a sheet email for that username (the tool sets it from the row), and
role assignments on the project for that user only (a project an old run reused
keeps its original assignees). Even that cannot prove the project was not hand
made, so tagging needs the operator to name each username after reviewing the
dry run. Everything else is reported as skipped to resolve by hand.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from account_automation import validators
from account_automation.models import SheetRow, Status
from account_automation.services.keystone_accounts import (
    MANAGED_PROJECT_TAG,
    UnmanagedAccountError,
    find_project,
    find_user,
    is_managed,
)


LOGGER = logging.getLogger(__name__)

PROVISIONED_STATUSES = frozenset(
    {
        Status.ACTIVE,
        Status.EXPIRING,
        Status.EXPIRED,
        Status.RENEWAL,
        Status.PENDING_DELETE,
        Status.READY_TO_DELETE,
    }
)


@dataclass(frozen=True)
class BackfillOutcome:
    username: str
    action: str  # "tagged", "would-tag", "already-tagged" or "skipped"
    reason: str = ""


def backfill_project_tags(
    conn: Any,
    domain_id: str,
    rows: Iterable[SheetRow],
    *,
    apply: bool,
    only: frozenset[str] | None = None,
) -> tuple[BackfillOutcome, ...]:
    """Tag provisioned accounts' projects; only report unless ``apply`` is set.

    ``only`` restricts the run to those usernames (all candidates when None).
    """
    emails: dict[str, set[str]] = {}
    for row in rows:
        if row.status in PROVISIONED_STATUSES:
            emails.setdefault(row.username, set()).add(row.email.strip().lower())
    names = sorted(emails if only is None else only)
    return tuple(_backfill_or_skip(conn, domain_id, name, emails, apply=apply) for name in names)


def _backfill_or_skip(
    conn: Any,
    domain_id: str,
    name: str,
    emails: dict[str, set[str]],
    *,
    apply: bool,
) -> BackfillOutcome:
    if name not in emails:
        return BackfillOutcome(name, "skipped", f"no provisioned sheet row for {name}")
    try:
        return _backfill_one(conn, domain_id, name, emails[name], apply=apply)
    except (UnmanagedAccountError, LookupError) as exc:  # one bad name must not abort the run
        return BackfillOutcome(name, "skipped", str(exc))


def _backfill_one(
    conn: Any,
    domain_id: str,
    name: str,
    sheet_emails: set[str],
    *,
    apply: bool,
) -> BackfillOutcome:
    problem = validators.new_username_problem(name)
    if problem:
        return BackfillOutcome(name, "skipped", problem)

    project = find_project(conn, name, domain_id)
    if project is None:
        return BackfillOutcome(name, "skipped", f"no project named {name} in the domain")
    if is_managed(project):
        return BackfillOutcome(name, "already-tagged")

    user = find_user(conn, name, domain_id)
    if user is None:
        return BackfillOutcome(name, "skipped", f"no user named {name} in the domain")
    if (getattr(user, "email", None) or "").strip().lower() not in sheet_emails:
        return BackfillOutcome(name, "skipped", f"user {name}'s email does not match the sheet")
    # A group assignment has no "user" and maps to None, i.e. "someone else".
    assignees = {
        (getattr(a, "user", None) or {}).get("id")
        for a in conn.identity.role_assignments(scope_project_id=project.id)
    }
    if user.id not in assignees:
        return BackfillOutcome(name, "skipped", f"user {name} has no role on project {name}")
    if assignees != {user.id}:
        return BackfillOutcome(
            name, "skipped", f"project {name} has role assignments for other users or groups"
        )

    if not apply:
        return BackfillOutcome(name, "would-tag")
    LOGGER.info("Tagging project %s for username=%s with %s", project.id, name, MANAGED_PROJECT_TAG)
    conn.identity.update_project(project, tags=[*(project.tags or []), MANAGED_PROJECT_TAG])
    return BackfillOutcome(name, "tagged")
