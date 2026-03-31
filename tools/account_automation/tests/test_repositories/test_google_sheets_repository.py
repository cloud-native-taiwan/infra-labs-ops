import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from account_automation.models import RowUpdate, Status
from account_automation.repositories.google_sheets import GoogleSheetsRepository


def test_google_sheets_repository_reads_rows(make_config, mocker) -> None:
    worksheet = Mock()
    worksheet.row_values.return_value = [
        "時間戳記",
        "使用者條款",
        "姓名",
        "使用者名稱",
        "Email",
        "使用用途",
        "使用時間",
        "使用條件",
        "使用條件補充說明",
        "vCPU 數量",
        "記憶體 (GB)",
        "儲存空間 (GB)",
        "其餘設備",
        "如何得知 CNTUG Infra Labs 資訊？",
        "Status",
        "ExpiryDate",
        "ExpiryEmailSentAt",
        "DeletePreviewSentAt",
    ]
    worksheet.get_all_records.return_value = [
        {
            "時間戳記": "2026/3/25 下午 1:00:05",
            "使用者條款": "我同意",
            "姓名": "John Doe",
            "使用者名稱": "johndoe",
            "Email": "johndoe@gmail.com",
            "使用用途": "test",
            "使用時間": "一個月",
            "使用條件": "貢獻程式碼、文件內容等至開源軟體",
            "使用條件補充說明": "test",
            "vCPU 數量": 1,
            "記憶體 (GB)": 1,
            "儲存空間 (GB)": 1,
            "其餘設備": "Load Balancer, GPU",
            "如何得知 CNTUG Infra Labs 資訊？": "friend",
            "Status": "approved",
            "ExpiryDate": "2026-04-25",
            "ExpiryEmailSentAt": "",
            "DeletePreviewSentAt": "",
        }
    ]
    client = Mock()
    client.open_by_key.return_value.worksheet.return_value = worksheet
    service_account = mocker.patch(
        "account_automation.repositories.google_sheets.gspread.service_account_from_dict",
        return_value=client,
    )

    repository = GoogleSheetsRepository(make_config())

    rows = repository.read_all_rows()

    service_account.assert_called_once_with(json.loads(make_config().google_service_account_json))
    assert len(rows) == 1
    assert rows[0].row_number == 2
    assert rows[0].status is Status.APPROVED
    assert rows[0].expiry_date == date(2026, 4, 25)
    assert rows[0].expiry_email_sent_at is None
    assert rows[0].quota.extras == frozenset({"Load Balancer", "GPU"})


@pytest.fixture
def mock_gspread(mocker):
    worksheet = Mock()
    worksheet.row_values.return_value = [
        "時間戳記",
        "Status",
        "ExpiryDate",
        "ExpiryEmailSentAt",
        "DeletePreviewSentAt",
    ]
    client = Mock()
    client.open_by_key.return_value.worksheet.return_value = worksheet
    patch = mocker.patch(
        "account_automation.repositories.google_sheets.gspread.service_account_from_dict",
        return_value=client,
    )
    return patch, worksheet


def test_google_sheets_repository_reads_service_account_from_file_path(
    tmp_path: Path, make_config, mock_gspread
) -> None:
    service_account_patch, _ = mock_gspread
    service_account_path = tmp_path / "service-account.json"
    service_account_path.write_text('{"type":"service_account"}', encoding="utf-8")

    GoogleSheetsRepository(
        make_config(google_service_account_json=str(service_account_path))
    )

    service_account_patch.assert_called_once_with({"type": "service_account"})


def test_google_sheets_repository_preserves_file_read_errors_for_service_account_path(
    make_config, mock_gspread, mocker
) -> None:
    mocker.patch(
        "account_automation.repositories.google_sheets.Path.read_text",
        side_effect=PermissionError("Permission denied"),
    )

    with pytest.raises(PermissionError, match="Permission denied"):
        GoogleSheetsRepository(
            make_config(google_service_account_json="/some/path.json")
        )


def test_google_sheets_repository_writes_updates_using_detected_columns(
    make_config, mock_gspread
) -> None:
    _, worksheet = mock_gspread
    worksheet.row_values.return_value = [
        "時間戳記",
        "Status",
        "姓名",
        "ExpiryEmailSentAt",
        "ExpiryDate",
        "DeletePreviewSentAt",
    ]

    repository = GoogleSheetsRepository(make_config())
    repository.write_row_update(
        RowUpdate(
            row_number=4,
            status=Status.ACTIVE,
            expiry_date=date(2026, 4, 25),
            expiry_email_sent_at=date(2026, 4, 10),
        )
    )

    worksheet.batch_update.assert_called_once_with([
        {"range": "B4", "values": [["active"]]},
        {"range": "E4", "values": [["2026-04-25"]]},
        {"range": "D4", "values": [["2026-04-10"]]},
    ])


def test_google_sheets_repository_raises_for_missing_required_column(
    make_config, mock_gspread
) -> None:
    _, worksheet = mock_gspread
    worksheet.row_values.return_value = [
        "時間戳記",
        "Status",
        "姓名",
        "ExpiryDate",
    ]

    with pytest.raises(ValueError, match="Missing required column: ExpiryEmailSentAt"):
        GoogleSheetsRepository(make_config())
