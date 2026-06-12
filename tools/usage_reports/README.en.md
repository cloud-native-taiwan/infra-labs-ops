# usage_reports

[中文](README.md)

Monthly cost reports for CNTUG Infra Labs OpenStack projects.

The tool queries CloudKitty for rated usage of the previous calendar
month, enriches resources with Nova/Cinder metadata, looks up project
members from Keystone, and emails per-project HTML cost reports via
Resend.

## What it does

- Reads CloudKitty v2 Summary data, grouping by the collector's stored
  keys: `tenant_id` (the project) and, for compute, the per-instance
  `uuid`. Storage carries no `uuid`, so it is fetched as a project-level
  aggregate in a second query and merged. These keys must match
  `kolla/config/cloudkitty/metrics.yml`; querying `project_id`/`id`
  returns an empty summary for every project.
- Resolves resource UUIDs to human-readable names and specs via Nova
  and Cinder.
- Discovers project members from Keystone role assignments; skips users
  with no email.
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
uv pip install -e ".[dev]"
cp .env.example .env  # fill in real values
uv run usage-reports generate --dry-run --month 2026-05
```

`--dry-run` skips Resend calls and prints report summaries to stdout.
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

The gate ignores a lagging scope whose project no longer exists: when a project
is deleted, CloudKitty's fetcher stops discovering its scope, so `last_processed`
freezes below the period end forever and would otherwise wedge the report
indefinitely. The gate confirms each lagging scope's project against Keystone and
skips it **only on a definitive 404** (logged at WARNING: `Ignoring lagging scope
... project no longer exists`). A transient Keystone error is treated as
"still live" and keeps blocking, so a momentary outage never causes a live
project to be under-billed.

A mistyped `--only-project` (matching no billable project for the period,
including a period with no usage at all) exits **2** rather than silently
succeeding. The most common cause is passing a project *name* instead of its
UUID, or a project that genuinely had no rated usage that month. A `--only-email` that matches no member of a project logs a
warning and skips that project; this is exit **0** because, when scoping by
email alone across multiple projects, a non-matching project is expected.

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

## Rate card

Pricing is managed via CloudKitty's hashmap rating module. See
`docs/runbooks/cloudkitty-rate-card.md` and
`scripts/setup_hashmap.sh`.

## Running tests

```
uv run pytest
uv run ruff check
uv run mypy --strict src
```
