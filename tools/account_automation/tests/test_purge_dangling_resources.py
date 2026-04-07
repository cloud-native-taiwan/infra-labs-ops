from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from account_automation.services.rgw_admin import RgwBucket


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


def test_scan_rgw_tracks_orphaned_rgw_users_without_buckets() -> None:
    module = _load_script_module()
    result = defaultdict(lambda: defaultdict(list))
    rgw = SimpleNamespace(
        list_implicit_tenant_uids=lambda: ["proj-1$proj-1"],
        list_user_buckets=lambda project_id: [],
    )

    module._scan_rgw(rgw, frozenset(), result)

    assert result["proj-1"]["rgw_users"] == ["proj-1$proj-1"]
    assert result["proj-1"]["object_containers"] == []


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
        "proj-1",
        {
            "object_containers": [
                RgwBucket(name="bucket-a", tenant="proj-1", num_objects=1, size_bytes=1),
            ],
            "rgw_users": ["proj-1$proj-1"],
        },
        dry_run=False,
        rgw=rgw,
    )

    assert calls == [
        ("bucket", "bucket-a:proj-1"),
        ("user", "proj-1$proj-1"),
    ]
