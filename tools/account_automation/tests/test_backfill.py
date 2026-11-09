from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from account_automation.backfill import BackfillOutcome, backfill_project_tags
from account_automation.models import Status
from account_automation.services.keystone_accounts import MANAGED_PROJECT_TAG


DOMAIN = "domain-id"


@pytest.fixture
def conn() -> MagicMock:
    """Keystone with an untagged tool-shaped 'alice': user (sheet email) + member role."""
    conn = MagicMock()
    conn.identity.projects.side_effect = lambda name, domain_id: (
        [SimpleNamespace(id="proj-1", name="alice", tags=["keep-me"])] if name == "alice" else []
    )
    conn.identity.users.side_effect = lambda name, domain_id: (
        [SimpleNamespace(id="user-1", name="alice", email="Test@Example.com")]
        if name == "alice"
        else []
    )
    conn.identity.role_assignments.return_value = [SimpleNamespace(user={"id": "user-1"})]
    return conn


def test_dry_run_reports_without_tagging(conn, make_row) -> None:
    outcomes = backfill_project_tags(
        conn,
        DOMAIN,
        [make_row(username="alice", status=Status.ACTIVE)],
        apply=False,
    )

    assert outcomes == (BackfillOutcome("alice", "would-tag"),)
    conn.identity.update_project.assert_not_called()
    conn.identity.role_assignments.assert_called_once_with(scope_project_id="proj-1")


def test_apply_adds_tag_and_keeps_existing_tags(conn, make_row) -> None:
    outcomes = backfill_project_tags(
        conn,
        DOMAIN,
        [make_row(username="alice", status=Status.EXPIRED)],
        apply=True,
    )

    assert outcomes == (BackfillOutcome("alice", "tagged"),)
    conn.identity.update_project.assert_called_once()
    _, kwargs = conn.identity.update_project.call_args
    assert kwargs == {"tags": ["keep-me", MANAGED_PROJECT_TAG]}


def test_already_tagged_is_left_alone(conn, make_row) -> None:
    conn.identity.projects.side_effect = None
    conn.identity.projects.return_value = [
        SimpleNamespace(id="proj-1", name="alice", tags=[MANAGED_PROJECT_TAG]),
    ]

    outcomes = backfill_project_tags(
        conn,
        DOMAIN,
        [make_row(username="alice", status=Status.ACTIVE)],
        apply=True,
    )

    assert outcomes == (BackfillOutcome("alice", "already-tagged"),)
    conn.identity.update_project.assert_not_called()


def test_only_provisioned_statuses_are_candidates(conn, make_row) -> None:
    rows = [
        make_row(username="alice", status=status)
        for status in (Status.APPROVED, Status.DELETED, None)
    ]

    assert backfill_project_tags(conn, DOMAIN, rows, apply=True) == ()
    conn.identity.update_project.assert_not_called()


def test_duplicate_rows_yield_one_outcome(conn, make_row) -> None:
    rows = [
        make_row(username="alice", status=Status.DELETED),
        make_row(username="alice", status=Status.ACTIVE),
        make_row(username="alice", status=Status.RENEWAL),
    ]

    outcomes = backfill_project_tags(conn, DOMAIN, rows, apply=False)

    assert outcomes == (BackfillOutcome("alice", "would-tag"),)


@pytest.mark.parametrize(
    ("username", "reason"),
    [
        ("service", "Reserved username"),
        ("0123456789abcdef0123456789abcdef", "Username looks like an OpenStack ID"),
        ("bob", "no project named bob in the domain"),
    ],
)
def test_skips_unsafe_or_missing(conn, make_row, username, reason) -> None:
    outcomes = backfill_project_tags(
        conn,
        DOMAIN,
        [make_row(username=username, status=Status.ACTIVE)],
        apply=True,
    )

    assert outcomes == (BackfillOutcome(username, "skipped", reason),)
    conn.identity.update_project.assert_not_called()


def test_skips_project_without_same_name_user(conn, make_row) -> None:
    conn.identity.users.side_effect = None
    conn.identity.users.return_value = []

    outcomes = backfill_project_tags(
        conn,
        DOMAIN,
        [make_row(username="alice", status=Status.ACTIVE)],
        apply=True,
    )

    assert outcomes == (BackfillOutcome("alice", "skipped", "no user named alice in the domain"),)
    conn.identity.update_project.assert_not_called()


def test_skips_user_without_role_on_project(conn, make_row) -> None:
    conn.identity.role_assignments.return_value = []

    outcomes = backfill_project_tags(
        conn,
        DOMAIN,
        [make_row(username="alice", status=Status.ACTIVE)],
        apply=True,
    )

    assert outcomes == (
        BackfillOutcome("alice", "skipped", "user alice has no role on project alice"),
    )
    conn.identity.update_project.assert_not_called()


def test_skips_user_whose_email_differs_from_sheet(conn, make_row) -> None:
    # Same name, project and role, but not the requester the tool provisioned
    # (e.g. an operator's hand-made account that a request collided with).
    outcomes = backfill_project_tags(
        conn,
        DOMAIN,
        [make_row(username="alice", email="someone-else@example.com", status=Status.ACTIVE)],
        apply=True,
    )

    assert outcomes == (
        BackfillOutcome("alice", "skipped", "user alice's email does not match the sheet"),
    )
    conn.identity.update_project.assert_not_called()


def test_unrelated_row_fields_do_not_block_tagging(conn, make_row) -> None:
    row = make_row(username="alice", duration_raw="legacy", status=Status.ACTIVE)

    outcomes = backfill_project_tags(conn, DOMAIN, [row], apply=False)

    assert outcomes == (BackfillOutcome("alice", "would-tag"),)


@pytest.mark.parametrize(
    "other",
    [SimpleNamespace(user={"id": "operator"}), SimpleNamespace(group={"id": "team"})],
)
def test_skips_project_with_other_role_assignees(conn, make_row, other) -> None:
    # An old run that reused a foreign project left the foreign assignees behind.
    conn.identity.role_assignments.return_value = [SimpleNamespace(user={"id": "user-1"}), other]

    outcomes = backfill_project_tags(
        conn,
        DOMAIN,
        [make_row(username="alice", status=Status.ACTIVE)],
        apply=True,
    )

    assert outcomes == (
        BackfillOutcome(
            "alice", "skipped", "project alice has role assignments for other users or groups"
        ),
    )
    conn.identity.update_project.assert_not_called()


def test_only_restricts_to_named_usernames(conn, make_row) -> None:
    rows = [make_row(username=name, status=Status.ACTIVE) for name in ("alice", "bob")]

    outcomes = backfill_project_tags(
        conn, DOMAIN, rows, apply=True, only=frozenset({"alice", "carol"})
    )

    assert outcomes == (
        BackfillOutcome("alice", "tagged"),
        BackfillOutcome("carol", "skipped", "no provisioned sheet row for carol"),
    )


def test_lookup_refusal_skips_one_username_without_aborting(conn, make_row) -> None:
    conn.identity.projects.side_effect = lambda name, domain_id: (
        [SimpleNamespace(id="p", name="BOB", tags=[])]
        if name == "bob"
        else [SimpleNamespace(id="proj-1", name="alice", tags=[])]
    )
    rows = [make_row(username=name, status=Status.ACTIVE) for name in ("alice", "bob")]

    outcomes = backfill_project_tags(conn, DOMAIN, rows, apply=False)

    assert outcomes[0] == BackfillOutcome("alice", "would-tag")
    assert outcomes[1].action == "skipped"
    assert "case-variant project" in outcomes[1].reason
