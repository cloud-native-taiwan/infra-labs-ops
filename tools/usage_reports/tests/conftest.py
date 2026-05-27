from collections.abc import Callable
from typing import Any

import pytest

from usage_reports.config import AppConfig


@pytest.fixture
def make_config() -> Callable[..., AppConfig]:
    def factory(**overrides: Any) -> AppConfig:
        defaults: dict[str, Any] = {
            "openstack_cloud": "openstack",
            "cloudkitty_endpoint_override": "",
            "resend_api_key": "re_test_key",
            "resend_from_email": "infra@example.com",
            "admin_email": "",
            "report_timezone": "Asia/Taipei",
            "delivery_manifest_path": "/tmp/test-deliveries.json",
            "dry_run": False,
            "log_level": "INFO",
        }
        defaults.update(overrides)
        return AppConfig(**defaults)

    return factory
