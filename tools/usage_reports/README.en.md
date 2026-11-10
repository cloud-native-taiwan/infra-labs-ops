# usage_reports

[中文](README.md)

Monthly cost reports for CNTUG Infra Labs OpenStack projects.

The tool queries CloudKitty for rated usage of the previous calendar
month, enriches instances with Nova metadata (storage is a CloudKitty
project-level aggregate), looks up project members from Keystone, and
emails per-project HTML cost reports via Resend.

## What it does

- Reads CloudKitty v2 Summary data, grouping by the collector's stored
  keys: `tenant_id` (the project) and, for compute, the per-instance
  `uuid`. Storage carries no `uuid`, so it is fetched as a project-level
  aggregate in a second query and merged. These keys must match
  `kolla/config/cloudkitty/metrics.yml`; querying `project_id`/`id`
  returns an empty summary for every project.
- Resolves instance UUIDs to human-readable names and specs via Nova.
  The run builds one cross-project index of servers up front (a single
  bulk list call), so enrichment is a dict lookup rather than a
  per-resource GET; an instance missing from the index is still confirmed
  with a single GET before being labelled deleted. Storage is not enriched
  per-volume -- CloudKitty reports it as a project-level aggregate.
- Discovers project members from Keystone role assignments; skips users
  with no email or that Keystone reports gone (404). A member whose
  lookup fails transiently is retried, and if it still fails the run
  reports the recipient as unresolved and exits non-zero rather than
  silently dropping their report.
- Sends one HTML email per recipient via Resend, with bilingual content
  (Chinese / English) and a per-resource cost breakdown. Costs are
  labelled in USD.
- Tracks deliveries in a JSON manifest so reruns are idempotent.

## What it does not do

- Real billing, invoicing, or payment collection.
- Quota enforcement or automatic resource cleanup.
- Network usage metering.
- Real-time dashboards (Horizon / Skyline cover that).

## Configuration

All configuration is via `INFRA_LABS_*` environment variables. See
`.env.example` for the complete list.

CloudKitty access uses the `clouds.yaml` profile named by
`INFRA_LABS_OPENSTACK_CLOUD`. The credential must have the
`rating:rating:get_all` permission (typically the `admin` role).

Set `api_timeout: 30` in `clouds.yaml` to bound how long Keystone /
Nova / Cinder calls can hang. CloudKitty HTTP calls are bounded by a
30 s timeout in code (see `cloudkitty_service.HTTP_TIMEOUT_SECONDS`).

## Running locally

```
cd tools/usage_reports
uv venv
uv pip install --no-deps -e ../infra_labs_common  # shared library; never on PyPI
uv pip install -e ".[dev]"
cp .env.example .env  # fill in real values
uv run usage-reports generate --dry-run --month 2026-05
```

`--dry-run` skips Resend calls and logs what would be sent. In the container,
`deploy/reconcile.sh` refuses to run (exit 2) while `INFRA_LABS_DRY_RUN` is
true, so a dry-run month is never recorded as done (it would otherwise never
be sent for real once dry-run is switched off). Because no pass completes,
`PeriodReconcileStale` fires about 2 hours into a dry-run deployment; that is
expected.
`--month YYYY-MM` selects the reporting period; omit to use the
previous calendar month in the configured timezone.
`--force` bypasses the freshness check and the delivery idempotency
manifest (use when re-sending or testing off-cycle).

### Testing one user

`--only-project <project_id>` and `--only-email <address>` scope a run to a
single project and/or recipient so you can preview or send just one person's
report instead of every project member. `--only-project` takes the project
**UUID**, not its name -- get it with `openstack project show <name> -c id
-f value`. They compose with the flags above:

```
# Render only Alice's report, send nothing:
uv run usage-reports generate --dry-run --force --only-email alice@example.com

# Actually send only Alice's report for one project:
uv run usage-reports generate --force \
  --only-project 1a2b... --only-email alice@example.com
```

`--force` is recommended for these test runs: it skips the freshness gate and
the manifest so a prior delivery does not suppress the resend.

A scoped run (with `--only-project` and/or `--only-email`) **does not record
deliveries to the manifest**. This is a safety guard: a real scoped test send
would otherwise mark that recipient as delivered, and the next scheduled run
would silently skip them -- leaving them with whatever (possibly incomplete)
report the test produced. If you are using a scoped run to *legitimately*
deliver to one missed recipient and want it recorded, add `--record-deliveries`.

A scheduled run also exits **2** when CloudKitty has not finished rating the
month yet (the freshness gate: `CloudKitty has not finished processing ...`).
If this persists across runs, CloudKitty metering has stalled -- see
`docs/runbooks/cloudkitty-metering-stall.md`.

CloudKitty's scope state is sharded across the processor hosts, so a single
scope can appear in several `/v2/scope` rows that advance independently. The
gate folds those rows to the **slowest** (minimum) `last_processed` per scope --
a row with no timestamp (a shard that has never processed the scope) counts as
not-ready -- so it waits for the laggard shard instead of passing early on the
fastest one and under-billing.

The gate ignores a lagging scope whose project no longer exists: when a project
is deleted, CloudKitty's fetcher stops discovering its scope, so `last_processed`
freezes below the period end forever and would otherwise wedge the report
indefinitely. The gate confirms each lagging scope's project against Keystone and
skips it **only on a definitive 404** (logged at WARNING: `Ignoring lagging scope
... project no longer exists`). A transient Keystone error is treated as
"still live" and keeps blocking, so a momentary outage never causes a live
project to be under-billed.

Conditions that a rerun can never fix must not fail the month, because the
reconciler retries a failed month forever and blocks every later month behind
it (this stalled all reports from 2026-07 to 2026-10). So:

- A project deleted after it accrued usage has no one left to email. It is
  skipped with a WARNING (`Project no longer exists`).
- A recipient address Resend rejects (a validation error naming the `to`
  field) is skipped with an ERROR (`Recipient rejected by Resend`), not
  retried, and not recorded as delivered. Fix the address in Keystone, then
  deliver with `--only-project <id> --only-email <address>
  --record-deliveries`.

Any other send failure -- including validation errors about the sender or
domain, which hit every send -- or a member whose Keystone lookup keeps
failing, still exits **1**. Each send carries a Resend idempotency key (a
digest of the manifest key), so a retry after a lost response is not
delivered twice.

A mistyped `--only-project` (matching no billable project for the period,
including a period with no usage at all) exits **2** rather than silently
succeeding. The most common cause is passing a project *name* instead of its
UUID, or a project that genuinely had no rated usage that month. A `--only-email` that matches no member of a project logs a
warning and skips that project; this is exit **0** because, when scoping by
email alone across multiple projects, a non-matching project is expected.

Every run logs one `Month summary month=... projects=... total_cost=...
unpriced_instances=...` line before sending anything. An instance with active
hours but $0 cost was not priced by `rate.py` (its flavor could not be
resolved from Nova); these are counted there and named in a WARNING
(`... have active hours but $0 cost ...`), so an under-counted month is never
silent. A VM priced for only part of the month is not counted there (the
summary has one row per VM per month); look for `UNPRICED` in the
`cloudkitty_processor` log. A month whose CloudKitty summary is empty, totals
$0 across every project, or has running instances but none priced, exits
**2** (refused) without sending, so `period-reconcile` does not
advance the watermark past it: in this lab that means rating is broken or data
is missing, not a free month. `--force` overrides the refusal for a manual
run; a `--only-project` run is exempt, since one project can legitimately
cost $0. The scheduled reconciler never passes `--force`, so a month that
really has no data blocks every later month (and fires `PeriodReportOverdue`)
until an operator moves the watermark past it. See "Reset the watermark" in
`tools/period_reconcile/README.en.md`.

## Deployment

The tool runs as a Docker container on the deploy host. Scheduling is no
longer a single monthly fire (supercronic silently skips it if the container
is down at that minute); it follows the period-job integrity contract from
`tools/period_reconcile/`: supercronic runs `/app/reconcile.sh` hourly
(`17 * * * *`) and the entrypoint runs one pass at container start,
back-filling every closed month that has not yet succeeded (a watermark
records progress; a flock prevents double-fires). See `deploy/reconcile.sh`,
`deploy/crontab`, `deploy/entrypoint.sh`, and
`ansible/playbooks/deploy-usage-reports.yml`.

The code rsync excludes `.env`, `secrets/`, and `state/` (delivery manifest
and watermark), so `--delete` never removes them. The container mounts
`secrets/` as a directory, read-only, and the playbook recreates the container
when `.env` or `clouds.yaml` changed. See "Secrets" in `tools/README.en.md` to
rotate one.

Deploy with an immutable image tag published by
`.github/workflows/build-tools.yml`:

```
ansible-playbook -i ansible/hosts ansible/playbooks/deploy-usage-reports.yml \
  -e tools_image_tag=sha-<commit>
```

Alerting: each reconcile pass writes Prometheus gauges to
`/var/lib/node_exporter/textfile_collector/usage-reports.prom` on the deploy
host. The playbook installs a textfile-only `prometheus-node-exporter` bound to
the internal address, kolla Prometheus scrapes it
(`kolla/config/prometheus/prometheus.yml.d/deploy-host.yml`), and
`kolla/config/prometheus/period-reconcile-alerts.rules` alerts when a closed
month still has no report after 3 days (`PeriodReportOverdue`) or no pass has
completed in 2 hours (`PeriodReconcileStale`). See `tools/period_reconcile/`.

## Rate card

Pricing is computed by CloudKitty's pyscripts rating module, not hashmap. See
`docs/runbooks/cloudkitty-rate-card.md`, `scripts/rate.py`, and
`scripts/setup_pyscript.sh`.

CloudKitty can rate a period long after it ended (a stall backlog or a
reprocess). `rate.py` therefore prices a VM that has since been deleted from
the flavor Nova recorded with the deleted instance, and never drops a VM it
has seen from its cache. A VM it still cannot price stays at $0 with an
`UNPRICED` warning in the `cloudkitty_processor` log and is counted in the
report's `Month summary` line.

To re-check a past month's totals without sending email, run a dry run on the
deploy host and read its `Month summary` line:

```
docker exec usage-reports usage-reports generate --dry-run --month 2026-07
```

The dry run reads what CloudKitty has stored. To see the corrected totals,
first push the fixed `rate.py` and reprocess the months. The procedure is in
"Re-checking past months after a pricing fix" in
`docs/runbooks/cloudkitty-rate-card.md`.

## Running tests

```
uv run pytest
uv run ruff check
uv run mypy --strict src
```
