"""Logging setup with secret redaction.

The CLI logs the full wrapped command line before executing it; an operator
may pass credentials there (e.g. ``--token ...``). Redact recognized secret
shapes before anything reaches the log stream. Pattern set and filter kept in
sync with tools/usage_reports/src/usage_reports/logging_config.py.
"""
from __future__ import annotations

import logging
import re

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
        # Redact AFTER %-interpolation. Rewriting the format string first (e.g.
        # turning `password=%s` into `password=[REDACTED]`) would leave
        # record.args with a now-unconsumed value, so getMessage() would raise
        # TypeError and logging would drop the line via handleError.
        # getMessage() interpolates msg % args safely; we redact the result and
        # clear args so the handler does not interpolate again. exc_info /
        # stack_info are left untouched.
        record.msg = _redact_string(record.getMessage())
        record.args = None
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
