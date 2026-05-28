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
not one per VM. Persisted across cloudkitty pyscripts exec() invocations by
stashing the cache on the sys module (a fresh globals dict is given to
exec() each call, so module-level state in this file would otherwise be
lost). On Nova failure: retry with backoff (1s, 2s, 4s = ~7s total);
unknown UUIDs are then left at price=0 with a loud LOG.warning, which is
the lab policy (do not charge if we cannot verify).

Rate rationale, source citations, monthly cost examples:
  docs/runbooks/cloudkitty-rate-card.md

Deploy via tools/usage_reports/scripts/setup_pyscript.sh.
"""
import logging
import sys
import time
from decimal import Decimal

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

def _state():
    st = getattr(sys, _CACHE_KEY, None)
    if st is None:
        st = {'cache': {}, 'refreshed_at': 0.0}
        setattr(sys, _CACHE_KEY, st)
    return st

# ---------------------------------------------------------------------------
# Nova
# ---------------------------------------------------------------------------
def _nova_client():
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
    auth = ksa_loading.load_auth_from_conf_options(cfg.CONF, 'keystone_authtoken')
    cafile = cfg.CONF.keystone_authtoken.cafile or True
    sess = ksa_session.Session(auth=auth, verify=cafile, timeout=30)
    return novaclient.Client('2.1', session=sess, endpoint_type='internalURL')


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


def _refresh_cache():
    """Block on Nova; retry with bounded backoff before giving up. Returns
    True on success, False if every attempt failed (caller leaves stale
    cache in place; unknown UUIDs will rate at $0)."""
    delays = (0, 1, 2, 4)
    last_exc = None
    # Build the Nova client once per refresh; the keystoneauth Session
    # caches its token so each retry reuses it instead of re-fetching.
    try:
        nova = _nova_client()
    except Exception as e:
        LOG.error('rate.py: cannot construct Nova client (config error?): %s', e)
        return False
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            flavors = {f.id: f for f in nova.flavors.list(detailed=True,
                                                          is_public=None)}
            servers = nova.servers.list(search_opts={'all_tenants': True})
            new_cache = {}
            unpriced_aliases = set()
            for s in servers:
                fid = (getattr(s, 'flavor', None) or {}).get('id')
                flv = flavors.get(fid)
                if not flv:
                    continue
                try:
                    extras = flv.get_keys()
                except Exception:
                    extras = {}
                gpu_alias, gpu_count = _extract_gpu(extras)
                if gpu_alias and gpu_alias not in GPU_RATE_HOUR:
                    unpriced_aliases.add(gpu_alias)
                new_cache[s.id] = {
                    'vcpus': int(flv.vcpus),
                    'ram_mb': int(flv.ram),
                    'gpu_alias': gpu_alias,
                    'gpu_count': gpu_count,
                    'flavor_id': fid,
                    'flavor_name': flv.name,
                }
            st = _state()
            st['cache'] = new_cache
            st['refreshed_at'] = time.time()
            LOG.info('rate.py: cached %d instance->flavor mappings',
                     len(new_cache))
            if unpriced_aliases:
                LOG.warning('rate.py: GPU alias(es) seen on flavors but not '
                            'in GPU_RATE_HOUR -- those VMs are billed '
                            'compute-only: %s',
                            sorted(unpriced_aliases))
            return True
        except Exception as e:
            last_exc = e
            LOG.warning('rate.py: Nova refresh attempt %d/%d failed: %s',
                        attempt + 1, len(delays), e)
    LOG.error('rate.py: Nova unreachable after %d attempts; instances with '
              'unknown UUIDs will be priced at 0 (last error: %s)',
              len(delays), last_exc)
    return False


def _ensure_cache_fresh():
    st = _state()
    if time.time() - st['refreshed_at'] > CACHE_TTL:
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
                uuid = item.get('groupby', {}).get('uuid')
                if not uuid:
                    continue
                info = st['cache'].get(uuid)
                if info is None:
                    # VM created since last refresh? Try one resync.
                    if _refresh_cache():
                        info = st['cache'].get(uuid)
                if info is None:
                    LOG.warning('rate.py: no flavor info for instance %s; '
                                'leaving price=0', uuid)
                    continue
                # Enrich metadata so the report shows what flavor was rated.
                meta = item.setdefault('metadata', {})
                meta['flavor_id'] = info['flavor_id']
                meta['flavor_name'] = info['flavor_name']
                meta['vcpus'] = str(info['vcpus'])
                meta['memory_mb'] = str(info['ram_mb'])
                # qty for openstack_nova_server_status is the MAP-mutated
                # 0/1 active indicator over the period; multiply so an
                # inactive frame stays at 0.
                rating['price'] = _instance_per_period(info) * qty
            elif usage_type == 'storage':
                rating['price'] = _storage_per_period(qty)


_process(data)  # noqa: F821 -- `data` is injected by cloudkitty pyscripts
