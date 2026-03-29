from collections.abc import Callable
from datetime import date
from typing import Any

import pytest

from account_automation.config import AppConfig
from account_automation.models import ResourceQuota, SheetRow, Status


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
            "expiry_warning_days": 14,
            "grace_period_days": 7,
            "dry_run": False,
            "log_level": "INFO",
        }
        defaults.update(overrides)
        return AppConfig(**defaults)

    return factory
