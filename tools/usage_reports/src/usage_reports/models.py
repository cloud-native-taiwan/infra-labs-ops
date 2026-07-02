from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


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
class ResourceIndex:
    """Once-per-run snapshot of every server keyed by id, so instance enrichment
    is a dict lookup instead of an N+1 of per-resource GETs.

    Storage is not indexed: CloudKitty reports it as a project-level aggregate
    (resource_id=""), so storage rows never reach a per-resource lookup. A
    per-volume index would need reintroducing here if per-volume itemization
    ever lands.
    """

    servers: Mapping[str, Any]


@dataclass(frozen=True)
class ProjectMembership:
    """Result of resolving a project's members.

    ``unresolved_user_ids`` holds users whose lookup failed transiently after
    retries -- they are NOT silently dropped: the caller surfaces them so the
    run reports an incomplete recipient list and exits non-zero.
    """

    members: tuple["ProjectMember", ...] = ()
    unresolved_user_ids: tuple[str, ...] = ()


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
