# ADR-0021: Account lifecycle automation and deletion safety

- **Status:** Accepted
- **Date:** 2026-04-01
- **Deciders:** CNTUG ops

## Context

A Google Sheet drives OpenStack account lifecycle for a small lab (~20
accounts). Account deletion is irreversible across many dependent resources.
Expiry previously had no teeth: the email told users to "contact admin", and
expired users were never disabled. A self-service form and token flow was
scoped but proved too heavy for this scale. (Renewal of this decision:
2026-06-27.)

## Decision

1. **Two-phase, admin-gated deletion.** `PENDING_DELETE` enriches a preview of
   ~10 resource types (servers, volumes, networks, ports, routers, FIPs,
   security groups, snapshots, load balancers, images) plus Keystone group
   members and emails the admin. The admin must set `READY_TO_DELETE` before
   anything is destroyed; then the state moves to `DELETED`.
2. **Ordered resource purge before project deletion**: servers → load_balancers
   → floating_ips → routers → ports → snapshots → volumes → networks →
   security_groups → images. Routers clear their external gateway and static
   routes before delete to avoid a 409. Deletes use explicit
   `wait_for_delete()` (the Proxy layer rejects `wait=True`).
3. **Admin-driven renewal.** Typing `RENEWAL` in the Status cell recomputes
   expiry as `max(parse_duration(today), current_expiry)` (extend-only; to
   shorten, edit the duration), idempotently enables the Keystone user, clears
   `ExpiryEmailSentAt` to re-arm warnings, and sets `ACTIVE`. Renewing a
   non-existent user fails loudly rather than creating a ghost.
4. **Disable on expiry.** At the `EXPIRING` → `EXPIRED` edge, disable the
   Keystone user once, so expiry now has teeth.
5. **Per-row failure isolation.** Sheet-write failures are caught, logged as
   failures, and the loop continues; OpenStack mutations are idempotent and the
   next cron run retries.

## Alternatives considered

- **Immediate deletion** — rejected; irreversible with no review.
- **Self-service renewal form + token + email machinery** — superseded;
  admin-driven is simpler and fits ~20 accounts.
- **All-or-nothing per-pass on first write error** — rejected; one bad row
  blocked everything.

## Consequences

Destructive operations require explicit human approval and surface a full
inventory first. Expiry actually locks accounts. The idempotent design
tolerates partial failures and retries, and the date rule is extend-only by
design.

## References

- docs/plans/2026-05-07-001-feat-account-renewal-form-plan.md (superseded)
- docs/plans/2026-06-27-001-feat-account-renewal-disable-on-expiry-plan.md
- commits f940cf7 (deletion review state), c86b11c (enriched preview + group
  deletion), 4fcc551 (purge before delete + async wait), fcc1860 (admin
  renewal + disable-on-expiry), 3dc5953 (per-row failure isolation)
- Account automation design + OpenStack API quirks notes
