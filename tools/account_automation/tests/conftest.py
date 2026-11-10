import io
import logging
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from account_automation.config import AppConfig
from account_automation.logging_config import configure_logging
from account_automation.models import ResourceQuota, SheetRow, Status
from account_automation.services.openstack_service import OpenStackServiceImpl


@pytest.fixture
def make_row() -> Callable[..., SheetRow]:
    def factory(**overrides: Any) -> SheetRow:
        defaults: dict[str, Any] = {
            "row_number": 2,
            "timestamp": "2026-03-25T10:00:00Z",
            "name": "Test User",
            "username": "test_user",
            "email": "test@example.com",
            "purpose": "Testing",
            "duration_raw": "一個月",
            "quota": ResourceQuota(vcpus=2, ram_gb=8, storage_gb=100),
            "status": Status.APPROVED,
            "expiry_date": date(2026, 4, 25),
            "expiry_email_sent_at": None,
            "delete_preview_sent_at": None,
        }
        defaults.update(overrides)
        return SheetRow(**defaults)

    return factory


@pytest.fixture
def make_config() -> Callable[..., AppConfig]:
    def factory(**overrides: Any) -> AppConfig:
        defaults: dict[str, Any] = {
            "google_service_account_json": '{"type":"service_account"}',
            "spreadsheet_id": "spreadsheet-id",
            "worksheet_name": "Sheet1",
            "openstack_cloud": "default",
            "openstack_domain_id": "domain-id",
            "openstack_member_role": "member",
            "openstack_lb_role": "load-balancer_member",
            "resend_api_key": "re_test_key",
            "resend_from_email": "infra@example.com",
            "admin_email": "",
            "expiry_warning_days": 14,
            "grace_period_days": 7,
            "dry_run": False,
            "log_level": "INFO",
        }
        defaults.update(overrides)
        return AppConfig(**defaults)

    return factory


@pytest.fixture
def production_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """Install the production handler (with SecretRedactingFilter) for one test.

    caplog's handler bypasses the filter, so tests that check what actually
    reaches the log stream read the returned buffer instead. Every handler
    configure_logging builds during the test (including a later reconfigure by
    the code under test) writes there. Restores the root logger afterwards.
    """
    buffer = io.StringIO()
    stream_handler = logging.StreamHandler
    monkeypatch.setattr(logging, "StreamHandler", lambda: stream_handler(buffer))
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    configure_logging("INFO")
    yield buffer
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


@pytest.fixture
def mock_conn() -> MagicMock:
    conn = MagicMock()
    # Default: all list calls return empty (no user/project/group exists)
    conn.identity.users.return_value = []
    conn.identity.projects.return_value = []
    conn.identity.groups.return_value = []
    conn.compute.servers.return_value = []
    conn.load_balancer.load_balancers.return_value = []
    conn.network.ips.return_value = []
    conn.network.routers.return_value = []
    conn.network.ports.return_value = []
    conn.network.networks.return_value = []
    conn.network.security_groups.return_value = []
    conn.block_storage.snapshots.return_value = []
    conn.block_storage.volumes.return_value = []
    conn.image.images.return_value = []
    # Swift: default to object-store not available (no endpoint)
    conn.object_store.get_endpoint.side_effect = Exception("service not found")
    conn.session = MagicMock()
    return conn


@pytest.fixture
def service(make_config: Callable[..., AppConfig], mock_conn: MagicMock) -> OpenStackServiceImpl:
    config = make_config()
    with patch("account_automation.services.openstack_service.openstack") as mock_os:
        mock_os.connect.return_value = mock_conn
        svc = OpenStackServiceImpl(config)
    return svc
