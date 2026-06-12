"""Redaction tests for the CLI's secret-redacting log filter.

The CLI logs the full wrapped command line before executing it; these tests
pin that recognized secret shapes never reach the log stream while ordinary
operational text (periods, paths) passes through untouched. Mirrors
tools/usage_reports/tests/test_logging_config.py.
"""
from __future__ import annotations

import logging

from period_reconcile.logging_config import SecretRedactingFilter, _redact_string


def test_password_kv_is_redacted() -> None:
    assert _redact_string("password=hunter2") == "password=[REDACTED]"
    assert _redact_string("token: shhhh") == "token=[REDACTED]"


def test_bearer_token_is_redacted() -> None:
    redacted = _redact_string("Authorization: Bearer abcDEF123456ghijklmnop")
    assert "abcDEF123456ghijklmnop" not in redacted
    assert "[REDACTED]" in redacted


def test_prefixed_api_key_is_redacted() -> None:
    redacted = _redact_string("exec: tool --auth sk_abcdef12345678ABCD run")
    assert "sk_" not in redacted
    assert "[REDACTED]" in redacted


def test_plain_command_line_passes_through() -> None:
    msg = "job=usage-reports exec: /usr/local/bin/usage-reports generate --month 2026-05"
    assert _redact_string(msg) == msg


def test_filter_redacts_lazy_format_args() -> None:
    """Secrets must be caught in %s args, not just preformatted messages."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="exec: %s",
        args=("cmd --password=hunter2",),
        exc_info=None,
    )
    assert SecretRedactingFilter().filter(record)
    assert "hunter2" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()
