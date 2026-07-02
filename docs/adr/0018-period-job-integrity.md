# ADR-0018: Period-job integrity contract for scheduled jobs

- **Status:** Accepted
- **Date:** 2026-06-01
- **Deciders:** CNTUG ops

## Context

Supercronic silently skips a fire if the container is down at that minute, so a
closed month could go silently unreported by usage_reports. Trusting that cron
actually fires is not safe for billing-grade jobs.

## Decision

Invert the model from "fire at a time" to "reconcile toward: a successful run
exists for every closed period". A `period_reconcile` package keeps a
last-success watermark persisted atomically (tempfile, rename, and dual fsync of
both the file and its directory, because rename is not durable until the
directory entry is flushed).

The crontab becomes an hourly reconcile tick plus a boot-time pass, both calling
a shared deploy/reconcile.sh. Back-fill is oldest-first and bounded; it stops at
the first non-zero exit and advances the watermark only past zero-exit periods,
so it resumes from the exact failure and never skips a hole. Watermark labels
are validated against `\d{4}-(0[1-9]|1[0-2])` so a malformed label cannot
misorder months. A non-blocking advisory flock protects against double-fire
(hourly tick versus boot pass); the second caller exits cleanly.

Exit-code contract: 0 means success or nothing-to-do, 2 means gated/retry-later
(letting the freshness gate block without alerting), and 1 means error/alert-
human. Wrapped jobs must be idempotent for closed periods; usage_reports
achieves this via a per-recipient delivery manifest, also persisted atomically.

## Alternatives considered

- Trust cron fires: rejected. Silent skips.
- os.replace without dir fsync: rejected. Not crash-durable.
- Skip-ahead back-fill: rejected. Jumps holes.
- Blocking lock: rejected. The second caller should bow out, not queue.

## Consequences

Every closed period eventually gets exactly one successful run despite outages
or clock skew. Gates can defer (exit 2) without paging. Idempotency becomes a
hard requirement on any wrapped job.

## References

- commit 6ccdd29 (period_reconcile contract, adopted in usage_reports)
- tools/period_reconcile/src/period_reconcile/{watermark,reconcile,lock}.py
