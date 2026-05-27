from __future__ import annotations

from usage_reports.logging_config import _redact_string


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


def test_bearer_token_is_redacted() -> None:
    text = "Authorization: Bearer abcDEF123456ghijklmnop"
    redacted = _redact_string(text)
    assert "abcDEF123456ghijklmnop" not in redacted
    assert "[REDACTED]" in redacted


def test_password_kv_is_redacted() -> None:
    assert _redact_string("password=hunter2") == "password=[REDACTED]"
    assert _redact_string("api_key: shhhh") == "api_key=[REDACTED]"


def test_plain_log_message_passes_through() -> None:
    msg = "Project name=lab-alpha members=3 total_cost=4.20"
    assert _redact_string(msg) == msg
