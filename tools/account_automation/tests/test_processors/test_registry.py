from account_automation.models import Status
from account_automation.processors import (
    active,
    approved,
    expiring,
    pending_delete,
    ready_to_delete,
    registry,
)


def test_get_processor_returns_expected_handlers() -> None:
    assert registry.get_processor(Status.APPROVED) is approved.process
    assert registry.get_processor(Status.ACTIVE) is active.process
    assert registry.get_processor(Status.EXPIRING) is expiring.process
    assert registry.get_processor(Status.PENDING_DELETE) is pending_delete.process
    assert registry.get_processor(Status.READY_TO_DELETE) is ready_to_delete.process


def test_get_processor_returns_none_for_unregistered_status() -> None:
    assert registry.get_processor(Status.EXPIRED) is None
    assert registry.get_processor(Status.RENEWAL_REQUESTED) is None
    assert registry.get_processor(Status.DELETED) is None
