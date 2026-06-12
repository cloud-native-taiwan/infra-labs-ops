#!/usr/bin/env sh
# Single source of truth for the usage-reports reconcile invocation
# (period-job integrity contract, tools/period_reconcile/). Called by both
# the boot-time pass in entrypoint.sh and the hourly supercronic tick, so
# the two cannot drift on watermark path, timezone, or back-fill bound.
#
# {period} is substituted by period-reconcile with each missed month (YYYY-MM).
set -eu

exec /usr/local/bin/period-reconcile \
  --job usage-reports \
  --watermark /var/lib/usage-reports/reconcile-watermark.json \
  --lock /tmp/usage-reports-reconcile.lock \
  --timezone "${INFRA_LABS_REPORT_TIMEZONE:-Asia/Taipei}" \
  --max-backfill 6 \
  -- /usr/local/bin/usage-reports generate --month '{period}'
