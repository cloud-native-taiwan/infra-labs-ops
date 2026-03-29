from dataclasses import dataclass
from datetime import date
from enum import StrEnum, auto


class Status(StrEnum):
    APPROVED = auto()
    ACTIVE = auto()
    EXPIRING = auto()
    EXPIRED = auto()
    PENDING_DELETE = auto()
    RENEWAL_REQUESTED = auto()
    DELETED = auto()


@dataclass(frozen=True)
class ResourceQuota:
    vcpus: int
    ram_gb: int
    storage_gb: int
    extras: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SheetRow:
    row_number: int
    timestamp: str
    name: str
    username: str
    email: str
    purpose: str
    duration_raw: str
    quota: ResourceQuota
    status: Status | None
    expiry_date: date | None
    expiry_email_sent_at: date | None


@dataclass(frozen=True)
class RowUpdate:
    row_number: int
    status: Status | None = None
    expiry_date: date | None = None
    expiry_email_sent_at: date | None = None


@dataclass(frozen=True)
class ProcessingResult:
    row: SheetRow
    update: RowUpdate | None
    success: bool
    message: str

    @classmethod
    def skip(cls, row: SheetRow) -> "ProcessingResult":
        return cls(row=row, update=None, success=True, message="")

    @classmethod
    def failure(cls, row: SheetRow, message: str) -> "ProcessingResult":
        return cls(row=row, update=None, success=False, message=message)
