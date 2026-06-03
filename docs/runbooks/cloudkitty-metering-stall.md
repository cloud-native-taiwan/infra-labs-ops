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

### Per-scope progress is sharded across the processor hosts

`cloudkitty_processor` runs on every control node (`.21`/`.22`/`.24`) against
one shared global scope state. Any worker on any host can win a scope's lock
and advance its `last_processed`, and only **that** host's
`/var/log/kolla/cloudkitty/cloudkitty-processor.log` records the
`Processing timestamp [...]` line. Grepping a single host therefore makes
scopes look stuck when another host has already carried them forward. Always
reconcile across all three hosts (or read the authoritative
`cloudkitty.scope_state` table / `GET /v2/scope`) before concluding a scope is
stalled. (CloudKitty's distributed lock is also not mutually exclusive across
hosts -- multiple workers logging `Acquired lock` for the same scope is benign
noise, not a deadlock.)

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

## A different cause: a deleted project's scope (does NOT recover)

If a single scope is frozen at an old `last_processed` while every other scope
has advanced to the present, the lookback-delta stall above is not the cause --
that one froze *cloud-wide*. The usual culprit is a **deleted project**: once a
project is removed, the fetcher stops discovering its scope, so its
`last_processed` is pinned at the last value forever and can never reach the
period end.

Confirm the project is gone:

```bash
openstack project show <scope_id>   # "No project ... exists" == deleted
```

(Cross-check it is also absent from the exporter:
`curl -s http://<exporter>:9198/metrics | grep 'openstack_identity_project_info{.*id="<scope_id>"'`
returns nothing for a deleted project.)

No recovery action is needed. The report's freshness gate confirms each lagging
scope against Keystone and **skips** one whose project returns a 404, logging
`Ignoring lagging scope <id>: project no longer exists` at WARNING. A live
project's lagging scope still blocks (and a transient Keystone error blocks
too, rather than risk under-billing). So a deleted project no longer wedges the
monthly report -- but the WARNING line is worth noting, since the same branch
fires if a `scope_id` ever stops matching a project id (a fetcher/`scope_key`
change).
