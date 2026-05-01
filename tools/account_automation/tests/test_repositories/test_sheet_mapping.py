from datetime import date

import pytest

from account_automation.models import RowUpdate
from account_automation.repositories._sheet_mapping import parse_sheet_row, serialize_row_update


def test_parse_sheet_row_handles_blank_optional_values() -> None:
    row = parse_sheet_row(
        {
            "時間戳記": "2026/3/25 下午 1:00:05",
            "姓名": "John Doe",
            "使用者名稱": "johndoe",
            "Email": "johndoe@gmail.com",
            "使用用途": "test",
            "使用時間": "一個月",
            "vCPU 數量": "1",
            "記憶體 (GB)": "1",
            "儲存空間 (GB)": "1",
            "其餘設備": None,
            "Status": "",
            "ExpiryDate": "",
            "ExpiryEmailSentAt": "",
        },
        row_number=2,
    )

    assert row.quota.extras == frozenset()
    assert row.status is None
    assert row.expiry_date is None
    assert row.expiry_email_sent_at is None


def test_parse_sheet_row_blank_quota_fields_become_none() -> None:
    row = parse_sheet_row(
        {
            "時間戳記": "2026/3/25 下午 1:00:05",
            "姓名": "Jane Doe",
            "使用者名稱": "janedoe",
            "Email": "janedoe@gmail.com",
            "使用用途": "test",
            "使用時間": "一個月",
            "vCPU 數量": "",
            "記憶體 (GB)": None,
            "儲存空間 (GB)": "  ",
            "其餘設備": "",
            "Status": "",
            "ExpiryDate": "",
            "ExpiryEmailSentAt": "",
        },
        row_number=3,
    )

    assert row.quota.vcpus is None
    assert row.quota.ram_gb is None
    assert row.quota.storage_gb is None


def test_parse_sheet_row_non_numeric_quota_value_raises() -> None:
    with pytest.raises(ValueError, match="Non-numeric value '8 cores' in column vCPU 數量"):
        parse_sheet_row(
            {
                "時間戳記": "2026/3/25 下午 1:00:05",
                "姓名": "Jane Doe",
                "使用者名稱": "janedoe",
                "Email": "janedoe@gmail.com",
                "使用用途": "test",
                "使用時間": "一個月",
                "vCPU 數量": "8 cores",
                "記憶體 (GB)": "16",
                "儲存空間 (GB)": "100",
                "其餘設備": "",
                "Status": "",
                "ExpiryDate": "",
                "ExpiryEmailSentAt": "",
            },
            row_number=4,
        )


def test_serialize_row_update_omits_none_fields() -> None:
    assert serialize_row_update(RowUpdate(row_number=2)) == {}


def test_serialize_row_update_with_delete_preview_sent_at() -> None:
    update = RowUpdate(row_number=2, delete_preview_sent_at=date(2026, 3, 25))
    result = serialize_row_update(update)
    assert result == {"DeletePreviewSentAt": "2026-03-25"}


def test_parse_sheet_row_with_delete_preview_sent_at() -> None:
    row = parse_sheet_row(
        {
            "時間戳記": "2026/3/25 下午 1:00:05",
            "姓名": "John Doe",
            "使用者名稱": "johndoe",
            "Email": "johndoe@gmail.com",
            "使用用途": "test",
            "使用時間": "一個月",
            "vCPU 數量": "1",
            "記憶體 (GB)": "1",
            "儲存空間 (GB)": "1",
            "其餘設備": "",
            "Status": "pending_delete",
            "ExpiryDate": "2026-04-25",
            "ExpiryEmailSentAt": "",
            "DeletePreviewSentAt": "2026-03-24",
        },
        row_number=2,
    )
    assert row.delete_preview_sent_at == date(2026, 3, 24)
