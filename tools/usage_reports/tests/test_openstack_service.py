from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


from usage_reports.models import ResourceCost, ResourceKind
from usage_reports.services.openstack_service import (
    OpenStackServiceImpl,
    _format_instance_specs,
    _user_id_from_assignment,
)


def _assignment(user_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(user={"id": user_id} if user_id else None)


def _make_service(make_config) -> tuple[OpenStackServiceImpl, MagicMock]:
    conn = MagicMock()
    service = OpenStackServiceImpl(config=make_config(), connection=conn)
    return service, conn


def test_format_instance_specs_full() -> None:
    assert _format_instance_specs(4, 8192) == "4 vCPU / 8.0 GiB RAM"


def test_format_instance_specs_partial() -> None:
    assert _format_instance_specs(2, None) == "2 vCPU"


def test_format_instance_specs_invalid_ram_ignored() -> None:
    assert _format_instance_specs(2, "abc") == "2 vCPU"


def test_user_id_from_assignment_dict() -> None:
    assert _user_id_from_assignment(_assignment("u-1")) == "u-1"


def test_user_id_from_assignment_missing() -> None:
    assert _user_id_from_assignment(_assignment(None)) is None


def test_list_project_members_skips_users_without_email(make_config) -> None:
    """AE2: project with 3 members where one has no email returns 2."""
    service, conn = _make_service(make_config)
    conn.identity.role_assignments.return_value = [
        _assignment("u-1"),
        _assignment("u-2"),
        _assignment("u-3"),
    ]
    users = {
        "u-1": SimpleNamespace(id="u-1", name="alice", email="alice@example.com"),
        "u-2": SimpleNamespace(id="u-2", name="bob", email=""),
        "u-3": SimpleNamespace(id="u-3", name="carol", email="carol@example.com"),
    }
    conn.identity.get_user.side_effect = lambda uid: users[uid]

    members = service.list_project_members("proj-1")

    assert len(members) == 2
    assert {m.user_name for m in members} == {"alice", "carol"}


def test_list_project_members_zero_users_with_email(make_config) -> None:
    service, conn = _make_service(make_config)
    conn.identity.role_assignments.return_value = [_assignment("u-1")]
    conn.identity.get_user.return_value = SimpleNamespace(id="u-1", name="x", email=None)

    assert service.list_project_members("proj-1") == ()


def test_list_project_members_dedupe(make_config) -> None:
    """Same user_id appearing in multiple assignments is yielded once."""
    service, conn = _make_service(make_config)
    conn.identity.role_assignments.return_value = [
        _assignment("u-1"),
        _assignment("u-1"),
    ]
    conn.identity.get_user.return_value = SimpleNamespace(id="u-1", name="alice", email="a@x.com")

    members = service.list_project_members("proj-1")
    assert len(members) == 1


def test_list_project_members_user_lookup_failure_isolated(make_config) -> None:
    service, conn = _make_service(make_config)
    conn.identity.role_assignments.return_value = [_assignment("u-1"), _assignment("u-2")]

    def fail_for_u2(uid: str) -> Any:
        if uid == "u-2":
            raise RuntimeError("ldap down")
        return SimpleNamespace(id=uid, name="alice", email="alice@x.com")

    conn.identity.get_user.side_effect = fail_for_u2

    members = service.list_project_members("proj-1")
    assert [m.user_id for m in members] == ["u-1"]


def test_enrich_resource_instance(make_config) -> None:
    service, conn = _make_service(make_config)
    conn.compute.get_server.return_value = SimpleNamespace(
        name="my-vm",
        status="ACTIVE",
        flavor={"vcpus": 2, "ram": 4096},
    )
    cost = ResourceCost(
        kind=ResourceKind.INSTANCE,
        resource_id="uuid-1",
        name="",
        specs="",
        hours=1.0,
        cost=0.5,
    )
    enriched = service.enrich_resource(cost)
    assert enriched.name == "my-vm"
    assert enriched.specs == "2 vCPU / 4.0 GiB RAM"
    assert enriched.status == "ACTIVE"


def test_enrich_resource_instance_deleted_falls_back(make_config) -> None:
    """A real 404 from Nova marks the resource as deleted."""
    from openstack import exceptions as os_exceptions

    service, conn = _make_service(make_config)
    conn.compute.get_server.side_effect = os_exceptions.ResourceNotFound("404")
    cost = ResourceCost(
        kind=ResourceKind.INSTANCE,
        resource_id="uuid-deleted",
        name="",
        specs="",
        hours=1.0,
        cost=0.5,
    )
    enriched = service.enrich_resource(cost)
    assert enriched.name == "uuid-deleted"
    assert enriched.status == "deleted"


def test_enrich_resource_instance_transient_marks_unknown(make_config) -> None:
    """A transient API error (5xx, timeout) is distinguished from deletion."""
    service, conn = _make_service(make_config)
    conn.compute.get_server.side_effect = RuntimeError("nova 503")
    cost = ResourceCost(
        kind=ResourceKind.INSTANCE,
        resource_id="uuid-live",
        name="",
        specs="",
        hours=1.0,
        cost=0.5,
    )
    enriched = service.enrich_resource(cost)
    assert enriched.status == "unknown"


def test_enrich_resource_volume_transient_marks_unknown(make_config) -> None:
    service, conn = _make_service(make_config)
    conn.block_storage.get_volume.side_effect = RuntimeError("cinder 503")
    cost = ResourceCost(
        kind=ResourceKind.STORAGE,
        resource_id="vol-1",
        name="",
        specs="",
        hours=1.0,
        cost=0.1,
    )
    enriched = service.enrich_resource(cost)
    assert enriched.status == "unknown"


def test_enrich_resource_volume(make_config) -> None:
    service, conn = _make_service(make_config)
    conn.block_storage.get_volume.return_value = SimpleNamespace(
        name="data-vol",
        status="in-use",
        size=100,
    )
    cost = ResourceCost(
        kind=ResourceKind.STORAGE,
        resource_id="vol-1",
        name="",
        specs="",
        hours=24.0,
        cost=0.5,
    )
    enriched = service.enrich_resource(cost)
    assert enriched.name == "data-vol"
    assert enriched.specs == "100 GiB"


def test_enrich_resource_no_id_returns_unchanged(make_config) -> None:
    service, _ = _make_service(make_config)
    cost = ResourceCost(
        kind=ResourceKind.INSTANCE,
        resource_id="",
        name="",
        specs="",
        hours=0,
        cost=0,
    )
    assert service.enrich_resource(cost) is cost


def test_get_project_name(make_config) -> None:
    service, conn = _make_service(make_config)
    conn.identity.get_project.return_value = SimpleNamespace(name="lab-alpha")
    assert service.get_project_name("p-1") == "lab-alpha"


def test_get_project_name_failure_returns_id(make_config) -> None:
    service, conn = _make_service(make_config)
    conn.identity.get_project.side_effect = RuntimeError("404")
    assert service.get_project_name("p-1") == "p-1"


def test_list_all_project_ids(make_config) -> None:
    service, conn = _make_service(make_config)
    conn.identity.projects.return_value = [
        SimpleNamespace(id="p-1"),
        SimpleNamespace(id="p-2"),
    ]
    assert service.list_all_project_ids() == ("p-1", "p-2")
