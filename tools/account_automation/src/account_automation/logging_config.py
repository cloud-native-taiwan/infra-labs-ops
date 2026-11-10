from __future__ import annotations

import logging

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


def describe_exception(exc: BaseException) -> str:
    """Return ``"<Type>: <message>"``: a one-line failure reason, no traceback."""
    return f"{type(exc).__name__}: {exc}"
