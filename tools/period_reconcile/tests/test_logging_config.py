"""Tests for the CLI's logging setup.

The redaction filter's behavior is covered in infra_labs_common; here we pin
that ``configure_logging`` installs that filter on the root handler and uses the
CLI's log format, so secrets in the wrapped command line never reach the stream.
"""
from __future__ import annotations

import logging

from infra_labs_common.redaction import SecretRedactingFilter

from period_reconcile.logging_config import LOG_FORMAT, configure_logging


def test_configure_logging_installs_redacting_filter() -> None:
    configure_logging()
    root_logger = logging.getLogger()
    assert root_logger.level == logging.INFO
    handler = root_logger.handlers[0]
    assert any(isinstance(f, SecretRedactingFilter) for f in handler.filters)


def test_configure_logging_uses_expected_format() -> None:
    configure_logging()
    handler = logging.getLogger().handlers[0]
    assert handler.formatter is not None
    assert handler.formatter._fmt == LOG_FORMAT
