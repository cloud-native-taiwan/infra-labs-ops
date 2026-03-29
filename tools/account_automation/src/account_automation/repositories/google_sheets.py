import json
import logging
from pathlib import Path

import gspread

from account_automation.config import AppConfig
from account_automation.models import RowUpdate, SheetRow
from account_automation.repositories._sheet_mapping import (
    EXPIRY_DATE_COLUMN,
    EXPIRY_EMAIL_SENT_AT_COLUMN,
    STATUS_COLUMN,
    parse_sheet_row,
    serialize_row_update,
)
from account_automation.retry import STANDARD_RETRY

LOGGER = logging.getLogger(__name__)


class GoogleSheetsRepository:
    def __init__(self, config: AppConfig) -> None:
        client = gspread.service_account_from_dict(
            _load_service_account_info(config.google_service_account_json)
        )
        worksheet = client.open_by_key(config.spreadsheet_id).worksheet(config.worksheet_name)
        headers = worksheet.row_values(1)

        self._worksheet = worksheet
        self._column_indices = {
            STATUS_COLUMN: _find_column_index(headers, STATUS_COLUMN),
            EXPIRY_DATE_COLUMN: _find_column_index(headers, EXPIRY_DATE_COLUMN),
            EXPIRY_EMAIL_SENT_AT_COLUMN: _find_column_index(
                headers, EXPIRY_EMAIL_SENT_AT_COLUMN
            ),
        }

    @STANDARD_RETRY
    def read_all_rows(self) -> tuple[SheetRow, ...]:
        records = self._worksheet.get_all_records(head=1)
        rows: list[SheetRow] = []
        for row_number, record in enumerate(records, start=2):
            try:
                rows.append(parse_sheet_row(record, row_number))
            except Exception:
                LOGGER.warning("Failed to parse row %d — skipping", row_number, exc_info=True)
        return tuple(rows)

    @STANDARD_RETRY
    def write_row_update(self, update: RowUpdate) -> None:
        serialized_update = serialize_row_update(update)
        if not serialized_update:
            return

        batch = [
            {
                "range": _to_a1(self._column_indices[column_name], update.row_number),
                "values": [[value]],
            }
            for column_name, value in serialized_update.items()
        ]
        self._worksheet.batch_update(batch)


def _find_column_index(headers: list[str], column_name: str) -> int:
    try:
        return headers.index(column_name) + 1
    except ValueError as exc:
        raise ValueError(f"Missing required column: {column_name}") from exc


def _load_service_account_info(raw_value: str) -> dict[str, object]:
    if raw_value.lstrip().startswith(("{", "[")):
        raw_json = raw_value
    else:
        try:
            raw_json = Path(raw_value).read_text(encoding="utf-8")
        except FileNotFoundError:
            raw_json = raw_value

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON must be inline JSON or a path to a JSON file"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON must decode to a JSON object"
        )

    return payload


def _to_a1(column_number: int, row_number: int) -> str:
    letters = []
    remaining = column_number
    while remaining > 0:
        remaining, remainder = divmod(remaining - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return f"{''.join(reversed(letters))}{row_number}"
