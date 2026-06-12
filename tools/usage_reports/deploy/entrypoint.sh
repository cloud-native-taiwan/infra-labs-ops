#!/usr/bin/env sh
# Entrypoint for the usage-reports container.
#
# Period-job integrity contract (tools/period_reconcile/): on container start
# we run one reconcile pass immediately, then hand off to supercronic for the
# hourly tick. The boot pass back-fills any closed month that was missed while
# the container was down (supercronic alone does not catch up).
#
# The boot reconcile shares a flock with the hourly tick, so even if a tick
# fires the same minute the container starts, they cannot double-run. Its exit
# code is logged but does NOT fail container start: a refused (exit 2) or
# errored (exit 1) month must not crash-loop the container -- the next hourly
# tick retries, and the watermark records what already succeeded.
set -eu

echo "usage-reports: boot-time reconcile pass starting"
if /app/reconcile.sh; then
  echo "usage-reports: boot-time reconcile pass succeeded"
else
  echo "usage-reports: boot-time reconcile exited $? (non-fatal; hourly tick will retry)"
fi

echo "usage-reports: starting supercronic"
exec supercronic -split-logs /app/crontab
