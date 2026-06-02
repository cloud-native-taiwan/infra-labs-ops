# CloudKitty metering stall (frozen `last_processed`)

This runbook covers the failure mode where CloudKitty silently stops
advancing and the monthly usage report aborts. It is the operator companion
to `kolla/config/cloudkitty.conf` and the
`prometheus_openstack_exporter_interval` setting in `kolla/globals.yml`.

## Symptom

The `usage-reports generate` job (1st of the month, 09:00 Asia/Taipei) exits
**2** with:

```
Scope <id> is lagging (last_processed=<old ts>, period_end=<month end>)
CloudKitty has not finished processing <month>; aborting. Pass --force to override.
```

This is the freshness gate in `tools/usage_reports/.../orchestrator.py`
working as designed: it refuses to under-bill when CloudKitty has not rated
the full month. The bug is upstream, in CloudKitty's metering.

## Root cause

CloudKitty's prometheus **fetcher** discovers scopes with an **instant**
query, `max(openstack_identity_project_info) by (id)`. Prometheus only
answers an instant query while the metric's latest sample is within its
`--query.lookback-delta` (default **5m**). The openstack-exporter scrape
interval was **10m**, so the identity metric was stale for roughly half of
every cycle. CloudKitty's collection `Worker` polls every `collect.period`
(600s) and drifted into the stale half, so `fetcher.get_tenants()` returned
`[]` every cycle, no scope was rated, and `last_processed` froze for the
whole cloud.

Tell-tale signs in `/var/log/kolla/cloudkitty/cloudkitty-processor.log`:

- The `Worker` class logs `scopes [{s}] loaded for fetcher {f}` (the count is
  an upstream unsubstituted-template bug) followed immediately by
  `Finished processing all storage scopes` with **no** per-scope
  `Trying to acquire lock` / `Processing timestamp [...]` lines.
- Per-day log volume flatlines into an identical idle loop; only
  `ReprocessingWorker [...] loaded [0] schedules` runs.

It is NOT a data outage: a range query such as
`count_over_time(openstack_identity_project_info[10m])` still shows
continuous data, because a range query looks back far enough to catch the
10m sample. Only the fetcher's instant query goes stale.

## Confirm

Run the fetcher's exact query as an **instant** query a few times over
~5 minutes:

```
max(openstack_identity_project_info) by (id)
```

It returns the projects right after a scrape, then goes **empty** once more
than `lookback-delta` has elapsed. Empty is the stall.

## Fix

Keep the exporter scrape interval below Prometheus' lookback-delta:

- `kolla/globals.yml`: `prometheus_openstack_exporter_interval: 2m` (and
  `prometheus_openstack_exporter_timeout: 1m`, kept under the interval).

Reconfigure Prometheus, then restart the CloudKitty processors:

```bash
kolla-ansible -i <inventory> reconfigure -t prometheus
# on each control node:
sudo docker restart cloudkitty_processor
```

(Restart clears the stuck poll phase; without it the Worker may stay locked
in the stale window until it drifts out.)

Do NOT raise the interval back to the kolla 10m default without also raising
Prometheus `--query.lookback-delta` above the interval.

## Recovery of the backlog

Because Prometheus retains the metrics continuously, the stalled window is
fully recoverable -- no `--force` needed. Once the fetcher returns scopes,
the processor forward-fills every 600s period from the retained data and
advances `last_processed` to the present (watch the log for
`Processing timestamp [...]` lines resuming). When `last_processed` passes
the target month end, rerun the report:

```bash
# on the deploy host:
docker exec usage-reports usage-reports generate
```

If the backlog is large and you want to bound it explicitly, use the
reprocessing API (see `cloudkitty-rate-card.md`) for the affected range.
