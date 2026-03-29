from datetime import date

import pytest

from account_automation.duration import parse_duration


@pytest.mark.parametrize(
    ("raw", "start_date", "expected"),
    [
        ("兩週", date(2026, 3, 25), date(2026, 4, 8)),
        ("一個月", date(2026, 3, 25), date(2026, 4, 25)),
        ("三個月", date(2026, 3, 25), date(2026, 6, 25)),
        ("六個月", date(2026, 3, 25), date(2026, 9, 25)),
    ],
)
def test_parse_duration_supported_values(raw: str, start_date: date, expected: date) -> None:
    adder = parse_duration(raw)

    assert adder(start_date) == expected


def test_parse_duration_unknown_value_raises() -> None:
    with pytest.raises(ValueError, match="Unknown duration"):
        parse_duration("一年")


def test_parse_duration_handles_month_end() -> None:
    adder = parse_duration("一個月")

    assert adder(date(2025, 1, 31)) == date(2025, 2, 28)
