# period_reconcile

[中文](README.md)

A period-job integrity contract for deploy-host cron jobs.

## The problem

Unlike `systemd Persistent=true`, supercronic does **not** catch up a job that
was due while the container was down -- and gives no signal that it skipped
(see `docs/troubleshooting.md`). For a period-anchored job -- such as the
monthly usage report -- a missed fire window means the report for a closed
month silently never runs, and the only detection mechanism is a human
noticing its absence.

This package inverts the model from "fire at a wall-clock time" to "reconcile
toward the invariant: a successful run exists for every **closed** period". A
thin wrapper records a per-job **last-success watermark** on disk and, on
every hourly tick and on container start, back-fills each closed period that
has not yet succeeded.

This mirrors the doctrine the repo already applies on the data side
(usage_reports' freshness gate "refuses rather than under-bills", per-scope
`last_processed` watermarks); the scheduler layer was the one place that did
not follow it.

## The contract

### Watermark (one per job)

A JSON file on the job's existing persistent volume (co-located with the
job's own state so a container rebuild does not lose it). Format:

```json
{
  "version": 1,
  "job": "usage-reports",
  "last_success_label": "2026-05",
  "updated_at": "2026-06-01T01:00:00+00:00"
}
```

- Absent file = "no period has ever succeeded" (fresh deploy).
- Corrupt or schema-mismatched = **hard error**, refuse to run (refuse rather
  than guess). Treating a corrupt file as empty could re-run already-delivered
  periods or skip periods on a later write.
- `last_success_label` must be zero-padded `YYYY-MM` (or `null`); anything
  else is treated as corrupt. Labels are compared lexicographically, so an
  unpadded value like `2026-5` would sort wrongly and silently skip months.
- `last_success_label` only ever moves forward, never regresses.

### Period computation

Only the **monthly** cadence is implemented today. A period is a half-open
interval `[start, end)` anchored on calendar boundaries in a configured
timezone (the cluster runs `Asia/Taipei`). A period is "closed" once
wall-clock time has passed its exclusive `end`. **Only closed periods are ever
back-filled** -- an open (still-accruing) period has incomplete data and must
not be reported.

The model is deliberately factored so other cadences (weekly, daily) can be
added without touching the back-fill loop: a cadence only supplies "the period
containing instant T" and "the period after P".

### Back-fill loop and exit codes

Follows the repo's 0/2/1 contract; the return value reflects the **worst**
outcome across all attempted periods:

| Exit | Meaning | Behaviour |
|------|---------|-----------|
| 0 | success | Nothing to do, or every attempted period exited 0 |
| 2 | refused/gated | A wrapped job exited 2 (e.g. usage_reports' freshness gate: data not ready). Stop and retry next tick. The watermark does **not** advance past the refused period |
| 1 | error | A wrapped job exited 1 (or any unexpected code). Stop and surface failure so a human is alerted |

- Periods are attempted **oldest-first** and the loop **halts at the first
  non-zero exit** (stop-on-failure) so a hole is never skipped.
- The watermark advances **only past periods that exited 0**, persisted
  atomically (tmp + rename + fsync) after each success, so a partial back-fill
  resumes exactly where it left off on the next tick.
- `--max-backfill` (default 6) caps how many periods one pass attempts
  (oldest-first), so a long outage produces a bounded, predictable run.

### Double-fire protection

The hourly tick and the container-start run can overlap, as can a long-running
tick and the next tick. The wrapper takes a non-blocking advisory `flock`. If
another reconcile already holds it, this invocation exits **0** (the other
pass owns the work) -- it neither blocks nor double-runs.

### Idempotency requirement on wrapped jobs (read this)

Back-fill re-runs the wrapped job for a **closed** period. The wrapped job
**must** be idempotent for re-running a period -- a re-run must not produce
duplicate side effects.

- **usage_reports satisfies this**: its per-recipient delivery manifest (keyed
  `period/project/email`) skips already-delivered recipients on a re-run, and
  its freshness gate exits 2 while CloudKitty has not finished rating the
  month. CloudKitty's rated data for a closed month is permanent, so re-running
  a month is safe.
- Any new consumer must confirm "re-running the same period is safe" before
  adopting this contract.

## CLI

```
period-reconcile \
  --job usage-reports \
  --watermark /var/lib/usage-reports/reconcile-watermark.json \
  --lock /tmp/usage-reports-reconcile.lock \
  --timezone Asia/Taipei \
  --max-backfill 6 \
  -- /usr/local/bin/usage-reports generate --month {period}
```

Everything after `--` is the wrapped job's command template. The literal token
`{period}` is substituted with each missed period's label (e.g. `2026-05`)
before each run. A template without `{period}` is refused (exit 1) so it
cannot silently ignore the period being back-filled.

## Consumer adoption status

| Consumer | Status | Notes |
|----------|--------|-------|
| `usage_reports` | **Adopted** | Monthly report; crontab is now an hourly reconcile and the entrypoint runs a boot-time pass. Wrapped command: `usage-reports generate --month {period}` |
| `account_automation` | Not applicable (documented) | Reconciles current spreadsheet state daily; **not** anchored on closed periods. Missing a day just means the next day's run handles it; re-running a past date is meaningless, and its side effects (emails, resource deletion) have no "re-running the same day is safe" guarantee. Already has its own flock. **To adopt**: would first require defining "day" as a period and proving per-day re-run idempotency -- judged not worthwhile today. |
| `keystone-totp` | Blocked (not yet tracked in git) | The tool is not committed to the repo, so its invocation model is unverified here and it was not wired. Its known cron job (`cleanup-stale-credentials`) prunes *currently* expired credentials and is not period-anchored, so it is unlikely to need this contract -- but that should be confirmed against the committed tool before any decision. **To adopt**: first land the tool in git, then verify whether any of its jobs produce a per-closed-period artifact. |

Any new consumer must read "Idempotency requirement on wrapped jobs" above
before adopting.

## Operations

### Inspect the watermark

```bash
docker compose exec usage-reports cat /var/lib/usage-reports/reconcile-watermark.json
```

### Reset the watermark

Deleting the watermark file makes the next tick treat the job as a fresh
deploy: it back-fills only the **single most-recent** closed period (never the
entire history). To force older months, set `last_success_label` to an earlier
label (or `null`) by hand and let the tick fire -- but first confirm the
wrapped job can still safely re-run those months.

```bash
# Trigger one reconcile pass by hand; reconcile.sh is the single shared
# invocation used by both the crontab and the entrypoint (see
# tools/usage_reports/deploy/reconcile.sh).
docker compose exec usage-reports /app/reconcile.sh
```

## Running tests

```
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check
uv run mypy --strict src
```
