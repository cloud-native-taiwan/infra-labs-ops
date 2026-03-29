from collections.abc import Mapping
from datetime import date

from account_automation.models import ResourceQuota, RowUpdate, SheetRow
from account_automation.validators import validate_extras, validate_status


TIMESTAMP_COLUMN = "時間戳記"
NAME_COLUMN = "姓名"
USERNAME_COLUMN = "使用者名稱"
EMAIL_COLUMN = "Email"
PURPOSE_COLUMN = "使用用途"
DURATION_COLUMN = "使用時間"
VCPUS_COLUMN = "vCPU 數量"
RAM_GB_COLUMN = "記憶體 (GB)"
STORAGE_GB_COLUMN = "儲存空間 (GB)"
EXTRAS_COLUMN = "其餘設備"
STATUS_COLUMN = "Status"
EXPIRY_DATE_COLUMN = "ExpiryDate"
EXPIRY_EMAIL_SENT_AT_COLUMN = "ExpiryEmailSentAt"


def parse_sheet_row(record: Mapping[str, object], row_number: int) -> SheetRow:
    return SheetRow(
        row_number=row_number,
        timestamp=_get_text(record, TIMESTAMP_COLUMN),
        name=_get_text(record, NAME_COLUMN),
        username=_get_text(record, USERNAME_COLUMN),
        email=_get_text(record, EMAIL_COLUMN),
        purpose=_get_text(record, PURPOSE_COLUMN),
        duration_raw=_get_text(record, DURATION_COLUMN),
        quota=ResourceQuota(
            vcpus=_get_int(record, VCPUS_COLUMN),
            ram_gb=_get_int(record, RAM_GB_COLUMN),
            storage_gb=_get_int(record, STORAGE_GB_COLUMN),
            extras=validate_extras(_get_text(record, EXTRAS_COLUMN)),
        ),
        status=validate_status(_get_text(record, STATUS_COLUMN)),
        expiry_date=_parse_optional_date(_get_text(record, EXPIRY_DATE_COLUMN)),
        expiry_email_sent_at=_parse_optional_date(
            _get_text(record, EXPIRY_EMAIL_SENT_AT_COLUMN)
        ),
    )


def serialize_row_update(update: RowUpdate) -> dict[str, str]:
    serialized: dict[str, str] = {}
    if update.status is not None:
        serialized[STATUS_COLUMN] = update.status.value
    if update.expiry_date is not None:
        serialized[EXPIRY_DATE_COLUMN] = update.expiry_date.isoformat()
    if update.expiry_email_sent_at is not None:
        serialized[EXPIRY_EMAIL_SENT_AT_COLUMN] = update.expiry_email_sent_at.isoformat()
    return serialized


def _get_text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _get_int(record: Mapping[str, object], key: str) -> int:
    return int(_get_text(record, key))


def _parse_optional_date(raw: str) -> date | None:
    if raw == "":
        return None
    return date.fromisoformat(raw)
