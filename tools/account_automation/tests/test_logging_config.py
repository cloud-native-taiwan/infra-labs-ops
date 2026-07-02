import logging

from account_automation.logging_config import (
    SecretRedactingFilter,
    configure_logging,
)


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_redacts_prefixed_key_in_message() -> None:
    record = _record("using api key re_1234567890abcdef")
    SecretRedactingFilter().filter(record)

    message = record.getMessage()
    assert "re_1234567890abcdef" not in message
    assert "[REDACTED]" in message


def test_redacts_secret_embedded_in_format_string() -> None:
    # A secret interpolated into the format string itself (not via args) is the
    # case the old args-only filter missed.
    record = _record("connecting with password=hunter2secret to host")
    SecretRedactingFilter().filter(record)

    message = record.getMessage()
    assert "hunter2secret" not in message
    assert "password=[REDACTED]" in message


def test_redacts_bearer_token_in_args() -> None:
    record = _record("auth header %s", "Bearer abcdefghijkl12345")
    SecretRedactingFilter().filter(record)

    assert "abcdefghijkl12345" not in record.getMessage()


def test_preserves_uuid_values() -> None:
    # The diverged old filter over-redacted any 20+ char token, eating UUIDs.
    uuid = "11111111-2222-3333-4444-555555555555"
    record = _record("project id %s", uuid)
    SecretRedactingFilter().filter(record)

    assert uuid in record.getMessage()


def test_percent_style_secret_in_format_string_does_not_raise() -> None:
    # Regression: the old filter rewrote the format string before interpolation,
    # so `password=%s` became `password=[REDACTED]` while record.args still held
    # the value -- getMessage() then raised TypeError and logging dropped the
    # line. The filter must interpolate first, then redact.
    record = _record("sending password=%s to user=%s", "s3cr3t-value", "alice")
    SecretRedactingFilter().filter(record)

    message = record.getMessage()  # must not raise
    assert "s3cr3t-value" not in message
    assert "alice" in message
    assert "password=[REDACTED]" in message


def test_configure_logging_installs_redacting_filter() -> None:
    configure_logging("DEBUG")
    root_logger = logging.getLogger()

    assert root_logger.level == logging.DEBUG
    handler = root_logger.handlers[0]
    assert any(isinstance(f, SecretRedactingFilter) for f in handler.filters)
