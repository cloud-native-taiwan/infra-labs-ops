from __future__ import annotations

import logging
import traceback

from infra_labs_common.redaction import SecretRedactingFilter

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"


def configure_logging(log_level: str) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level.upper())

    handler = logging.StreamHandler()
    handler.setLevel(log_level.upper())
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(SecretRedactingFilter())

    root_logger.addHandler(handler)


# SecretRedactingFilter redacts only the formatted message, never exc_info
# tracebacks. Failure reasons are therefore logged as message text built by
# these helpers, never via LOGGER.exception / exc_info=True.


def describe_exception(exc: BaseException) -> str:
    """Return ``"<Type>: <message>"`` for logging as (redacted) message text."""
    return f"{type(exc).__name__}: {exc}"


def format_traceback(exc: BaseException) -> str:
    """Return the full traceback of ``exc`` for logging as message text."""
    return "".join(traceback.format_exception(exc)).rstrip()
