from types import SimpleNamespace

import pytest
import requests.exceptions as req_exc
from keystoneauth1 import exceptions as ksa_exc

from infra_labs_common.retry import STANDARD_RETRY, is_transient_error


class _ResendError(Exception):
    """Stand-in for resend's error type, which exposes HTTP status on .code."""

    def __init__(self, code: str | int) -> None:
        super().__init__(code)
        self.code = code


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError(),
        TimeoutError(),
        req_exc.ConnectionError(),
        req_exc.Timeout(),
        req_exc.ConnectTimeout(),
        req_exc.ReadTimeout(),
        ksa_exc.RetriableConnectionFailure(),
    ],
)
def test_connection_and_timeout_errors_are_transient(exc: BaseException) -> None:
    assert is_transient_error(exc) is True


def test_server_error_status_is_transient() -> None:
    class ServiceUnavailable(Exception):
        status_code = 503

    class InternalError(Exception):
        http_status = 500

    assert is_transient_error(ServiceUnavailable()) is True
    assert is_transient_error(InternalError()) is True


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad input"),
        KeyError("missing"),
        RuntimeError("boom"),
    ],
)
def test_generic_errors_are_not_transient(exc: BaseException) -> None:
    assert is_transient_error(exc) is False


def test_client_error_status_is_not_transient() -> None:
    class NotFound(Exception):
        status_code = 404

    class Unauthorized(Exception):
        http_status = 401

    assert is_transient_error(NotFound()) is False
    assert is_transient_error(Unauthorized()) is False


def test_nested_response_5xx_is_transient() -> None:
    # requests.HTTPError / gspread.APIError carry the status on exc.response.
    err = req_exc.HTTPError(response=SimpleNamespace(status_code=503))
    assert is_transient_error(err) is True

    not_found = req_exc.HTTPError(response=SimpleNamespace(status_code=404))
    assert is_transient_error(not_found) is False


def test_resend_code_5xx_is_transient() -> None:
    # Resend errors expose the HTTP status on exc.code as str or int.
    assert is_transient_error(_ResendError(code="503")) is True
    assert is_transient_error(_ResendError(code=500)) is True
    assert is_transient_error(_ResendError(code="429")) is False
    # Non-numeric codes must not raise and must not be treated as transient.
    assert is_transient_error(_ResendError(code="rate_limit_exceeded")) is False


def test_standard_retry_retries_transient_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    calls = {"n": 0}

    @STANDARD_RETRY
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise req_exc.ConnectionError("transient")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_standard_retry_does_not_retry_non_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    calls = {"n": 0}

    @STANDARD_RETRY
    def bad() -> None:
        calls["n"] += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        bad()
    assert calls["n"] == 1
