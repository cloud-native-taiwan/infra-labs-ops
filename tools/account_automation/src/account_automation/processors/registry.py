from collections.abc import Callable
from datetime import date

from account_automation.config import AppConfig
from account_automation.models import ProcessingResult, SheetRow, Status
from account_automation.processors import (
    active,
    approved,
    expiring,
    pending_delete,
    ready_to_delete,
)
from account_automation.services.email_service import EmailService
from account_automation.services.openstack_service import OpenStackService


Processor = Callable[
    [SheetRow, date, AppConfig, OpenStackService, EmailService],
    ProcessingResult,
]


PROCESSORS: dict[Status, Processor] = {
    Status.APPROVED: approved.process,
    Status.ACTIVE: active.process,
    Status.EXPIRING: expiring.process,
    Status.PENDING_DELETE: pending_delete.process,
    Status.READY_TO_DELETE: ready_to_delete.process,
}


def get_processor(status: Status) -> Processor | None:
    return PROCESSORS.get(status)
