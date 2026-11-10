import logging
from collections.abc import Mapping
from datetime import date

from account_automation.logging_config import describe_exception
from account_automation.models import InvalidRow, ResourceQuota, RowUpdate, SheetRow
from account_automation.validators import parse_status, validate_extras, validate_status

LOGGER = logging.getLogger(__name__)


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
DELETE_PREVIEW_SENT_AT_COLUMN = "DeletePreviewSentAt"


def parse_sheet_row(record: Mapping[str, object], row_number: int) -> SheetRow:
    # Status first (it never raises), so an unknown status is reported before
    # any other cell can fail; parse_record then re-reads it quietly.
    status = validate_status(_get_text(record, STATUS_COLUMN), row_number)
    return SheetRow(
        row_number=row_number,
        timestamp=_get_text(record, TIMESTAMP_COLUMN),
        name=_get_text(record, NAME_COLUMN),
        username=_get_text(record, USERNAME_COLUMN),
        email=_get_text(record, EMAIL_COLUMN),
        purpose=_get_text(record, PURPOSE_COLUMN),
        duration_raw=_get_text(record, DURATION_COLUMN),
        quota=ResourceQuota(
            vcpus=_get_optional_int(record, VCPUS_COLUMN),
            ram_gb=_get_optional_int(record, RAM_GB_COLUMN),
            storage_gb=_get_optional_int(record, STORAGE_GB_COLUMN),
            extras=validate_extras(_get_text(record, EXTRAS_COLUMN)),
        ),
        status=status,
        expiry_date=_parse_optional_date(_get_text(record, EXPIRY_DATE_COLUMN)),
        expiry_email_sent_at=_parse_optional_date(
            _get_text(record, EXPIRY_EMAIL_SENT_AT_COLUMN)
        ),
        delete_preview_sent_at=_parse_optional_date(
            _get_text(record, DELETE_PREVIEW_SENT_AT_COLUMN)
        ),
    )


def parse_record(record: Mapping[str, object], row_number: int) -> SheetRow | InvalidRow:
    """Parse one sheet record, returning an InvalidRow if any cell is malformed.

    The orchestrator decides whether the bad row matters (only rows whose
    Status has a processor are reported), so this logs at DEBUG only.
    """
    try:
        return parse_sheet_row(record, row_number)
    except Exception as exc:
        reason = describe_exception(exc)
        LOGGER.debug("Failed to parse row %d: %s", row_number, reason)
        return InvalidRow(
            row_number=row_number,
            username=_get_text(record, USERNAME_COLUMN),
            status=parse_status(_get_text(record, STATUS_COLUMN)),
            reason=reason,
        )


def serialize_row_update(update: RowUpdate) -> dict[str, str]:
    serialized: dict[str, str] = {}
    if update.status is not None:
        serialized[STATUS_COLUMN] = update.status.value
    if update.expiry_date is not None:
        serialized[EXPIRY_DATE_COLUMN] = update.expiry_date.isoformat()
    if update.clear_expiry_email_sent_at:
        serialized[EXPIRY_EMAIL_SENT_AT_COLUMN] = ""
    elif update.expiry_email_sent_at is not None:
        serialized[EXPIRY_EMAIL_SENT_AT_COLUMN] = update.expiry_email_sent_at.isoformat()
    if update.delete_preview_sent_at is not None:
        serialized[DELETE_PREVIEW_SENT_AT_COLUMN] = update.delete_preview_sent_at.isoformat()
    return serialized


def _get_text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _get_optional_int(record: Mapping[str, object], key: str) -> int | None:
    text = _get_text(record, key)
    if text == "":
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"Non-numeric value {text!r} in column {key}") from exc


def _parse_optional_date(raw: str) -> date | None:
    if raw == "":
        return None
    return date.fromisoformat(raw)
