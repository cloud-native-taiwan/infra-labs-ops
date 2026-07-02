"""Transient-error classification and the shared retry decorator.

Only transient faults are retried; 4xx / auth / validation errors are not,
because repeating them just amplifies load or duplicates side effects.

This module imports tenacity / requests / keystoneauth1. It is imported only by
host tools that already pin those (account_automation, usage_reports); the
stdlib-only consumer (period_reconcile) never imports it.
"""
from __future__ import annotations

import contextlib

from keystoneauth1 import exceptions as ksa_exceptions
from requests import exceptions as requests_exceptions  # type: ignore[import-untyped]
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

# Only transient faults are worth retrying. Retrying 4xx / auth / validation
# errors just repeats a request that will keep failing (and can amplify load or
# duplicate side effects). Retried faults are: network connection failures,
# request timeouts, and HTTP 5xx / service-unavailable responses raised by
# openstacksdk, keystoneauth, or requests.
_TRANSIENT_EXCEPTION_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    requests_exceptions.ConnectionError,
    requests_exceptions.Timeout,
    ksa_exceptions.ConnectionError,
    ksa_exceptions.RetriableConnectionFailure,
)


def _has_server_error_status(exc: BaseException) -> bool:
    # Collect every place a 5xx status can hide across the SDKs we call.
    candidates: list[object] = [
        # openstacksdk exposes ``status_code``; keystoneauth uses ``http_status``.
        getattr(exc, "status_code", None),
        getattr(exc, "http_status", None),
    ]
    # requests.HTTPError and gspread.APIError carry it on ``exc.response``.
    response = getattr(exc, "response", None)
    if response is not None:
        candidates.append(getattr(response, "status_code", None))
    # Resend errors expose the HTTP status on ``exc.code`` (str or int).
    code = getattr(exc, "code", None)
    if code is not None:
        with contextlib.suppress(TypeError, ValueError):
            candidates.append(int(code))
    return any(
        isinstance(status, int) and 500 <= status < 600 for status in candidates
    )


def is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_EXCEPTION_TYPES):
        return True
    return _has_server_error_status(exc)


STANDARD_RETRY = retry(
    retry=retry_if_exception(is_transient_error),
    stop=stop_after_attempt(3),
    # Exponential backoff with jitter to avoid synchronized retry storms.
    wait=wait_exponential_jitter(initial=1, max=10),
    reraise=True,
)
