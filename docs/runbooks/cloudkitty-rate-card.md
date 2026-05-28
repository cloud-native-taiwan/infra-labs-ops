# CloudKitty rate card

This runbook documents how the CNTUG Infra Labs CloudKitty rate card is
structured, how to change rates, and how to re-rate historical usage after
a rate change. It is the operator companion to
`tools/usage_reports/scripts/rate.py` (the rating logic) and
`tools/usage_reports/scripts/setup_pyscript.sh` (the bootstrap).

## Architecture

- **Collector:** Prometheus. `openstack_nova_server_status` and
  `openstack_cinder_limits_volume_used_gb` feed CloudKitty.
- **Storage:** OpenSearch (CloudKitty storage v2). kolla-ansible 2026.1
  dropped influxdb deployment; opensearch is the v2 backend we use, which
  is required for the `/v2/task/reprocesses` API the reprocessing section
  below depends on (sqlalchemy is v1-only and does not expose it).
- **Rating module:** `pyscripts`, running `rate.py`. The script looks each
  instance's flavor up via the Nova API (cached 10 min, retried with
  backoff on transient failure), then computes a per-period price from the
  vCPU/RAM/GPU rates below and writes it back to the dataframe. Storage is
  priced inline from the per-project GiB metric, no Nova lookup needed.

  *Why not `hashmap`?* Hashmap can only match on labels that the collector
  actually pulls from Prometheus. The version of
  `prometheus-openstack-exporter` deployed here does NOT emit `flavor_id`
  on `openstack_nova_server_status` (labels are `id, hostname, tenant_id,
  user_id, host_id, status, availability_zone, address_*` only). Without
  flavor labels in the collected dataframe, every period rates to $0. The
  pyscript bridges the gap by joining Nova-side flavor metadata onto each
  frame and prices inline.
- **Collection period:** 600 s. All rates below are expressed per hour or
  per month; the script derives the per-period figure at runtime.

## Pricing model

This is **showback, not billing**. The goal is awareness ("this free
infrastructure has real value") and a nudge to clean up idle VMs. Rates are
provider-neutral (R10) and anchored at **~65% of the budget cloud tier**
(DigitalOcean / Vultr) rather than hyperscalers: Infra Labs offers **no
uptime SLA** and no storage QoS, so the honest peers are budget VPS and
LowEndBox providers, not AWS/GCP. This places a 2 vCPU / 4 GB VM at ~$15/mo,
between the budget VPS rate (~$20-24/mo, but those publish a 99.99% SLA) and
the no-SLA LowEndBox floor (~$5/mo). A single `MULTIPLIER` constant at the
top of `rate.py` scales the whole card; lower it (e.g. `0.5`) for an even
gentler nudge.

`rate.py` does **not** hand-enumerate flavors. It reads the live flavor
list from Nova on each cache refresh and derives each flavor's per-period
cost from its vCPU and RAM:

```
hourly_cost = VCPU_RATE_HOUR * vcpus + RAM_RATE_GB_HOUR * ram_gib
per_period  = hourly_cost * MULTIPLIER / 6          (6 periods per hour)
```

### Current rates

| Resource | Rate | Anchor (~65% of budget tier) |
|----------|------|------------------------------|
| vCPU | $0.006 / vCPU-hour | budget VPS (DO/Vultr) shared-CPU, decomposed and discounted for no SLA |
| RAM | $0.002 / GB-hour | set low: RAM is plentiful here, vCPU/instance count is the scarce resource |
| Storage | $0.04 / GiB-month | ~65% of blended budget block storage (DO/Vultr ~$0.05-0.10): replicated Ceph RBD, no SLA, no QoS/IOPS guarantee |
| GPU `TeslaT10` | +$0.25 / hour | T4/T10-class community/managed inference ($0.20-0.30) |
| GPU `NVIDIA-A5000-24Q` (full 24 GB) | +$0.25 / hour | RunPod/Vast community A5000 on-demand ($0.16-0.27); not on hyperscalers |
| GPU `NVIDIA-A5000-12Q` (half 12 GB) | +$0.125 / hour | half of the full slice |
| GPU `Intel-Arc-Pro-B50-VF` | +$0.15 / hour | no cloud reference; T-class by INT8 TOPS (~170), MSRP $349 |

Sample monthly costs (24/7): 2 vCPU / 4 GB → `(0.006*2 + 0.002*4) = $0.020/hr`
(~$15/mo, ~65% of DO/Vultr ~$22); 4 vCPU / 8 GB → ~$29/mo; 8 vCPU / 16 GB →
~$58/mo. GPU rates are **adders** on top of the flavor's compute cost and are
*not* discounted to 65% -- they already sit at the community-marketplace
(RunPod/Vast) level, which is itself the no-SLA floor for these cards, and
GPUs are the scarce resource worth keeping salient. Sources are listed at the
bottom of this runbook.

Storage is a flat blended GiB rate because the collected metric
(`openstack_cinder_limits_volume_used_gb`) is a per-project aggregate with
no volume-type breakdown, and the Ceph backend provides no per-volume QoS.
Network is intentionally not metered (R6); sustained upstream is ~2 Gb/s, so
egress-heavy workloads are discouraged but not billed.

## GPU detection

GPU flavors are detected automatically from their `pci_passthrough:alias`
property (see `kolla/config/nova/*/nova.conf` for the `TeslaT10`,
`NVIDIA-A5000-24Q`, `NVIDIA-A5000-12Q`, and `Intel-Arc-Pro-B50-VF`
aliases). To price a new GPU type, add it to the `GPU_RATE_HOUR` dict
near the top of `rate.py` and re-run `setup_pyscript.sh` to push the
updated script. A flavor whose alias is missing from `GPU_RATE_HOUR` is
billed compute-only with a `LOG.warning` -- check
`docker logs cloudkitty_processor` on the control nodes after a deploy.

## OpenStack catalog: `volumev3` alias for cinder

`prometheus-openstack-exporter` (the version kolla 2026.1 ships) discovers
the Cinder API by looking up service type `volumev3` in the keystone
catalog. Modern kolla-ansible registers Cinder only as `block-storage`
(the official service-types-authority name), so the exporter logs

```
service=volume error="No suitable endpoint could be found in the service catalog."
```

and emits **zero `openstack_cinder_*` metrics**, which means CloudKitty's
prometheus collector never produces `storage` dataframes and the storage
rate stays at $0 regardless of what the pyscript wants to do.

Fix: register `cinderv3` (type `volumev3`) as an alias pointing at the
same cinder endpoints. This is the OpenStack-documented migration path
between old (`volume` / `volumev3`) and new (`block-storage`) service
type names; both clients are happy.

```bash
# One-time, admin-scoped:
openstack service create --name cinderv3 \
  --description "OpenStack Block Storage v3 (alias for openstack-exporter)" \
  volumev3

# Mirror the existing cinder endpoints under the new service:
for iface in public internal admin; do
  url=$(openstack endpoint list --service cinder --interface "$iface" \
    -f value -c URL | head -1)
  [[ -z "$url" ]] && continue
  openstack endpoint create --region RegionOne volumev3 "$iface" "$url"
done

# Restart the exporter so its in-memory catalog cache is rebuilt:
for h in 192.168.0.21 192.168.0.22; do
  ssh debian@$h sudo docker restart prometheus_openstack_exporter
done
```

Within ~10 minutes (one exporter scrape interval) Prometheus picks up
`openstack_cinder_limits_volume_used_gb`. Within ~20 minutes CloudKitty
processes a period that includes the new metric and the pyscript prices
storage. Verify with:

```
openstack rating summary get -b <begin> -e <end> --groupby type
```

A row with `type=storage` and non-zero `rate` should appear once the
pipeline catches up.

## Failure handling

`rate.py` makes Nova API calls to map instance UUIDs to flavors. The
behavior on Nova trouble:

- **Cache hit:** no API call; price is computed from the in-memory cache.
- **Cache miss (new VM since last refresh):** synchronous refresh
  attempt with up to 4 total attempts (initial + 3 retries with 1s, 2s,
  4s backoff, ~7s of sleep plus per-call timeouts of 30s). If every
  attempt fails, the affected instance is priced at $0 and a
  `LOG.warning` with the UUID is emitted to `cloudkitty_processor` logs.
- **Cache-wide refresh failure (cache stale + Nova unreachable):**
  cached entries continue to serve until refresh succeeds; UUIDs not in
  cache rate at $0 (lab policy: do not charge if we cannot verify).
- **Cache TTL:** 600 s. A VM's flavor never changes, so the TTL is
  about catching newly-created VMs, not invalidating stale data.

## Deployment sequence (end to end)

The CloudKitty + reporting deployment is staged because the pyscript
needs to be in place before the first processor cycle, and the report
tool depends on rated data already existing in CloudKitty's OpenSearch
storage.

1. `kolla-ansible -i multinode prechecks --tags cloudkitty,opensearch`
2. `kolla-ansible -i multinode reconfigure --tags cloudkitty,opensearch`
3. Confirm API health: `openstack rating module list` returns the
   `hashmap`, `noop`, and `pyscripts` modules.
4. Run `tools/usage_reports/scripts/setup_pyscript.sh` to enable
   pyscripts, upload `rate.py`, and tear down any leftover hashmap state.
5. Wait one or two collection periods (10 to 20 minutes). Validate with
   `openstack rating summary get -b ... -e ...`; non-zero `rate` rows
   should appear for active projects.
6. Provision the report tool's secrets first (gitignored, not in the
   repo): place `.env` and `clouds.yaml` under
   `ansible/private/tools/usage_reports/`. Then deploy from
   `ansible/`: `ansible-playbook playbooks/deploy-usage-reports.yml`.
7. Smoke-test inside the container:
   `docker exec usage-reports usage-reports generate --dry-run --month <YYYY-MM>`.

## First-time setup

> **Important:** Activate the pyscript *before* you start expecting useful
> data. Periods collected with no rating module enabled (or with a script
> that fails to set a price) are stored as zero-rated rows and only
> a reprocess can fix them retroactively (see below).

1. Deploy CloudKitty:
   ```
   kolla-ansible -i multinode prechecks --tags cloudkitty,opensearch
   kolla-ansible -i multinode reconfigure --tags cloudkitty,opensearch
   ```
2. Verify the rating module list shows `pyscripts` is available:
   ```
   openstack rating module list
   ```
   It will show `enabled=False` until step 4.
3. Review the rate constants (`VCPU_RATE_HOUR`, `RAM_RATE_GB_HOUR`,
   `STORAGE_RATE_GB_MONTH`, `GPU_RATE_HOUR`, `MULTIPLIER`) at the top of
   `tools/usage_reports/scripts/rate.py`. The defaults match the rate
   table above.
4. Apply from an admin-scoped shell with `python-cloudkittyclient` and
   `jq` available:
   ```
   ./tools/usage_reports/scripts/setup_pyscript.sh
   ```
   The script enables pyscripts, uploads `rate.py`, sets priority above
   hashmap, and removes any leftover hashmap services / fields / groups /
   mappings.
5. Wait one or two collection periods (10 to 20 minutes), then sanity
   check:
   ```
   openstack rating summary get -b 2026-05-01T00:00:00 -e 2026-05-02T00:00:00
   ```
   Confirm non-zero `rate` for projects with active VMs.

## Changing rates

1. Edit the rate constants at the top of
   `tools/usage_reports/scripts/rate.py`.
2. Re-run the bootstrap to push the new version:
   ```
   ./tools/usage_reports/scripts/setup_pyscript.sh
   ```
   The script is idempotent -- pyscripts upsert is by name, so the
   existing stored script is updated in place.
3. Re-rate historical periods (next section) if the change should apply
   to data already in storage.

## Reprocessing historical data

After a rate change, past periods still hold the old prices. Re-rating
is driven by CloudKitty's v2 reprocessing API (`/v2/task/reprocesses`);
there is no stable `openstack rating reprocess` CLI subcommand, so call
the API directly. Timestamps are UTC.

1. Get a token and the rating endpoint:
   ```bash
   TOKEN=$(openstack token issue -f value -c id)
   RATING_URL=$(openstack endpoint list --service rating --interface internal \
     -f value -c URL)
   ```
2. List scopes (and their processing state) to find the scope IDs to
   re-rate:
   ```bash
   curl -sH "X-Auth-Token: $TOKEN" "$RATING_URL/v2/scope" | jq .
   ```
3. Schedule a reprocess for the affected month(s). `scope_ids` is a
   JSON array of scope IDs from step 2:
   ```bash
   curl -sX POST "$RATING_URL/v2/task/reprocesses" \
     -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
     -d '{
       "reason": "Rate card update 2026-05-28",
       "scope_ids": ["<scope_id>", "..."],
       "start_reprocess_time": "2026-05-01T00:00:00+00:00",
       "end_reprocess_time": "2026-06-01T00:00:00+00:00"
     }'
   ```
4. Monitor progress (watch `current_reprocess_time` advance toward the
   end time):
   ```bash
   curl -sH "X-Auth-Token: $TOKEN" "$RATING_URL/v2/task/reprocesses" | jq .
   ```
5. Once reprocessing has passed the month end, regenerate the affected
   month's reports with
   `usage-reports generate --month 2026-05 --force` (the `--force`
   flag bypasses the delivery idempotency manifest).

## Audit checks

After deploying or changing rates, confirm a rated dataframe carries the
expected metadata and a non-zero price. Inspect a recent dataframe via
the OpenSearch storage backend (replace the VIP / time as needed):

```bash
curl -sS -H "Content-Type: application/json" \
  "http://192.168.113.252:9200/cloudkitty/_search?pretty" \
  -d '{
    "size": 3,
    "query": {"range": {"end": {"gte": "2026-05-28T15:00:00"}}},
    "_source": ["start","end","type","qty","price","groupby","metadata"]
  }'
```

A correctly-rated `instance` document looks like:

```json
{
  "start": "2026-05-28T15:00:00+00:00",
  "end":   "2026-05-28T15:10:00+00:00",
  "type":  "instance",
  "qty":   1.0,
  "price": 0.0033333333,
  "groupby": {"uuid": "...", "tenant_id": "..."},
  "metadata": {"flavor_id": "d1.medium", "flavor_name": "d1.medium",
               "vcpus": "2", "memory_mb": "4096"}
}
```

If `metadata.flavor_id` is `"<nil>"` or empty and `price` is `0`, the
pyscript is not running -- check
`docker logs cloudkitty_processor` on a control node for `rate.py:`
warnings.

For the same end-to-end check from the rating API, look for non-zero
`rate` rows:

```
openstack rating summary get -b 2026-05-28T15:00:00 -e 2026-05-29T00:00:00
```

## Sources

Pricing anchors (retrieved May 2026):

- Budget VPS (primary anchor):
  [DigitalOcean Droplet Pricing](https://www.digitalocean.com/pricing/droplets) /
  [Vultr Regular Performance Compute](https://www.vultr.com/products/regular-performance-compute/)
- Budget block storage:
  [DigitalOcean Volume Pricing](https://docs.digitalocean.com/products/volumes/details/pricing/)
  ($0.10/GiB-mo, NVMe, 99.99% SLA) /
  [Vultr Block Storage](https://www.vultr.com/products/block-storage/)
  (HDD tier ~$0.05/GiB-mo)
- No-SLA floor for sanity-checking the low end:
  [LowEndBox](https://lowendbox.com/) (4 GB KVM VPS commonly ~$4-5/mo)
- Hyperscaler reference (the level we are deliberately *below*):
  [AWS EC2 On-Demand](https://aws.amazon.com/ec2/pricing/on-demand/) /
  [Google Cloud VM Pricing](https://cloud.google.com/compute/vm-instance-pricing)
- GPU (community marketplace):
  [RunPod RTX A5000](https://www.runpod.io/gpu-models/rtx-a5000) /
  [Vast.ai RTX A5000](https://vast.ai/pricing/gpu/RTX-A5000);
  [Intel Arc Pro B50 specs/MSRP - Tom's Hardware](https://www.tomshardware.com/pc-components/gpus/intel-launches-usd299-arc-pro-b50-with-16gb-of-memory)
- [openstack-exporter nova.go label set](https://github.com/openstack-exporter/openstack-exporter/blob/main/exporters/nova.go)
  (confirms `flavor_id` is *not* a label on `openstack_nova_server_status`
  in the version we deploy -- the trigger for choosing pyscripts over
  hashmap)
