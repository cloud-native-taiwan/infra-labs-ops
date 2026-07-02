"""Unit tests for the CloudKitty rating pyscript (scripts/rate.py).

rate.py is exec()'d by cloudkitty with an injected `data`, so its last line
(`_process(data)`) cannot run at import time. `_load_rate_module` strips that
entrypoint and execs the rest into an isolated module so the pure pricing
functions can be tested directly, without Nova or cloudkitty in the loop.
"""
from __future__ import annotations

import types
from decimal import Decimal
from pathlib import Path

import pytest

RATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "rate.py"
_ENTRYPOINT = "_process(data)"


def _load_rate_module() -> types.ModuleType:
    source = RATE_PATH.read_text(encoding="utf-8")
    body = "\n".join(ln for ln in source.splitlines() if not ln.startswith(_ENTRYPOINT))
    module = types.ModuleType("rate_under_test")
    module.__file__ = str(RATE_PATH)
    exec(compile(body, str(RATE_PATH), "exec"), module.__dict__)  # noqa: S102
    return module


@pytest.fixture
def rate() -> types.ModuleType:
    mod = _load_rate_module()
    # Start every test from an empty, non-expiring cache so no test refreshes
    # from Nova. State lives on `sys` (shared across module loads), so reset it.
    mod._state()["cache"] = {}
    mod._state()["refreshed_at"] = 0.0
    return mod


def _info(vcpus: int, ram_mb: int, gpu_alias: str | None = None, gpu_count: int = 0) -> dict:
    return {
        "vcpus": vcpus,
        "ram_mb": ram_mb,
        "gpu_alias": gpu_alias,
        "gpu_count": gpu_count,
        "flavor_id": "flv-1",
        "flavor_name": "m1.test",
    }


# --------------------------------------------------------------------------
# Rate constants (must mirror docs/runbooks/cloudkitty-rate-card.md)
# --------------------------------------------------------------------------
def test_period_constants(rate: types.ModuleType) -> None:
    assert rate.PERIODS_PER_HOUR == Decimal(6)
    assert rate.PERIODS_PER_MONTH == Decimal(4380)  # 730 h/month * 6 periods/h


# --------------------------------------------------------------------------
# GPU alias parsing
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (None, (None, 0)),
        ("", (None, 0)),
        ("TeslaT10:2", ("TeslaT10", 2)),
        ("TeslaT10", ("TeslaT10", 1)),
        ("TeslaT10:notanint", ("TeslaT10", 1)),
        ("NVIDIA-A5000-24Q:1,other:9", ("NVIDIA-A5000-24Q", 1)),
    ],
)
def test_extract_gpu(rate: types.ModuleType, spec: str | None, expected: tuple) -> None:
    extra_specs = None if spec is None else {"pci_passthrough:alias": spec}
    assert rate._extract_gpu(extra_specs) == expected


# --------------------------------------------------------------------------
# Instance pricing
# --------------------------------------------------------------------------
def test_instance_per_period_compute_only(rate: types.ModuleType) -> None:
    # (0.006 * 2 vCPU + 0.002 * 4 GiB) / 6 periods per hour.
    price = rate._instance_per_period(_info(vcpus=2, ram_mb=4096))
    assert float(price) == pytest.approx((0.006 * 2 + 0.002 * 4) / 6)


def test_instance_per_period_adds_priced_gpu(rate: types.ModuleType) -> None:
    compute_only = rate._instance_per_period(_info(vcpus=2, ram_mb=4096))
    with_gpu = rate._instance_per_period(
        _info(vcpus=2, ram_mb=4096, gpu_alias="TeslaT10", gpu_count=1)
    )
    # TeslaT10 is 0.25/h -> 0.25/6 per period, added on top of compute.
    assert float(with_gpu - compute_only) == pytest.approx(0.25 / 6)


def test_instance_per_period_ignores_unpriced_gpu(rate: types.ModuleType) -> None:
    """A GPU alias not in GPU_RATE_HOUR is billed compute-only, not free-plus-
    error: the VM still pays for vCPU/RAM."""
    compute_only = rate._instance_per_period(_info(vcpus=2, ram_mb=4096))
    unpriced = rate._instance_per_period(
        _info(vcpus=2, ram_mb=4096, gpu_alias="MysteryGPU", gpu_count=4)
    )
    assert unpriced == compute_only


# --------------------------------------------------------------------------
# Storage pricing
# --------------------------------------------------------------------------
def test_storage_per_period_scales_with_size(rate: types.ModuleType) -> None:
    # 0.04 $/GiB-month * 100 GiB / 4380 periods-per-month.
    price = rate._storage_per_period(Decimal(100))
    assert float(price) == pytest.approx(0.04 * 100 / 4380)


def test_storage_per_period_zero_usage(rate: types.ModuleType) -> None:
    assert rate._storage_per_period(Decimal(0)) == Decimal(0)


# --------------------------------------------------------------------------
# _process entrypoint: rate lookup + edge cases
# --------------------------------------------------------------------------
def _process_isolated(rate: types.ModuleType, payload: dict) -> None:
    # Never touch Nova during _process tests; the cache is seeded by the caller.
    rate._ensure_cache_fresh = lambda: None
    rate._process(payload)


def test_process_prices_instance_from_cache(rate: types.ModuleType) -> None:
    rate._state()["cache"] = {"vm-1": _info(vcpus=2, ram_mb=4096)}
    item = {"vol": {"qty": Decimal(6)}, "groupby": {"uuid": "vm-1"}}
    payload = {"usage": {"instance": [item]}}

    _process_isolated(rate, payload)

    expected = rate._instance_per_period(_info(vcpus=2, ram_mb=4096)) * Decimal(6)
    assert item["rating"]["price"] == expected
    # Metadata is enriched so the report can show the rated flavor.
    assert item["metadata"]["flavor_name"] == "m1.test"
    assert item["metadata"]["vcpus"] == "2"


def test_process_zero_qty_instance_prices_zero(rate: types.ModuleType) -> None:
    """A MAP-mutated qty=0 (instance not ACTIVE) prices at 0 without a lookup."""
    rate._state()["cache"] = {"vm-1": _info(vcpus=99, ram_mb=99999)}
    item = {"vol": {"qty": Decimal(0)}, "groupby": {"uuid": "vm-1"}}

    _process_isolated(rate, {"usage": {"instance": [item]}})

    assert item["rating"]["price"] == Decimal(0)


def test_process_unknown_uuid_left_unpriced(rate: types.ModuleType) -> None:
    """An active instance with no flavor info (cache miss + failed refresh) is
    left at its incoming price rather than charged wrongly -- lab policy is
    do-not-charge. Real CloudKitty items arrive with rating pre-seeded to
    price=0 (see rate.py's documented DataPoint shape), and the unknown-uuid
    `continue` leaves that seeded value untouched."""
    rate._refresh_cache = lambda: False
    item = {
        "vol": {"qty": Decimal(6)},
        "groupby": {"uuid": "ghost"},
        "rating": {"price": Decimal(0)},
    }

    _process_isolated(rate, {"usage": {"instance": [item]}})

    # Not priced up: the seeded 0 stands, the instance is not charged.
    assert item["rating"]["price"] == Decimal(0)


def test_process_prices_storage(rate: types.ModuleType) -> None:
    item = {"vol": {"qty": Decimal(100)}}

    _process_isolated(rate, {"usage": {"storage": [item]}})

    assert item["rating"]["price"] == rate._storage_per_period(Decimal(100))


def test_process_ignores_unknown_usage_type(rate: types.ModuleType) -> None:
    item = {"vol": {"qty": Decimal(5)}, "groupby": {"uuid": "n-1"}}

    _process_isolated(rate, {"usage": {"network": [item]}})

    assert "price" not in item["rating"]
