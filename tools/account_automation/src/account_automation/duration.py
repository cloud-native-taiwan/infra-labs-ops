from collections.abc import Callable
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta


def _add_days(days: int) -> Callable[[date], date]:
    return lambda value: value + timedelta(days=days)


def _add_months(months: int) -> Callable[[date], date]:
    return lambda value: value + relativedelta(months=months)


DURATION_MAP: dict[str, Callable[[date], date]] = {
    "兩週": _add_days(14),
    "一個月": _add_months(1),
    "三個月": _add_months(3),
    "六個月": _add_months(6),
}


def parse_duration(raw: str) -> Callable[[date], date]:
    normalized = raw.strip()
    try:
        return DURATION_MAP[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown duration: {raw}") from exc
