from typing import Protocol

from account_automation.models import InvalidRow, RowUpdate, SheetRow


class SheetRepository(Protocol):
    """Repository interface for reading rows and applying partial row updates.

    ``read_all_rows`` returns an InvalidRow (never raises) for a row whose
    cells cannot be parsed, so callers can report it.

    In RowUpdate, ``None`` fields mean "do not write this field". Implementations
    must not write or clear fields where RowUpdate has ``None``.
    """

    def read_all_rows(self) -> tuple[SheetRow | InvalidRow, ...]:
        ...

    def write_row_update(self, update: RowUpdate) -> None:
        ...
