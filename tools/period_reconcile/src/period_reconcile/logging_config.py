"""Logging setup with secret redaction.

The CLI logs the full wrapped command line before executing it; an operator
may pass credentials there (e.g. ``--token ...``). The shared
``SecretRedactingFilter`` redacts recognized secret shapes before anything
reaches the log stream.
"""
from __future__ import annotations

import logging

from infra_labs_common.redaction import SecretRedactingFilter

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(SecretRedactingFilter())

    root_logger.addHandler(handler)
