from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ResourceKind(StrEnum):
    INSTANCE = "instance"
    STORAGE = "storage"


@dataclass(frozen=True)
class ResourceCost:
    kind: ResourceKind
    resource_id: str
    name: str
    specs: str
    hours: float
    cost: float
    status: str = ""


@dataclass(frozen=True)
class ProjectUsage:
    project_id: str
    project_name: str
    resources: tuple[ResourceCost, ...] = ()

    @property
    def total_cost(self) -> float:
        return round(sum(r.cost for r in self.resources), 4)


@dataclass(frozen=True)
class ProjectMember:
    user_id: str
    user_name: str
    email: str


@dataclass(frozen=True)
class ReportPeriod:
    year: int
    month: int
    begin_utc: datetime
    end_utc: datetime

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(frozen=True)
class ReportData:
    period: ReportPeriod
    project: ProjectUsage
