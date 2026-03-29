from __future__ import annotations

import logging
import re
from typing import Any


TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,}$")
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"


class PasswordRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.args = _redact_args(record.args)
        return True


def configure_logging(log_level: str) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level.upper())

    handler = logging.StreamHandler()
    handler.setLevel(log_level.upper())
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(PasswordRedactingFilter())

    root_logger.addHandler(handler)


def _redact_args(args: Any) -> Any:
    if isinstance(args, tuple):
        return tuple(_redact_value(value) for value in args)
    if isinstance(args, dict):
        return {key: _redact_value(value) for key, value in args.items()}
    return _redact_value(args)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str) and TOKEN_PATTERN.fullmatch(value):
        return "[REDACTED]"
    return value
