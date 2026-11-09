"""Unit tests for the CloudKitty rating pyscript (scripts/rate.py).

rate.py is exec()'d by cloudkitty with an injected `data`, so its last line
(`_process(data)`) cannot run at import time. `_load_rate_module` strips that
entrypoint and execs the rest into an isolated module so the pure pricing
functions can be tested directly, without Nova or cloudkitty in the loop.
"""
from __future__ import annotations

import sys
import time
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
    # State lives on `sys` (shared across module loads); drop it so every test
    # starts from the defaults _state() rebuilds. Nova is always faked.
    vars(sys).pop(mod._CACHE_KEY, None)
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
def _frame(uuid: str, qty: int = 6) -> dict:
    """An instance usage item as CloudKitty hands it over: price pre-seeded 0."""
    return {
        "vol": {"qty": Decimal(qty)},
        "groupby": {"uuid": uuid},
        "rating": {"price": Decimal(0)},
    }


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
    rate._lookup_deleted = lambda uuid: None
    item = _frame("ghost")

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


# --------------------------------------------------------------------------
# Nova lookups: deleted VMs, cache merge, pagination, unpriceable instances
# --------------------------------------------------------------------------
class _FakeFlavor:
    def __init__(self, fid: str, name: str, vcpus: int, ram: int) -> None:
        self.id = fid
        self.name = name
        self.vcpus = vcpus
        self.ram = ram

    def get_keys(self) -> dict:
        return {}


class _FakeNova:
    """Stands in for both novaclient microversions rate.py builds. `live` is
    what a plain servers.list returns (2.1 shape: flavor carries only an id);
    `deleted` is what a deleted=True listing returns (2.47 shape: the flavor
    embedded in the instance record, with vcpus/ram/extra_specs)."""

    def __init__(self, live: list | None = None, deleted: list | None = None) -> None:
        self._live = live or []
        self._deleted = deleted or []
        self.microversions: list[str] = []
        self.list_calls: list[tuple[dict, object]] = []
        self.flavors = types.SimpleNamespace(
            list=lambda detailed, is_public: [_FakeFlavor("flv-1", "m1.test", 2, 4096)]
        )
        self.servers = types.SimpleNamespace(list=self._list_servers)

    def _list_servers(self, search_opts: dict | None = None, limit: object = None) -> list:
        opts = dict(search_opts or {})
        self.list_calls.append((opts, limit))
        if opts.get("deleted"):
            return [s for s in self._deleted if opts.get("uuid") in (None, s.id)]
        return list(self._live)


def _server(sid: str, flavor: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=sid, flavor=flavor)


def _use_fake_nova(rate: types.ModuleType, nova: _FakeNova) -> None:
    def factory(microversion: str = "2.1") -> _FakeNova:
        nova.microversions.append(microversion)
        return nova

    rate._nova_client = factory


_GPU_EMBEDDED_FLAVOR = {
    "original_name": "g1.t10",
    "vcpus": 4,
    "ram": 8192,
    "disk": 40,
    "ephemeral": 0,
    "swap": 0,
    "extra_specs": {"pci_passthrough:alias": "TeslaT10:1"},
}


def test_process_prices_vm_deleted_since_the_rated_period(rate: types.ModuleType) -> None:
    """Regression (2026-11 audit): when CloudKitty rates or reprocesses a past
    month, a VM deleted since then is absent from today's servers.list. It must
    still be priced from the flavor Nova recorded with the instance, not left
    at $0."""
    _use_fake_nova(rate, _FakeNova(deleted=[_server("vm-gone", _GPU_EMBEDDED_FLAVOR)]))
    item = _frame("vm-gone")

    _process_isolated(rate, {"usage": {"instance": [item]}})

    expected = rate._instance_per_period(
        _info(vcpus=4, ram_mb=8192, gpu_alias="TeslaT10", gpu_count=1)
    ) * Decimal(6)
    assert item["rating"]["price"] == expected
    assert item["metadata"]["flavor_name"] == "g1.t10"
    assert item["metadata"]["vcpus"] == "4"


def test_deleted_lookup_uses_microversion_with_embedded_flavor(
    rate: types.ModuleType,
) -> None:
    """Only microversion >= 2.47 embeds vcpus/ram/extra_specs in server.flavor;
    below that the flavor is an id that may itself be deleted."""
    nova = _FakeNova(deleted=[_server("vm-gone", _GPU_EMBEDDED_FLAVOR)])
    _use_fake_nova(rate, nova)

    info = rate._lookup_deleted("vm-gone")

    assert info is not None and info["vcpus"] == 4
    assert nova.microversions == ["2.47"]
    opts, _ = nova.list_calls[-1]
    assert opts == {"all_tenants": True, "deleted": True, "uuid": "vm-gone"}


def test_refresh_merges_into_cache_instead_of_replacing(rate: types.ModuleType) -> None:
    """A VM seen once stays priceable after it disappears from servers.list."""
    rate._state()["cache"] = {"vm-old": _info(vcpus=1, ram_mb=1024)}
    _use_fake_nova(rate, _FakeNova(live=[_server("vm-new", {"id": "flv-1"})]))

    assert rate._refresh_cache() is True

    assert set(rate._state()["cache"]) == {"vm-old", "vm-new"}


def test_refresh_lists_every_server_not_just_the_first_page(rate: types.ModuleType) -> None:
    """novaclient returns only Nova's osapi_max_limit (1000) servers unless
    limit=-1, which silently drops the rest from the cache."""
    nova = _FakeNova(live=[_server("vm-1", {"id": "flv-1"})])
    _use_fake_nova(rate, nova)

    rate._refresh_cache()

    opts, limit = nova.list_calls[0]
    assert opts == {"all_tenants": True}
    assert limit == -1


def test_unpriceable_instance_is_loud_and_not_retried_every_frame(
    rate: types.ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    nova = _FakeNova()
    _use_fake_nova(rate, nova)

    with caplog.at_level("WARNING", logger="cloudkitty.rate_pyscript"):
        _process_isolated(rate, {"usage": {"instance": [_frame("ghost")]}})
    calls_after_first = len(nova.list_calls)
    _process_isolated(rate, {"usage": {"instance": [_frame("ghost")]}})

    assert any(
        "ghost" in r.getMessage() and "UNPRICED" in r.getMessage() for r in caplog.records
    )
    # The miss is remembered for CACHE_TTL: a reprocess over thousands of
    # periods must not re-list every server once per period per ghost.
    assert len(nova.list_calls) == calls_after_first


def test_transient_deleted_lookup_failure_is_not_remembered_as_a_miss(
    rate: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a definitive "no such instance" is negative-cached. A Nova blip
    must not zero every frame of a deleted VM for the next CACHE_TTL."""
    monkeypatch.setattr(rate.time, "sleep", lambda _s: None)
    nova = _FakeNova(deleted=[_server("vm-gone", _GPU_EMBEDDED_FLAVOR)])
    healthy_list = nova.servers.list

    def broken_list(search_opts: dict | None = None, limit: object = None) -> list:
        raise ConnectionError("nova down")

    nova.servers.list = broken_list
    _use_fake_nova(rate, nova)
    first = _frame("vm-gone")
    _process_isolated(rate, {"usage": {"instance": [first]}})
    assert first["rating"]["price"] == Decimal(0)
    assert "vm-gone" not in rate._state()["misses"]
    # A failed lookup backs off briefly instead of retrying every frame...
    blocked = _frame("vm-gone")
    _process_isolated(rate, {"usage": {"instance": [blocked]}})
    assert blocked["rating"]["price"] == Decimal(0)

    # ...and once the back-off window has passed and Nova answers, the VM
    # is priced: the failure was never remembered as a miss.
    rate._state()["deleted_lookup_failed_at"] = 0.0
    nova.servers.list = healthy_list
    second = _frame("vm-gone")
    _process_isolated(rate, {"usage": {"instance": [second]}})
    assert second["rating"]["price"] > 0


def test_cache_miss_skips_refresh_when_one_was_just_attempted(
    rate: types.ModuleType,
) -> None:
    """Each deleted VM in a reprocess must not trigger a full fleet listing
    when a refresh was tried seconds ago; go straight to the deleted lookup."""
    rate._state()["refresh_attempted_at"] = time.time()
    nova = _FakeNova(deleted=[_server("vm-gone", _GPU_EMBEDDED_FLAVOR)])
    _use_fake_nova(rate, nova)

    assert rate._resolve_missing("vm-gone") is not None
    assert [opts.get("deleted") for opts, _ in nova.list_calls] == [True]


def test_failed_flavor_catalog_does_not_block_deleted_lookup(
    rate: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deleted listing carries its own embedded flavor, so a broken
    flavors.list (refresh fails) must not stop a deleted VM being priced."""
    monkeypatch.setattr(rate.time, "sleep", lambda _s: None)
    nova = _FakeNova(deleted=[_server("vm-gone", _GPU_EMBEDDED_FLAVOR)])

    def broken_flavors(detailed: bool, is_public: object) -> list:
        raise ConnectionError("flavor API down")

    nova.flavors.list = broken_flavors
    _use_fake_nova(rate, nova)
    item = _frame("vm-gone")

    _process_isolated(rate, {"usage": {"instance": [item]}})

    assert item["rating"]["price"] > 0


def test_extra_specs_failure_keeps_verified_gpu_entry(rate: types.ModuleType) -> None:
    """A transient get_keys() failure must not overwrite a cached GPU entry
    with a compute-only one for every server on that flavor."""
    verified = _info(vcpus=2, ram_mb=4096, gpu_alias="TeslaT10", gpu_count=1)
    rate._state()["cache"] = {"vm-gpu": verified}

    class _BrokenKeysFlavor(_FakeFlavor):
        def get_keys(self) -> dict:
            raise ConnectionError("extra specs unavailable")

    nova = _FakeNova(live=[_server("vm-gpu", {"id": "flv-1"}),
                           _server("vm-gpu2", {"id": "flv-1"})])
    nova.flavors.list = lambda detailed, is_public: [
        _BrokenKeysFlavor("flv-1", "g1.t10", 2, 4096)
    ]
    _use_fake_nova(rate, nova)

    assert rate._refresh_cache() is True

    assert rate._state()["cache"]["vm-gpu"] == verified
    assert "vm-gpu2" not in rate._state()["cache"]


def test_deleted_vm_with_unknown_gpu_alias_is_warned(
    rate: types.ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    flavor = {**_GPU_EMBEDDED_FLAVOR, "extra_specs": {"pci_passthrough:alias": "MysteryGPU:1"}}
    _use_fake_nova(rate, _FakeNova(deleted=[_server("vm-gone", flavor)]))

    with caplog.at_level("WARNING", logger="cloudkitty.rate_pyscript"):
        rate._lookup_deleted("vm-gone")

    assert any("MysteryGPU" in r.getMessage() for r in caplog.records)


def test_state_upgrades_cache_left_by_an_older_script_version(
    rate: types.ModuleType,
) -> None:
    """The state dict lives on `sys` and outlives a setup_pyscript.sh upload,
    so a worker may hold a dict created by the previous rate.py version."""
    setattr(sys, rate._CACHE_KEY, {"cache": {}, "refreshed_at": 0.0})

    assert rate._state()["misses"] == {}
