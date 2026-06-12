"""Logging setup with secret redaction.

The CLI logs the full wrapped command line before executing it; an operator
may pass credentials there (e.g. ``--token ...``). Redact recognized secret
shapes before anything reaches the log stream. Pattern set kept in sync with
tools/usage_reports/src/usage_reports/logging_config.py.
"""
from __future__ import annotations

import logging
import re
from typing import Any

# Match recognized secret shapes without scooping up every OpenStack UUID.
# - Resend keys: `re_` prefix
# - Generic API keys with `sk_`, `pk_`, `key_` prefixes
# - Bearer tokens after the `Bearer ` keyword
# - Anything following `Authorization:` / `password=` / `token=` / `api_key=`
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:re|sk|pk|rk|key)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=]{12,}"),
    re.compile(
        r"(?i)(authorization|password|passwd|token|api[_-]?key|secret)\s*[=:]\s*\S+"
    ),
)
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.args = _redact_args(record.args)
        if isinstance(record.msg, str):
            record.msg = _redact_string(record.msg)
        return True


def configure_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(SecretRedactingFilter())

    root_logger.addHandler(handler)


def _redact_string(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(_replacement, text)
    return text


def _replacement(match: re.Match[str]) -> str:
    if match.lastindex:
        # Keep the labelled key (e.g. `password=`), redact the value
        return f"{match.group(1)}=[REDACTED]"
    return "[REDACTED]"


def _redact_args(args: Any) -> Any:
    if isinstance(args, tuple):
        return tuple(_redact_value(value) for value in args)
    if isinstance(args, dict):
        return {key: _redact_value(value) for key, value in args.items()}
    return _redact_value(args)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_string(value)
    return value
