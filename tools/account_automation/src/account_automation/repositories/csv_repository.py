import csv
from pathlib import Path

from account_automation.models import InvalidRow, RowUpdate, SheetRow
from account_automation.repositories._sheet_mapping import parse_record, serialize_row_update


class CsvRepository:
    def __init__(self, csv_path: str) -> None:
        self._csv_path = Path(csv_path)

    def read_all_rows(self) -> tuple[SheetRow | InvalidRow, ...]:
        with self._csv_path.open("r", encoding="utf-8", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            return tuple(
                parse_record(record, row_number)
                for row_number, record in enumerate(reader, start=2)
            )

    def write_row_update(self, update: RowUpdate) -> None:
        serialized_update = serialize_row_update(update)
        if not serialized_update:
            return

        with self._csv_path.open("r", encoding="utf-8", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            fieldnames = reader.fieldnames
            rows = list(reader)

        if fieldnames is None:
            raise ValueError("CSV file is missing a header row")

        row_index = update.row_number - 2
        if row_index < 0 or row_index >= len(rows):
            raise ValueError(f"Unknown row number: {update.row_number}")

        updated_row = dict(rows[row_index])
        updated_row.update(serialized_update)
        rows[row_index] = updated_row

        with self._csv_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
