from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from account_automation.models import ResourceQuota, RowUpdate, Status


def test_resource_quota_is_frozen() -> None:
    quota = ResourceQuota(vcpus=2, ram_gb=8, storage_gb=100)

    with pytest.raises(FrozenInstanceError):
        quota.vcpus = 4


def test_sheet_row_is_frozen(make_row) -> None:
    row = make_row()

    with pytest.raises(FrozenInstanceError):
        row.username = "other"


def test_status_enum_values() -> None:
    assert Status.APPROVED.value == "approved"
    assert Status.ACTIVE.value == "active"
    assert Status.EXPIRING.value == "expiring"
    assert Status.EXPIRED.value == "expired"
    assert Status.PENDING_DELETE.value == "pending_delete"
    assert Status.RENEWAL_REQUESTED.value == "renewal_requested"
    assert Status.DELETED.value == "deleted"


def test_row_update_construction() -> None:
    update = RowUpdate(
        row_number=3,
        status=Status.ACTIVE,
        expiry_date=date(2026, 5, 1),
        expiry_email_sent_at=date(2026, 4, 20),
    )

    assert update.row_number == 3
    assert update.status is Status.ACTIVE
    assert update.expiry_date == date(2026, 5, 1)
    assert update.expiry_email_sent_at == date(2026, 4, 20)


def test_row_update_defaults() -> None:
    update = RowUpdate(row_number=4)

    assert update.row_number == 4
    assert update.status is None
    assert update.expiry_date is None
    assert update.expiry_email_sent_at is None
