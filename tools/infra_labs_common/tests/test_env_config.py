from __future__ import annotations

import pytest

from infra_labs_common.env_config import (
    ENV_PREFIX,
    get_bool,
    get_field,
    get_int,
    get_optional,
    get_required,
    is_empty,
)


def test_prefix() -> None:
    assert ENV_PREFIX == "INFRA_LABS_"


def test_get_required_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRA_LABS_FOO", "bar")
    assert get_required("FOO") == "bar"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_get_required_absent_raises(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("INFRA_LABS_FOO", raising=False)
    else:
        monkeypatch.setenv("INFRA_LABS_FOO", value)
    with pytest.raises(ValueError, match="INFRA_LABS_FOO"):
        get_required("FOO")


def test_get_optional_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRA_LABS_FOO", raising=False)
    assert get_optional("FOO", "fallback") == "fallback"
    monkeypatch.setenv("INFRA_LABS_FOO", "  ")
    assert get_optional("FOO", "fallback") == "fallback"
    monkeypatch.setenv("INFRA_LABS_FOO", "set")
    assert get_optional("FOO", "fallback") == "set"


def test_get_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRA_LABS_N", raising=False)
    assert get_int("N", 7) == 7
    monkeypatch.setenv("INFRA_LABS_N", "42")
    assert get_int("N", 7) == 42


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("OFF", False),
    ],
)
def test_get_bool_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("INFRA_LABS_B", raw)
    assert get_bool("B", not expected) is expected


def test_get_bool_default_and_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRA_LABS_B", raising=False)
    assert get_bool("B", True) is True
    monkeypatch.setenv("INFRA_LABS_B", "maybe")
    with pytest.raises(ValueError, match="Invalid boolean"):
        get_bool("B", False)


def test_is_empty() -> None:
    assert is_empty(None) is True
    assert is_empty("") is True
    assert is_empty("   ") is True
    assert is_empty("x") is False


def test_get_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRA_LABS_F", raising=False)
    # require_all=False falls back to empty string when absent.
    assert get_field("F", require_all=False) == ""
    # require_all=True raises when absent.
    with pytest.raises(ValueError, match="INFRA_LABS_F"):
        get_field("F", require_all=True)
    monkeypatch.setenv("INFRA_LABS_F", "v")
    assert get_field("F", require_all=True) == "v"
