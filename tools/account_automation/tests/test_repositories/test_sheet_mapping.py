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


def test_serialize_row_update_omits_none_fields() -> None:
    assert serialize_row_update(RowUpdate(row_number=2)) == {}
