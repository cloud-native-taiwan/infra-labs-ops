import logging

import pytest

from account_automation.models import ResourceQuota, Status
from account_automation.validators import validate_extras, validate_row, validate_status


def test_validate_row_accepts_valid_row(make_row) -> None:
    valid, message = validate_row(make_row())

    assert valid is True
    assert message == ""


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"username": "bad user"}, "Invalid username"),
        ({"email": "invalid-email"}, "Invalid email"),
        ({"duration_raw": "一年"}, "Unknown duration"),
        ({"quota": ResourceQuota(vcpus=0, ram_gb=8, storage_gb=100)}, "Quota values must be positive"),
        ({"quota": ResourceQuota(vcpus=2, ram_gb=-1, storage_gb=100)}, "Quota values must be positive"),
        ({"quota": ResourceQuota(vcpus=2, ram_gb=8, storage_gb=0)}, "Quota values must be positive"),
    ],
)
def test_validate_row_rejects_invalid_values(make_row, overrides, expected_message: str) -> None:
    valid, message = validate_row(make_row(**overrides))

    assert valid is False
    assert message == expected_message


def test_validate_row_accepts_none_quota_values(make_row) -> None:
    row = make_row(quota=ResourceQuota(vcpus=None, ram_gb=None, storage_gb=None))

    valid, message = validate_row(row)

    assert valid is True
    assert message == ""


def test_validate_status_returns_enum_for_valid_value() -> None:
    assert validate_status("active") is Status.ACTIVE
    assert validate_status("ACTIVE") is Status.ACTIVE


def test_validate_status_returns_none_for_empty_value() -> None:
    assert validate_status("") is None
    assert validate_status("   ") is None


def test_validate_status_returns_none_for_unknown_value(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = validate_status("invalid")

    assert result is None
    assert "Unknown status" in caplog.text


def test_validate_extras_parses_known_values() -> None:
    extras = validate_extras("Load Balancer, GPU")

    assert extras == frozenset({"Load Balancer", "GPU"})


def test_validate_extras_normalizes_load_balancer_alias() -> None:
    extras = validate_extras("負載平衡器 (Load Balancer), GPU")

    assert extras == frozenset({"Load Balancer", "GPU"})


def test_validate_extras_logs_for_unknown_values(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        extras = validate_extras("Load Balancer, Unknown Extra")

    assert extras == frozenset({"Load Balancer", "Unknown Extra"})
    assert "Unrecognized extras" in caplog.text


def test_validate_extras_handles_empty_input() -> None:
    assert validate_extras("") == frozenset()
    assert validate_extras(" ,  ") == frozenset()


def test_validate_status_ready_to_delete() -> None:
    assert validate_status("ready_to_delete") is Status.READY_TO_DELETE
