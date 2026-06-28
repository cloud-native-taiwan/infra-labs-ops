from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from account_automation.services.rgw_admin import RgwBucket


# A realistic Keystone project ID (32-char hex) and its implicit-tenant UID.
# Using a well-formed ID keeps the purge-path fixtures honest against the
# client-side project-id/UID shape guards.
_PID = "11111111111111111111111111111111"
_UID = f"{_PID}${_PID}"


def _load_script_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "purge_dangling_resources.py"
    )
    spec = importlib.util.spec_from_file_location("purge_dangling_resources", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_passes_rgw_client_with_explicit_host_header(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    captured: dict[str, object] = {}

    def fake_collect_dangling(conn, valid_projects, rgw=None):
        captured["rgw"] = rgw
        return {}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purge_dangling_resources.py",
            "--cloud",
            "openstack",
            "--rgw-admin-url",
            "https://s3.cloudnative.tw:6780",
            "--rgw-admin-access-key",
            "access-key",
            "--rgw-admin-secret-key",
            "secret-key",
            "--rgw-admin-region",
            "cloudnative",
        ],
    )
    monkeypatch.setattr(module.openstack, "connect", lambda cloud: object())
    monkeypatch.setattr(module, "_get_valid_project_ids", lambda conn: frozenset())
    monkeypatch.setattr(module, "_collect_dangling", fake_collect_dangling)

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert excinfo.value.code == 0
    rgw = captured["rgw"]
    assert rgw is not None
    assert rgw._session.headers["Host"] == "s3.cloudnative.tw:6780"


def test_main_skips_project_that_reappeared_before_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    purged: list[str] = []

    # Empty at scan time (orphan), but present on the pre-delete re-check.
    calls = iter([frozenset(), frozenset({_PID})])
    monkeypatch.setattr(module, "_get_valid_project_ids", lambda conn: next(calls))
    monkeypatch.setattr(module, "_collect_dangling", lambda conn, valid, rgw=None: {_PID: {"volumes": [object()]}})
    monkeypatch.setattr(module, "_purge_project", lambda *a, **k: purged.append(a[1]))
    monkeypatch.setattr(module.openstack, "connect", lambda cloud: object())
    monkeypatch.setattr(sys, "argv", ["purge_dangling_resources.py", "--force"])

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert excinfo.value.code == 0
    assert purged == []  # reappeared project must not be purged


def test_main_aborts_when_reverify_returns_no_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    purged: list[str] = []

    # Non-empty at scan, but empty on the re-check (transient/auth glitch):
    # an empty set would mark every project an orphan, so purge must abort.
    calls = iter([frozenset({"other"}), frozenset()])
    monkeypatch.setattr(module, "_get_valid_project_ids", lambda conn: next(calls))
    monkeypatch.setattr(module, "_collect_dangling", lambda conn, valid, rgw=None: {_PID: {"volumes": [object()]}})
    monkeypatch.setattr(module, "_purge_project", lambda *a, **k: purged.append(a[1]))
    monkeypatch.setattr(module.openstack, "connect", lambda cloud: object())
    monkeypatch.setattr(sys, "argv", ["purge_dangling_resources.py", "--force"])

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert excinfo.value.code == 1
    assert purged == []


def test_scan_rgw_tracks_orphaned_rgw_users_without_buckets() -> None:
    module = _load_script_module()
    result = defaultdict(lambda: defaultdict(list))
    rgw = SimpleNamespace(
        list_implicit_tenant_uids=lambda: [_UID],
        list_user_buckets=lambda project_id: [],
    )

    module._scan_rgw(rgw, frozenset(), result)

    assert result[_PID]["rgw_users"] == [_UID]
    assert result[_PID]["object_containers"] == []


def test_purge_project_deletes_rgw_buckets_then_rgw_users() -> None:
    module = _load_script_module()
    conn = SimpleNamespace()
    calls: list[tuple[str, str]] = []

    def delete_bucket(bucket_name: str, *, tenant: str = "") -> None:
        calls.append(("bucket", f"{bucket_name}:{tenant}"))

    def delete_user(uid: str) -> None:
        calls.append(("user", uid))

    rgw = SimpleNamespace(delete_bucket=delete_bucket, delete_user=delete_user)

    module._purge_project(
        conn,
        _PID,
        {
            "object_containers": [
                RgwBucket(name="bucket-a", tenant=_PID, num_objects=1, size_bytes=1),
            ],
            "rgw_users": [_UID],
        },
        dry_run=False,
        rgw=rgw,
    )

    assert calls == [
        ("bucket", f"bucket-a:{_PID}"),
        ("user", _UID),
    ]
