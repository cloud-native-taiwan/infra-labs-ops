# CloudKitty rate card

This runbook documents how the CNTUG Infra Labs CloudKitty rate card is
structured, how to change rates, and how to re-rate historical usage after
a rate change. It is the operator companion to
`tools/usage_reports/scripts/setup_hashmap.sh`.

## Architecture

- **Collector:** Prometheus. The `openstack_nova_server_status` and
  `openstack_cinder_limits_volume_used_gb` series feed CloudKitty.
- **Storage:** InfluxDB (CloudKitty storage v2).
- **Rating module:** `hashmap`. Each flavor is one field mapping on the
  `instance` service; storage is a single service-level mapping on the
  `storage` service.
- **Collection period:** 600 s. All rates are expressed in
  *cost per 600 s collection period*.

## Pricing formula

```
per_period_cost = desired_hourly_rate / 6
```

Example: a flavor that should cost `0.12` per hour gets a `0.02` field
mapping cost.

For storage, the unit is GiB-per-period. To express
`X` per GiB per hour, set `STORAGE_RATE_PER_GIB_PERIOD = X / 6`.

## Flavor-to-hardware mapping

CloudKitty rates by flavor name, not by physical host. To price by CPU
generation or GPU type, ensure each hardware tier has its own flavor and
pin the flavor to the appropriate host aggregate using Nova `extra_specs`:

```
openstack aggregate create gen-newer
openstack aggregate add host gen-newer openstack05
openstack aggregate set --property cpu_gen=newer gen-newer

openstack flavor create --vcpus 4 --ram 8192 --disk 40 c1.large.gen-newer
openstack flavor set --property aggregate_instance_extra_specs:cpu_gen=newer c1.large.gen-newer
```

Then add a row to `COMPUTE_RATES` in `setup_hashmap.sh` for
`c1.large.gen-newer` distinct from any legacy `c1.large`.

GPU flavors follow the same pattern with PCI device aliases (see
`kolla/config/nova/openstack05/nova.conf` for the
`Intel-Arc-Pro-B50-VF` and `NVIDIA-A5000-24Q` aliases).

## Deployment sequence (end to end)

The CloudKitty + reporting deployment is staged because hashmap rates
must be in place before the first processor cycle, and the report tool
depends on rated data already existing in InfluxDB.

1. `kolla-ansible -i multinode prechecks --tags cloudkitty,influxdb`
2. `kolla-ansible -i multinode reconfigure --tags cloudkitty,influxdb`
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
   kolla-ansible -i multinode prechecks --tags cloudkitty,influxdb
   kolla-ansible -i multinode reconfigure --tags cloudkitty,influxdb
   ```
2. Verify the rating module list:
   ```
   openstack rating module list
   ```
   Expect `hashmap` present and `enabled=True` after the script runs.
3. Edit `COMPUTE_RATES`, `GPU_RATES`, and `STORAGE_RATE_PER_GIB_PERIOD`
   in `tools/usage_reports/scripts/setup_hashmap.sh` to match the
   cluster's actual flavors and the operator's pricing decisions.
4. Run the script from an admin-scoped shell on the deploy host:
   ```
   ./tools/usage_reports/scripts/setup_hashmap.sh
   ```
   The script logs which flavors have no mapping; iterate until none are
   reported.
5. Wait one or two collection periods (10 to 20 minutes), then sanity
   check:
   ```
   openstack rating summary get -b 2026-05-01T00:00:00 -e 2026-05-02T00:00:00
   ```
   Confirm non-zero `rate` for projects with active VMs.

## Changing rates

1. Edit the rate variables in `setup_hashmap.sh`.
2. Re-run the script. Existing mappings with the same `value` and `cost`
   are skipped; new or changed mappings are created.
   *Note:* CloudKitty hashmap does not support in-place mapping updates
   via field-name-only matching. To change a flavor's price, delete the
   old mapping first:
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

Run periodically to catch silent zero-rated flavors:

```
openstack rating hashmap mapping list --service-id $(
  openstack rating hashmap service list -f value -c "Service ID" -c Name \
    | awk '$2=="instance"{print $1}'
)
openstack flavor list -f value -c Name | sort > /tmp/active.txt
openstack rating hashmap mapping list -f value -c Value | sort > /tmp/mapped.txt
comm -23 /tmp/active.txt /tmp/mapped.txt
```

Any lines printed are flavors that bill at zero.
