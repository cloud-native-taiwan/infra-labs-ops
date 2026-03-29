from typing import Protocol

from account_automation.models import RowUpdate, SheetRow


class SheetRepository(Protocol):
    """Repository interface for reading rows and applying partial row updates.

    In RowUpdate, ``None`` fields mean "do not write this field". Implementations
    must not write or clear fields where RowUpdate has ``None``.
    """

    def read_all_rows(self) -> tuple[SheetRow, ...]:
        ...

    def write_row_update(self, update: RowUpdate) -> None:
        ...
