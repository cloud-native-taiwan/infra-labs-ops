from __future__ import annotations

import logging
import traceback

from infra_labs_common.redaction import SecretRedactingFilter, SecretRedactingFormatter

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"


def configure_logging(log_level: str) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level.upper())

    handler = logging.StreamHandler()
    handler.setLevel(log_level.upper())
    # The filter redacts the message; the formatter redacts exc_info
    # tracebacks and stack_info, which the filter never sees.
    handler.setFormatter(SecretRedactingFormatter(LOG_FORMAT))
    handler.addFilter(SecretRedactingFilter())

    root_logger.addHandler(handler)


# Helpers for logging a failure reason as message text: one line per row
# failure instead of a traceback, and redacted even by a handler that has only
# the filter.


def describe_exception(exc: BaseException) -> str:
    """Return ``"<Type>: <message>"`` for logging as (redacted) message text."""
    return f"{type(exc).__name__}: {exc}"


def format_traceback(exc: BaseException) -> str:
    """Return the full traceback of ``exc`` for logging as message text."""
    return "".join(traceback.format_exception(exc)).rstrip()
