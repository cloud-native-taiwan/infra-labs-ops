from __future__ import annotations

import logging

from infra_labs_common.redaction import SecretRedactingFilter, _redact_string


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


def test_uuid_is_not_redacted() -> None:
    """UUIDs are not secrets and must survive redaction so operators can debug."""
    uuid_str = "12345678-1234-1234-1234-123456789abc"
    # `project_id=` is not in the redacted-keys list, so the UUID is preserved
    assert uuid_str in _redact_string(f"project_id={uuid_str}")
    # A bare UUID is preserved
    assert uuid_str in _redact_string(f"Processing {uuid_str}")
    # A 32-hex string (no dashes) is preserved
    assert "abcdef0123456789abcdef0123456789" in _redact_string(
        "uuid=abcdef0123456789abcdef0123456789"
    )


def test_resend_key_is_redacted() -> None:
    text = "Sending with re_abcdef12345678ABCD success"
    assert "re_" not in _redact_string(text)
    assert "[REDACTED]" in _redact_string(text)


def test_prefixed_api_key_is_redacted() -> None:
    redacted = _redact_string("exec: tool --auth sk_abcdef12345678ABCD run")
    assert "sk_" not in redacted
    assert "[REDACTED]" in redacted


def test_bearer_token_is_redacted() -> None:
    text = "Authorization: Bearer abcDEF123456ghijklmnop"
    redacted = _redact_string(text)
    assert "abcDEF123456ghijklmnop" not in redacted
    assert "[REDACTED]" in redacted


def test_password_and_token_kv_are_redacted() -> None:
    assert _redact_string("password=hunter2") == "password=[REDACTED]"
    assert _redact_string("api_key: shhhh") == "api_key=[REDACTED]"
    assert _redact_string("token: shhhh") == "token=[REDACTED]"


def test_plain_message_passes_through() -> None:
    msg = "Project name=lab-alpha members=3 total_cost=4.20"
    assert _redact_string(msg) == msg


def test_filter_redacts_secret_embedded_in_message() -> None:
    record = _record("connecting with password=hunter2secret to host")
    SecretRedactingFilter().filter(record)
    message = record.getMessage()
    assert "hunter2secret" not in message
    assert "password=[REDACTED]" in message


def test_filter_redacts_bearer_token_in_args() -> None:
    record = _record("auth header %s", "Bearer abcdefghijkl12345")
    assert SecretRedactingFilter().filter(record)
    assert "abcdefghijkl12345" not in record.getMessage()


def test_filter_preserves_uuid_values() -> None:
    uuid = "11111111-2222-3333-4444-555555555555"
    record = _record("project id %s", uuid)
    SecretRedactingFilter().filter(record)
    assert uuid in record.getMessage()


def test_filter_redacts_lazy_format_args() -> None:
    """Secrets must be caught in %s args, not just preformatted messages."""
    record = _record("exec: %s", "cmd --password=hunter2")
    assert SecretRedactingFilter().filter(record)
    assert "hunter2" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()


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
