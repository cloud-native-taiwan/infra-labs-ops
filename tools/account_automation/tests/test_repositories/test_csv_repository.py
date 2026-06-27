from datetime import date

import pytest

from account_automation.models import RowUpdate, Status
from account_automation.repositories.csv_repository import CsvRepository


HEADER = (
    "時間戳記,使用者條款,姓名,使用者名稱,Email,使用用途,使用時間,使用條件,"
    "使用條件補充說明,vCPU 數量,記憶體 (GB),儲存空間 (GB),其餘設備,"
    "如何得知 CNTUG Infra Labs 資訊？,Status,ExpiryDate,ExpiryEmailSentAt\n"
)

ROW = (
    '2026/3/25 下午 1:00:05,我同意,John Doe,johndoe,johndoe@gmail.com,test,一個月,'
    '貢獻程式碼、文件內容等至開源軟體,test,1,1,1,"Load Balancer, GPU",friend,'
    "approved,2026-04-25,\n"
)


def test_csv_repository_reads_all_rows(tmp_path) -> None:
    csv_path = tmp_path / "sheet.csv"
    csv_path.write_text(HEADER + ROW, encoding="utf-8", newline="")

    repository = CsvRepository(str(csv_path))

    rows = repository.read_all_rows()

    assert len(rows) == 1
    assert rows[0].row_number == 2
    assert rows[0].timestamp == "2026/3/25 下午 1:00:05"
    assert rows[0].name == "John Doe"
    assert rows[0].username == "johndoe"
    assert rows[0].email == "johndoe@gmail.com"
    assert rows[0].purpose == "test"
    assert rows[0].duration_raw == "一個月"
    assert rows[0].quota.vcpus == 1
    assert rows[0].quota.ram_gb == 1
    assert rows[0].quota.storage_gb == 1
    assert rows[0].quota.extras == frozenset({"Load Balancer", "GPU"})
    assert rows[0].status is Status.APPROVED
    assert rows[0].expiry_date == date(2026, 4, 25)
    assert rows[0].expiry_email_sent_at is None


def test_csv_repository_writes_only_non_none_fields(tmp_path) -> None:
    csv_path = tmp_path / "sheet.csv"
    csv_path.write_text(HEADER + ROW, encoding="utf-8", newline="")

    repository = CsvRepository(str(csv_path))
    repository.write_row_update(
        RowUpdate(
            row_number=2,
            status=Status.EXPIRING,
            expiry_email_sent_at=date(2026, 4, 10),
        )
    )

    rows = repository.read_all_rows()

    assert len(rows) == 1
    assert rows[0].status is Status.EXPIRING
    assert rows[0].expiry_date == date(2026, 4, 25)
    assert rows[0].expiry_email_sent_at == date(2026, 4, 10)


def test_csv_repository_clears_expiry_email_sent_at(tmp_path) -> None:
    csv_path = tmp_path / "sheet.csv"
    populated_row = (
        '2026/3/25 下午 1:00:05,我同意,John Doe,johndoe,johndoe@gmail.com,test,一個月,'
        '貢獻程式碼、文件內容等至開源軟體,test,1,1,1,"Load Balancer, GPU",friend,'
        "expiring,2026-04-25,2026-04-10\n"
    )
    csv_path.write_text(HEADER + populated_row, encoding="utf-8", newline="")

    repository = CsvRepository(str(csv_path))
    repository.write_row_update(
        RowUpdate(row_number=2, status=Status.ACTIVE, clear_expiry_email_sent_at=True)
    )

    rows = repository.read_all_rows()

    assert rows[0].status is Status.ACTIVE
    assert rows[0].expiry_date == date(2026, 4, 25)
    assert rows[0].expiry_email_sent_at is None


def test_csv_repository_raises_for_unknown_row_number(tmp_path) -> None:
    csv_path = tmp_path / "sheet.csv"
    csv_path.write_text(HEADER + ROW, encoding="utf-8", newline="")

    repository = CsvRepository(str(csv_path))

    with pytest.raises(ValueError, match="Unknown row number: 3"):
        repository.write_row_update(RowUpdate(row_number=3, status=Status.DELETED))
