# CloudKitty rate card

This runbook documents how the CNTUG Infra Labs CloudKitty rate card is
structured, how to change rates, and how to re-rate historical usage after
a rate change. It is the operator companion to
`tools/usage_reports/scripts/setup_hashmap.sh`.

## Architecture

- **Collector:** Prometheus. The `openstack_nova_server_status` and
  `openstack_cinder_limits_volume_used_gb` series feed CloudKitty.
- **Storage:** OpenSearch (CloudKitty storage v2). kolla-ansible 2026.1
  dropped influxdb deployment; opensearch is the v2 backend we use, which
  is required for the `/v2/task/reprocesses` API the reprocessing section
  below depends on (sqlalchemy is v1-only and does not expose it).
- **Rating module:** `hashmap`. Compute is rated per flavor as a field
  mapping on the `instance` service, keyed on **`flavor_id`** (not
  `flavor_name`: openstack-exporter only guarantees a `flavor_id` label on
  `openstack_nova_server_status`). GPU flavors get an additional `gpu`-group
  mapping. Storage is a single service-level mapping on the `storage` service.
- **Collection period:** 600 s. All rates are stored as
  *cost per 600 s collection period*; the script derives them from the
  per-hour and per-month unit rates below.

## Pricing model

This is **showback, not billing**. The goal is awareness ("this free
infrastructure has real value") and a nudge to clean up idle VMs. Rates are
provider-neutral (R10) and anchored at **~65% of the budget cloud tier**
(DigitalOcean / Vultr) rather than hyperscalers: Infra Labs offers **no
uptime SLA** and no storage QoS, so the honest peers are budget VPS and
LowEndBox providers, not AWS/GCP. This places a 2 vCPU / 4 GB VM at ~$15/mo,
between the budget VPS rate (~$20-24/mo, but those publish a 99.99% SLA) and
the no-SLA LowEndBox floor (~$5/mo). A single `MULTIPLIER` env var scales the
whole card; lower it (e.g. `0.5`) for an even gentler nudge.

`setup_hashmap.sh` does **not** hand-enumerate flavors. It reads the live
flavor list and derives each flavor's per-period cost from its vCPU and RAM:

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

## CPU-generation pricing (R4)

The fleet spans two AMD EPYC generations: Zen 2 (EPYC 7282, openstack01/02)
and Zen 3 (EPYC 7413, openstack04/05). To bill older hardware less, set
`OLDER_GEN_REGEX` / `OLDER_GEN_MULTIPLIER` (default `0.8`) in
`setup_hashmap.sh`; any flavor whose name matches the regex gets the
discount.

This lever is **off by default**: today's flavors are not pinned to a host
aggregate, so a flavor's generation is non-deterministic and pricing it by
generation would be misleading. Enable it only once generation-specific
flavors exist:

```
openstack aggregate create gen-zen2
openstack aggregate add host gen-zen2 openstack01
openstack aggregate add host gen-zen2 openstack02
openstack aggregate set --property cpu_gen=zen2 gen-zen2

openstack flavor create --vcpus 4 --ram 8192 --disk 40 c1.large.gen2
openstack flavor set --property aggregate_instance_extra_specs:cpu_gen=zen2 c1.large.gen2
```

Then set `OLDER_GEN_REGEX='\.gen2$'` and re-run the script.

GPU flavors are detected automatically from their `pci_passthrough:alias`
property (see `kolla/config/nova/*/nova.conf` for the `TeslaT10`,
`NVIDIA-A5000-24Q`, `NVIDIA-A5000-12Q`, and `Intel-Arc-Pro-B50-VF`
aliases). To price a new GPU type, add it to `GPU_RATE_HOUR` in the script.

## Deployment sequence (end to end)

The CloudKitty + reporting deployment is staged because hashmap rates
must be in place before the first processor cycle, and the report tool
depends on rated data already existing in CloudKitty's OpenSearch storage.

1. `kolla-ansible -i multinode prechecks --tags cloudkitty,opensearch`
2. `kolla-ansible -i multinode reconfigure --tags cloudkitty,opensearch`
3. Confirm API health: `openstack rating module list` returns the
   `hashmap` and `pyscripts` modules.
4. Edit and run `tools/usage_reports/scripts/setup_hashmap.sh` to seed
   the rate card.
5. Wait one or two collection periods (10 to 20 minutes). Validate with
   `openstack rating summary get -b ... -e ...`; non-zero `rate` rows
   should appear for active projects.
6. Provision the tool's secrets first (gitignored, not in the repo):
   place `.env` and `clouds.yaml` under
   `ansible/private/tools/usage_reports/`. Then deploy, running from the
   `ansible/` directory:
   `ansible-playbook playbooks/deploy-usage-reports.yml`.
7. Smoke-test inside the container:
   `docker exec usage-reports usage-reports generate --dry-run --month <YYYY-MM>`.

## First-time setup

> **Important:** Configure the rate card *before* the first CloudKitty
> processor cycle. Periods collected with no matching mapping are stored
> as zero-rated rows and must be reprocessed (see below) once rates exist.

1. Deploy CloudKitty:
   ```
   kolla-ansible -i multinode prechecks --tags cloudkitty,opensearch
   kolla-ansible -i multinode reconfigure --tags cloudkitty,opensearch
   ```
2. Verify the rating module list:
   ```
   openstack rating module list
   ```
   Expect `hashmap` present and `enabled=True` after the script runs.
3. Review the rate variables (`VCPU_RATE_HOUR`, `RAM_RATE_GB_HOUR`,
   `STORAGE_RATE_GB_MONTH`, `GPU_RATE_HOUR`, `MULTIPLIER`) at the top of
   `tools/usage_reports/scripts/setup_hashmap.sh`. The defaults match the
   rate table above; compute mappings are derived from the live flavor
   list, so there is no per-flavor table to maintain.
4. Preview against the live cluster before applying, to validate flavor and
   GPU detection:
   ```
   DRY_RUN=1 ./tools/usage_reports/scripts/setup_hashmap.sh
   ```
   Then run for real from an admin-scoped shell on the deploy host:
   ```
   ./tools/usage_reports/scripts/setup_hashmap.sh
   ```
   The script warns about any GPU flavor whose PCI alias is unpriced; add it
   to `GPU_RATE_HOUR` and re-run until none are reported.
5. Wait one or two collection periods (10 to 20 minutes), then sanity
   check:
   ```
   openstack rating summary get -b 2026-05-01T00:00:00 -e 2026-05-02T00:00:00
   ```
   Confirm non-zero `rate` for projects with active VMs.

## Changing rates

1. Edit the rate variables in `setup_hashmap.sh` (or set `MULTIPLIER` to
   scale the whole card at once).
2. Re-run the script. Mappings are compared numerically, so a re-run with
   unchanged rates is a no-op; new mappings are created.
   *Note:* CloudKitty hashmap does not update mappings in place. To **lower
   or raise** an existing price you must delete the stale mapping first,
   otherwise both the old and new costs apply:
   ```
   openstack rating hashmap mapping list --field <field_id>
   openstack rating hashmap mapping delete <mapping_id>
   ```
3. Re-rate historical periods (see next section).

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
   comma-separated list of scope IDs from step 2:
   ```bash
   curl -sX POST "$RATING_URL/v2/task/reprocesses" \
     -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
     -d '{
       "reason": "Rate card update 2026-05-27",
       "scope_ids": "<scope_id>[,<scope_id>...]",
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

A `DRY_RUN=1` run is the simplest audit: it prints the derived per-period
cost for every live flavor and warns about any GPU alias that is not priced.
Because compute mappings are derived from the live flavor list, new flavors
are always covered; the only gap to watch for is a GPU flavor whose
`pci_passthrough:alias` is missing from `GPU_RATE_HOUR` (it would be billed
compute-only).

To confirm rates are actually flowing through to rated data, check a recent
summary for non-zero rate rows:

```
openstack rating summary get -b 2026-05-01T00:00:00 -e 2026-05-02T00:00:00
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
  (confirms `flavor_id` is the reliable label, not `flavor_name`)
