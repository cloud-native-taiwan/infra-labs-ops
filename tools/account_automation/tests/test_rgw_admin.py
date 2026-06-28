from unittest.mock import patch

import pytest
import requests

from account_automation.services.rgw_admin import RgwAdminClient


# A realistic implicit-tenant UID and its project-id half, used by delete tests.
_PID = "11111111111111111111111111111111"
_UID = f"{_PID}${_PID}"


def _prepare_request(client: RgwAdminClient, url: str) -> requests.PreparedRequest:
    request = requests.Request("GET", url, params={"uid": "test"})
    return client._session.prepare_request(request)


def test_prepared_request_includes_host_with_explicit_port() -> None:
    client = RgwAdminClient(
        "https://s3.cloudnative.tw:6780",
        "access-key",
        "secret-key",
        "cloudnative",
    )

    prepared = _prepare_request(client, "https://s3.cloudnative.tw:6780/admin/bucket")

    assert prepared.headers["Host"] == "s3.cloudnative.tw:6780"


def test_prepared_request_includes_host_without_default_https_port() -> None:
    client = RgwAdminClient(
        "https://rgw.example.com",
        "access-key",
        "secret-key",
        "cloudnative",
    )

    prepared = _prepare_request(client, "https://rgw.example.com/admin/bucket")

    assert prepared.headers["Host"] == "rgw.example.com"


def _make_client() -> RgwAdminClient:
    return RgwAdminClient(
        "https://s3.cloudnative.tw:6780",
        "access-key",
        "secret-key",
        "cloudnative",
    )


def _mock_response(status_code: int) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = b""
    return resp


def test_delete_bucket_succeeds_on_200() -> None:
    client = _make_client()
    with patch.object(client._session, "delete", return_value=_mock_response(200)):
        client.delete_bucket("test-bucket")


def test_delete_bucket_treats_404_as_success() -> None:
    client = _make_client()
    with patch.object(client._session, "delete", return_value=_mock_response(404)):
        client.delete_bucket("already-gone")


def test_delete_bucket_passes_tenant_param() -> None:
    client = _make_client()
    with patch.object(client._session, "delete", return_value=_mock_response(200)) as mock_del:
        client.delete_bucket("my-bucket", tenant=_PID)

    _, kwargs = mock_del.call_args
    assert kwargs["params"]["tenant"] == _PID
    assert kwargs["params"]["bucket"] == "my-bucket"


def test_delete_bucket_omits_tenant_when_empty() -> None:
    client = _make_client()
    with patch.object(client._session, "delete", return_value=_mock_response(200)) as mock_del:
        client.delete_bucket("my-bucket")

    _, kwargs = mock_del.call_args
    assert "tenant" not in kwargs["params"]


def test_delete_bucket_raises_on_500() -> None:
    client = _make_client()
    with patch.object(client._session, "delete", return_value=_mock_response(500)):
        try:
            client.delete_bucket("test-bucket")
            assert False, "Expected HTTPError"
        except requests.HTTPError:
            pass


def test_delete_user_succeeds_on_200() -> None:
    client = _make_client()
    with patch.object(client._session, "delete", return_value=_mock_response(200)):
        client.delete_user(_UID)


def test_delete_user_treats_404_as_success() -> None:
    client = _make_client()
    with patch.object(client._session, "delete", return_value=_mock_response(404)):
        client.delete_user(_UID)


def test_delete_implicit_tenant_user_formats_uid() -> None:
    client = _make_client()
    with patch.object(client, "delete_user") as mock_delete_user:
        client.delete_implicit_tenant_user(_PID)

    mock_delete_user.assert_called_once_with(_UID)


def test_init_rejects_non_https_url() -> None:
    with pytest.raises(ValueError, match="not HTTPS"):
        RgwAdminClient("http://rgw.example.com", "a", "s")


@pytest.mark.parametrize("url", [
    "http://localhost:6780",
    "http://127.0.0.1:6780",
    "http://[::1]:6780",
])
def test_init_allows_loopback_over_http(url: str) -> None:
    # Should not raise; loopback is treated as a secure context.
    RgwAdminClient(url, "a", "s")


def test_delete_user_rejects_non_implicit_uid() -> None:
    client = _make_client()
    with pytest.raises(ValueError, match="implicit-tenant"):
        client.delete_user("some-arbitrary-uid")


def test_delete_user_rejects_mismatched_halves() -> None:
    # The backreference must reject <pid1>$<pid2> with pid1 != pid2.
    other = "22222222222222222222222222222222"
    client = _make_client()
    with pytest.raises(ValueError, match="implicit-tenant"):
        client.delete_user(f"{_PID}${other}")


def test_delete_user_rejects_trailing_newline() -> None:
    # fullmatch (not match) must reject a UID with a trailing newline.
    client = _make_client()
    with pytest.raises(ValueError, match="implicit-tenant"):
        client.delete_user(f"{_UID}\n")


def test_delete_bucket_rejects_malformed_tenant() -> None:
    client = _make_client()
    with pytest.raises(ValueError, match="project-id shape"):
        client.delete_bucket("my-bucket", tenant="not-a-project-id")


def test_delete_bucket_rejects_tenant_with_trailing_newline() -> None:
    client = _make_client()
    with pytest.raises(ValueError, match="project-id shape"):
        client.delete_bucket("my-bucket", tenant=f"{_PID}\n")


def test_init_rejects_loopback_host_confusion() -> None:
    # A userinfo-prefixed URL must not be treated as loopback: the real host
    # is evil.com, so the cleartext guard must still fire.
    with pytest.raises(ValueError, match="not HTTPS"):
        RgwAdminClient("http://localhost@evil.com/", "a", "s")


def test_list_implicit_tenant_uids_uses_user_list_pagination() -> None:
    client = _make_client()
    page_one = {
        "keys": [
            "11111111111111111111111111111111$11111111111111111111111111111111",
            "not-an-implicit-user",
        ],
        "truncated": True,
        "marker": "page-2",
    }
    page_two = {
        "keys": [
            "22222222-2222-2222-2222-222222222222$22222222-2222-2222-2222-222222222222",
        ],
        "truncated": False,
    }

    with patch.object(client, "_get", side_effect=[page_one, page_two]) as mock_get:
        result = client.list_implicit_tenant_uids()

    assert result == [
        "11111111111111111111111111111111$11111111111111111111111111111111",
        "22222222-2222-2222-2222-222222222222$22222222-2222-2222-2222-222222222222",
    ]
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].args == ("user",)
    assert mock_get.call_args_list[0].kwargs == {"list": "true"}
    assert mock_get.call_args_list[1].args == ("user",)
    assert mock_get.call_args_list[1].kwargs == {"list": "true", "marker": "page-2"}


def test_list_implicit_tenant_uids_returns_empty_on_unexpected_shape() -> None:
    client = _make_client()

    with patch.object(client, "_get", return_value=["unexpected"]):
        result = client.list_implicit_tenant_uids()

    assert result == []
