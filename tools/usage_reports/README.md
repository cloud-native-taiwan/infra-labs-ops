# usage_reports

Monthly cost reports for CNTUG Infra Labs OpenStack projects.

The tool queries CloudKitty for rated usage of the previous calendar
month, enriches resources with Nova/Cinder metadata, looks up project
members from Keystone, and emails per-project HTML cost reports via
Resend.

## What it does

- Reads CloudKitty v2 Summary data grouped by `project_id`, `type`, and
  resource `id`.
- Resolves resource UUIDs to human-readable names and specs via Nova
  and Cinder.
- Discovers project members from Keystone role assignments; skips users
  with no email.
- Sends one HTML email per recipient via Resend, with bilingual content
  (Chinese / English) and a per-resource cost breakdown.
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

## Deployment

The tool runs as a Docker container on the deploy host, scheduled by
supercronic at the 1st of each month at 09:00 local time. See
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
