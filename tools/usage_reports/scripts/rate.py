"""CloudKitty pyscript: rate instances by Nova flavor and storage by GB-month.

Why this exists
---------------
Our Prometheus collector pulls openstack_nova_server_status (from
prometheus-openstack-exporter), but that series does NOT carry a flavor_id
label in the version we deploy -- it exposes id, hostname, tenant_id,
user_id, host_id, status, availability_zone, address_* only. CloudKitty's
prometheus collector renders the missing label as the literal string
"<nil>" on the dataframe metadata, which means a hashmap mapping on
flavor_id can never match. We tried hashmap first; every dataframe rated
at $0. This pyscript bridges the gap by joining Nova-side flavor metadata
onto each frame.

How it works
------------
On entry, refresh a (instance_uuid -> flavor_info) cache from Nova if older
than CACHE_TTL. Two API calls per refresh (servers.list + flavors.list),
not one per VM. Refreshes MERGE into the cache, so a VM seen once stays
priceable after it is deleted. Persisted across cloudkitty pyscripts exec()
invocations by stashing the cache on the sys module (a fresh globals dict is
given to exec() each call, so module-level state in this file would
otherwise be lost). On Nova failure: retry with backoff (1s, 2s, 4s = ~7s
total).

CloudKitty may rate a period long after it ended (wait_periods, a stall
backlog, a /v2/task/reprocesses run). A VM deleted since then is absent from
servers.list, so a cache miss falls back to Nova's deleted-instance listing
(admin-only, microversion 2.47+), whose server.flavor embeds the vcpus/ram/
extra_specs recorded with the instance -- correct even if the flavor itself
was deleted. That works until an operator runs `nova-manage db
archive_deleted_rows`. An instance still unresolved is left at price=0
(lab policy: do not charge if we cannot verify) with an "UNPRICED" warning,
and the usage report counts it too (active hours, $0 cost).

Rate rationale, source citations, monthly cost examples:
  docs/runbooks/cloudkitty-rate-card.md

Deploy via tools/usage_reports/scripts/setup_pyscript.sh.
"""
import logging
import sys
import time
from decimal import Decimal
from typing import Any

# Hardcoded logger name: `__name__` is unreliable in code run via exec().
LOG = logging.getLogger('cloudkitty.rate_pyscript')

# ---------------------------------------------------------------------------
# Rate constants (mirror docs/runbooks/cloudkitty-rate-card.md).
# Showback, not billing. Anchored at ~65% of budget cloud tier (DO/Vultr).
# ---------------------------------------------------------------------------
MULTIPLIER = Decimal('1.0')
VCPU_RATE_HOUR = Decimal('0.006')
RAM_RATE_GB_HOUR = Decimal('0.002')
STORAGE_RATE_GB_MONTH = Decimal('0.04')
GPU_RATE_HOUR = {
    'TeslaT10': Decimal('0.25'),
    'NVIDIA-A5000-24Q': Decimal('0.25'),
    'NVIDIA-A5000-12Q': Decimal('0.125'),
    'Intel-Arc-Pro-B50-VF': Decimal('0.15'),
}
PERIOD_SECONDS = 600
PERIODS_PER_HOUR = Decimal(3600) / Decimal(PERIOD_SECONDS)
PERIODS_PER_MONTH = Decimal(730 * 3600) / Decimal(PERIOD_SECONDS)

# ---------------------------------------------------------------------------
# Persistent cache. cloudkitty pyscripts exec()s this file with a fresh
# globals dict every invocation, so module-level state in this file would
# be lost between frames. sys is shared across all exec() calls in the
# same processor worker, so we stash state there.
# ---------------------------------------------------------------------------
_CACHE_KEY = '_cloudkitty_rate_state'
CACHE_TTL = 600  # seconds; one collection period
# A cache miss forces a refresh only if the last one is older than this. A VM
# active in a period being rated existed at least wait_periods (20 min) ago,
# so a fresher refresh has already seen it if it is still live.
MIN_REFRESH_INTERVAL = 60
RETRY_DELAYS = (0, 1, 2, 4)  # seconds slept before each Nova attempt
# Nova microversion whose server.flavor embeds vcpus/ram/extra_specs instead
# of only a flavor id (which may point at a since-deleted flavor).
EMBEDDED_FLAVOR_MICROVERSION = '2.47'

def _state() -> dict:
    st = getattr(sys, _CACHE_KEY, None)
    if st is None:
        st = {'cache': {}, 'refreshed_at': 0.0}
        setattr(sys, _CACHE_KEY, st)
    # The dict outlives a script upload, so one made by an older rate.py may
    # lack keys added since.
    st.setdefault('misses', {})   # uuid -> time Nova last said "no such VM"
    st.setdefault('clients', {})  # microversion -> (Client, built_at)
    # Per-path throttles (stamped when an attempt ends), so a failing call is
    # not retried for every missing VM in every frame.
    st.setdefault('refresh_attempted_at', 0.0)
    st.setdefault('deleted_lookup_failed_at', 0.0)
    return st

# ---------------------------------------------------------------------------
# Nova
# ---------------------------------------------------------------------------
def _nova_client(microversion: str = '2.1') -> Any:
    """Nova client for `microversion`, reused for CACHE_TTL to avoid a
    Keystone token request per lookup. Rebuilt after that, so a re-uploaded
    rate.py with different client settings takes effect without a
    processor restart (the cache on `sys` outlives the upload)."""
    clients = _state()['clients']
    client, built_at = clients.get(microversion, (None, 0.0))
    if client is None or time.time() - built_at > CACHE_TTL:
        client = _build_nova_client(microversion)
        clients[microversion] = (client, time.time())
    return client


def _build_nova_client(microversion: str) -> Any:
    # Reuse the cloudkitty service-user credentials already in cloudkitty.conf
    # (no new secrets to provision). kolla creates the cloudkitty user with
    # admin role on the service project, sufficient for all_tenants=True.
    #
    # Use keystoneauth1.loading rather than reading individual options off
    # cfg.CONF.keystone_authtoken: in the cloudkitty-processor (no WSGI
    # middleware) the password-plugin opts (username, password, auth_url,
    # ...) are not pre-registered on that group, so direct attribute
    # access raises NoSuchOptError. load_from_conf_options registers them
    # on demand from auth_type=password in the conf file.
    #
    # A 30s session-level timeout caps any hung Keystone or Nova call; the
    # retry loop in _refresh_cache then accepts ~3 such failures (~7s of
    # backoff sleep on top of timeouts) before giving up.
    from oslo_config import cfg
    from keystoneauth1 import loading as ksa_loading
    from keystoneauth1 import session as ksa_session
    from novaclient import client as novaclient
    # In the processor (no WSGI middleware), keystone_authtoken hasn't had
    # its auth/session opts registered, so load_auth_from_conf_options would
    # raise NoSuchOptError. Register both, then load. Idempotent across
    # repeated exec()s.
    ksa_loading.register_auth_conf_options(cfg.CONF, 'keystone_authtoken')
    ksa_loading.register_session_conf_options(cfg.CONF, 'keystone_authtoken')
    auth = ksa_loading.load_auth_from_conf_options(cfg.CONF, 'keystone_authtoken')
    cafile = cfg.CONF.keystone_authtoken.cafile or True
    sess = ksa_session.Session(auth=auth, verify=cafile, timeout=30)
    return novaclient.Client(microversion, session=sess, endpoint_type='internalURL')


def _extract_gpu(extra_specs):
    """Return (alias, count) parsed from a flavor's pci_passthrough:alias
    extra_spec. Returns (None, 0) if no GPU is requested."""
    spec = (extra_specs or {}).get('pci_passthrough:alias', '')
    if not spec:
        return (None, 0)
    first = spec.split(',')[0].strip()
    if ':' in first:
        name, _, count_s = first.partition(':')
        try:
            return (name.strip(), int(count_s.strip()))
        except ValueError:
            return (name.strip(), 1)
    return (first, 1)


def _flavor_info(vcpus: int | str, ram_mb: int | str,
                 extra_specs: dict | None, flavor_id: str,
                 flavor_name: str) -> dict:
    """Build one cache entry from a flavor's sizing and extra_specs."""
    gpu_alias, gpu_count = _extract_gpu(extra_specs)
    return {
        'vcpus': int(vcpus),
        'ram_mb': int(ram_mb),
        'gpu_alias': gpu_alias,
        'gpu_count': gpu_count,
        'flavor_id': flavor_id,
        'flavor_name': flavor_name,
    }


def _with_retries(call: Any, what: str) -> Any:
    """Run `call()` with the RETRY_DELAYS backoff; raise its last error if
    every attempt fails."""
    last_exc = None
    for attempt, delay in enumerate(RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return call()
        except Exception as e:
            last_exc = e
            LOG.warning('rate.py: %s attempt %d/%d failed: %s',
                        what, attempt + 1, len(RETRY_DELAYS), e)
    raise last_exc


def _list_live_flavors(nova: Any) -> dict:
    """uuid -> flavor info for every live server, via a flavors.list join."""
    flavors = {f.id: f for f in nova.flavors.list(detailed=True,
                                                  is_public=None)}
    # limit=-1: without it novaclient returns only Nova's first page
    # (osapi_max_limit, default 1000 servers).
    servers = nova.servers.list(search_opts={'all_tenants': True}, limit=-1)
    by_flavor = {}  # one get_keys() call per flavor, not per server
    out = {}
    for s in servers:
        fid = (getattr(s, 'flavor', None) or {}).get('id')
        if fid not in by_flavor:
            flv = flavors.get(fid)
            by_flavor[fid] = None
            if flv is not None:
                try:
                    by_flavor[fid] = _flavor_info(flv.vcpus, flv.ram,
                                                  flv.get_keys(), fid,
                                                  flv.name)
                except Exception as e:
                    # Without extra_specs a GPU flavor would be priced
                    # compute-only. Leave its servers out of this refresh
                    # so verified cache entries stay; uncached ones go
                    # UNPRICED (loud) until a later refresh succeeds.
                    LOG.warning('rate.py: extra_specs for flavor %s '
                                'unavailable, skipping its servers this '
                                'refresh: %s', fid, e)
        if by_flavor[fid] is not None:
            out[s.id] = by_flavor[fid]
    return out


def _warn_unpriced_gpus(infos: Any) -> None:
    aliases = {i['gpu_alias'] for i in infos
               if i['gpu_alias'] and i['gpu_alias'] not in GPU_RATE_HOUR}
    if aliases:
        LOG.warning('rate.py: GPU alias(es) seen on flavors but not in '
                    'GPU_RATE_HOUR -- those VMs are billed compute-only: %s',
                    sorted(aliases))


def _refresh_cache() -> bool:
    """Block on Nova; retry with bounded backoff before giving up. Returns
    True on success, False if every attempt failed (caller leaves stale
    cache in place). Merges into the cache rather than replacing it, so an
    entry survives its VM's deletion. Returns False without calling Nova if
    the previous attempt ended under MIN_REFRESH_INTERVAL ago: a fresh
    refresh already saw every live VM, and a failing one (up to ~2 min of
    timeouts) must not be repeated for every missing VM in every frame."""
    st = _state()
    if time.time() - st['refresh_attempted_at'] < MIN_REFRESH_INTERVAL:
        return False
    try:
        nova = _nova_client()
        new_cache = _with_retries(lambda: _list_live_flavors(nova),
                                  'Nova refresh')
    except Exception as e:
        LOG.error('rate.py: Nova refresh failed after %d attempts; instances '
                  'with unknown UUIDs will be priced at 0 (last error: %s)',
                  len(RETRY_DELAYS), e)
        return False
    finally:
        st['refresh_attempted_at'] = time.time()
    st['cache'].update(new_cache)
    now = time.time()
    st['refreshed_at'] = now
    st['misses'] = {u: t for u, t in st['misses'].items()
                    if now - t < CACHE_TTL}
    LOG.info('rate.py: refreshed %d live instance->flavor mappings '
             '(%d cached in total)', len(new_cache), len(st['cache']))
    _warn_unpriced_gpus(new_cache.values())
    return True


def _lookup_deleted(uuid: str) -> dict | None:
    """Flavor info for a deleted instance, from the flavor Nova embedded in
    the instance record (microversion >= 2.47). None if Nova answered but no
    longer has the row (archived/purged). Raises if every attempt failed, so
    the caller can tell "gone" from "Nova unreachable"."""
    def call() -> list:
        nova = _nova_client(EMBEDDED_FLAVOR_MICROVERSION)
        return nova.servers.list(search_opts={
            'all_tenants': True, 'deleted': True, 'uuid': uuid})

    servers = _with_retries(call, 'deleted-instance lookup for %s' % uuid)
    for s in servers:
        flv = getattr(s, 'flavor', None) or {}
        if s.id != uuid or 'vcpus' not in flv or 'ram' not in flv:
            continue
        # 2.47+ carries no flavor id; the name is what the report shows.
        info = _flavor_info(flv['vcpus'], flv['ram'], flv.get('extra_specs'),
                            '', flv.get('original_name') or '')
        _warn_unpriced_gpus([info])
        return info
    return None


def _resolve_missing(uuid: str) -> dict | None:
    """Cache miss: a VM created since the last refresh, or one deleted since
    the period being rated. A definitive "Nova has no such instance" is
    remembered for CACHE_TTL so a reprocess over thousands of periods does
    not hammer Nova per frame; a lookup that errored is not, so one Nova blip
    cannot zero a VM's frames for the next ten minutes."""
    st = _state()
    now = time.time()
    if now - st['misses'].get(uuid, 0.0) < CACHE_TTL:
        return None
    if _refresh_cache():
        info = st['cache'].get(uuid)
        if info is not None:
            return info
    # Tried even if the refresh failed or was throttled: the deleted listing
    # needs no flavor catalog, so it can succeed when flavors.list does not.
    # After it fails every retry, back off for MIN_REFRESH_INTERVAL instead
    # of paying the retries again for every missing VM.
    if now - st['deleted_lookup_failed_at'] < MIN_REFRESH_INTERVAL:
        return None
    try:
        info = _lookup_deleted(uuid)
    except Exception:
        # Each attempt was logged. Not remembered as a miss: the VM may
        # well be priceable once Nova answers.
        st['deleted_lookup_failed_at'] = time.time()
        return None
    if info is None:
        st['misses'][uuid] = time.time()
        return None
    st['cache'][uuid] = info
    return info


def _ensure_cache_fresh() -> None:
    if time.time() - _state()['refreshed_at'] > CACHE_TTL:
        _refresh_cache()


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
def _instance_per_period(info):
    """Per-period cost for one active instance of the cached flavor."""
    compute_per_hour = (
        VCPU_RATE_HOUR * Decimal(info['vcpus'])
        + RAM_RATE_GB_HOUR * (Decimal(info['ram_mb']) / Decimal(1024))
    )
    price = compute_per_hour / PERIODS_PER_HOUR
    alias = info['gpu_alias']
    if alias and alias in GPU_RATE_HOUR:
        gpu_per_hour = GPU_RATE_HOUR[alias] * Decimal(info['gpu_count'])
        price += gpu_per_hour / PERIODS_PER_HOUR
    return price * MULTIPLIER


def _storage_per_period(qty_gib):
    """Per-period cost for `qty_gib` GiB of provisioned project storage."""
    return STORAGE_RATE_GB_MONTH * qty_gib * MULTIPLIER / PERIODS_PER_MONTH


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# cloudkitty's pyscripts module calls `data.as_dict(mutable=True)` and exec()s
# this script with `data` bound to that dict, then rebuilds the DataFrame from
# the mutated dict afterwards (cloudkitty/rating/pyscripts/__init__.py:114).
# Per cloudkitty.dataframe.DataPoint.as_dict, each usage item has the shape:
#   {
#     'vol': {'unit': str, 'qty': Decimal},
#     'rating': {'price': Decimal},
#     'groupby': {...},
#     'metadata': {...},
#   }
# With mutable=True, every dict is a plain dict and safe to mutate in place.
def _process(payload):
    _ensure_cache_fresh()
    st = _state()
    for usage_type, items in payload.get('usage', {}).items():
        for item in items:
            qty = item.get('vol', {}).get('qty') or Decimal(0)
            rating = item.setdefault('rating', {})
            if usage_type == 'instance':
                if qty == 0:
                    # Inactive instance: MAP-mutated qty=0 means status was
                    # not ACTIVE during the period (shelved, error, etc.).
                    # Price is 0 regardless of flavor; skip the Nova lookup
                    # entirely. Avoids spammy "no flavor info" warnings for
                    # shelved/deleted VMs that aren't in nova.servers.list.
                    rating['price'] = Decimal(0)
                    continue
                uuid = item.get('groupby', {}).get('uuid')
                if not uuid:
                    continue
                info = st['cache'].get(uuid)
                if info is None:
                    info = _resolve_missing(uuid)
                if info is None:
                    # Active VM with no flavor info IS worth warning about
                    # -- means we're not pricing real compute usage. The
                    # usage report also counts these (active hours, $0).
                    LOG.warning('rate.py: UNPRICED active instance %s: no '
                                'flavor info from Nova (live or deleted); '
                                'leaving price=0', uuid)
                    continue
                # Enrich metadata so the report shows what flavor was rated.
                meta = item.setdefault('metadata', {})
                meta['flavor_id'] = info['flavor_id']
                meta['flavor_name'] = info['flavor_name']
                meta['vcpus'] = str(info['vcpus'])
                meta['memory_mb'] = str(info['ram_mb'])
                rating['price'] = _instance_per_period(info) * qty
            elif usage_type == 'storage':
                rating['price'] = _storage_per_period(qty)


_process(data)  # noqa: F821 -- `data` is injected by cloudkitty pyscripts
