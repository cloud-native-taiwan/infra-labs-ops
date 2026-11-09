"""Ownership guard: the tool only touches accounts whose project it tagged."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from account_automation.services import keystone_accounts
from account_automation.services.keystone_accounts import (
    MANAGED_PROJECT_TAG,
    UnmanagedAccountError,
)


ALICE_USER = SimpleNamespace(id="user-1", name="alice")
TAGGED = SimpleNamespace(id="proj-1", name="alice", tags=[MANAGED_PROJECT_TAG])
UNTAGGED = SimpleNamespace(id="proj-1", name="alice", tags=[])
GROUP = SimpleNamespace(id="group-1", name="alice")


@pytest.fixture
def conn(mock_conn: MagicMock) -> MagicMock:
    mock_conn.identity.validate_user_has_project_role.return_value = False
    return mock_conn


def _assert_untouched(conn: MagicMock) -> None:
    for method in (
        "create_project",
        "create_user",
        "assign_project_role_to_user",
        "delete_project",
        "delete_user",
        "delete_group",
        "update_user",
    ):
        getattr(conn.identity, method).assert_not_called()
    conn.compute.update_quota_set.assert_not_called()
    conn.compute.servers.assert_not_called()


class TestLookup:
    def test_lists_by_name_and_domain_never_find(self, conn) -> None:
        conn.identity.projects.return_value = [TAGGED]

        assert keystone_accounts.find_project(conn, "alice", "domain-id") is TAGGED

        conn.identity.projects.assert_called_once_with(name="alice", domain_id="domain-id")
        conn.identity.find_project.assert_not_called()

    def test_case_variant_name_is_refused(self, conn) -> None:
        # Keystone names are unique case-insensitively under MySQL collation, so
        # a foreign "alice" blocks "Alice" -- refuse instead of treating it as absent.
        conn.identity.users.return_value = [SimpleNamespace(id="u", name="alice")]

        with pytest.raises(UnmanagedAccountError):
            keystone_accounts.find_user(conn, "Alice", "domain-id")

    def test_id_shaped_name_is_only_matched_by_name(self, conn) -> None:
        project_id = "0123456789abcdef0123456789abcdef"

        assert keystone_accounts.find_project(conn, project_id, "domain-id") is None

        conn.identity.get_project.assert_not_called()


class TestCreateRefusesForeignAccounts:
    @pytest.mark.parametrize(
        ("users", "projects", "groups"),
        [
            ([], [UNTAGGED], []),
            ([ALICE_USER], [UNTAGGED], []),
            ([ALICE_USER], [], []),
            ([], [], [GROUP]),
        ],
        ids=["untagged-project", "user-on-untagged-project", "user-no-project", "group-only"],
    )
    def test_refuses_and_touches_nothing(
        self,
        service,
        conn,
        make_row,
        caplog,
        users,
        projects,
        groups,
    ) -> None:
        conn.identity.users.return_value = users
        conn.identity.projects.return_value = projects
        conn.identity.groups.return_value = groups

        with caplog.at_level(logging.ERROR), pytest.raises(UnmanagedAccountError):
            service.create_user_and_project(make_row(username="alice"), "pw")

        _assert_untouched(conn)
        assert "Refusing" in caplog.text

    def test_new_project_is_tagged(self, service, conn, make_row) -> None:
        conn.identity.create_project.return_value = TAGGED

        service.create_user_and_project(make_row(username="alice"), "pw")

        conn.identity.create_project.assert_called_once_with(
            name="alice",
            domain_id="domain-id",
            tags=[MANAGED_PROJECT_TAG],
        )

    def test_reuses_tagged_project_and_its_user(self, service, conn, make_row) -> None:
        conn.identity.users.return_value = [ALICE_USER]
        conn.identity.projects.return_value = [TAGGED]

        service.create_user_and_project(make_row(username="alice"), "pw")

        conn.identity.create_project.assert_not_called()
        conn.identity.create_user.assert_not_called()
        conn.identity.assign_project_role_to_user.assert_called_once()


class TestDestructivePathsRefuseUntagged:
    @pytest.fixture(autouse=True)
    def _foreign(self, conn) -> None:
        conn.identity.users.return_value = [ALICE_USER]
        conn.identity.projects.return_value = [UNTAGGED]
        conn.identity.groups.return_value = [GROUP]

    def test_inventory_log_does_not_list_foreign_resources(self, service, conn) -> None:
        service.log_project_resources("alice")

        conn.compute.servers.assert_not_called()
        conn.identity.group_users.assert_not_called()

    def test_delete_refuses_before_any_mutation(self, service, conn) -> None:
        with pytest.raises(UnmanagedAccountError):
            service.delete_user_and_project("alice")

        _assert_untouched(conn)
        conn.identity.remove_user_from_group.assert_not_called()

    def test_delete_refuses_group_without_project(self, service, conn) -> None:
        conn.identity.projects.return_value = []

        with pytest.raises(UnmanagedAccountError):
            service.delete_user_and_project("alice")

        _assert_untouched(conn)

    def test_preview_refuses(self, service) -> None:
        with pytest.raises(UnmanagedAccountError):
            service.preview_delete("alice")

    def test_set_user_enabled_refuses(self, service, conn) -> None:
        with pytest.raises(UnmanagedAccountError):
            service.set_user_enabled("alice", False)

        conn.identity.update_user.assert_not_called()

    def test_project_exists_refuses(self, service) -> None:
        with pytest.raises(UnmanagedAccountError):
            service.project_exists("alice")


class TestUserWithoutProject:
    @pytest.fixture(autouse=True)
    def _stray_user(self, conn) -> None:
        conn.identity.users.return_value = [ALICE_USER]

    def test_delete_is_noop_and_warns(self, service, conn, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            service.delete_user_and_project("alice")

        _assert_untouched(conn)
        assert "left untouched" in caplog.text

    def test_preview_matches_noop_delete(self, service) -> None:
        preview = service.preview_delete("alice")

        assert (preview.user_found, preview.project_found) == (False, False)


class TestManagedAccountAllowed:
    def test_project_exists_true_for_tagged(self, service, conn) -> None:
        conn.identity.projects.return_value = [TAGGED]

        assert service.project_exists("alice") is True

    def test_project_exists_false_when_absent(self, service) -> None:
        assert service.project_exists("alice") is False

    def test_set_user_enabled_on_managed_account(self, service, conn) -> None:
        conn.identity.users.return_value = [ALICE_USER]
        conn.identity.projects.return_value = [TAGGED]

        service.set_user_enabled("alice", False)

        conn.identity.update_user.assert_called_once_with(ALICE_USER, enabled=False)
