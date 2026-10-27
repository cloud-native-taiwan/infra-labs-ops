#!/usr/bin/env sh
# Single source of truth for the usage-reports reconcile invocation
# (period-job integrity contract, tools/period_reconcile/). Called by both
# the boot-time pass in entrypoint.sh and the hourly supercronic tick, so
# the two cannot drift on watermark path, timezone, or back-fill bound.
#
# {period} is substituted by period-reconcile with each missed month (YYYY-MM).
set -eu

# A dry run delivers nothing, so it must never advance the watermark: the
# month would then never be sent for real. Refuse (exit 2) instead. Asks the
# app's own parser so the two cannot disagree; an unparseable value refuses
# too. PYTHON and PERIOD_RECONCILE are overridable for tests only.
dry_run_rc=0
"${PYTHON:-/usr/local/bin/python3}" -c 'import sys
from infra_labs_common.env_config import get_bool
sys.exit(3 if get_bool("DRY_RUN", False) else 0)' || dry_run_rc=$?
if [ "$dry_run_rc" -ne 0 ]; then
  echo "usage-reports: INFRA_LABS_DRY_RUN is set or invalid; refusing to reconcile" >&2
  exit 2
fi

exec "${PERIOD_RECONCILE:-/usr/local/bin/period-reconcile}" \
  --job usage-reports \
  --watermark /var/lib/usage-reports/reconcile-watermark.json \
  --lock /tmp/usage-reports-reconcile.lock \
  --timezone "${INFRA_LABS_REPORT_TIMEZONE:-Asia/Taipei}" \
  --max-backfill 6 \
  --metrics-file /metrics/usage-reports.prom \
  -- /usr/local/bin/usage-reports generate --month '{period}'
