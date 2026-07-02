# ADR-0028: keystone-totp archived; TOTP enforcement deferred to upstream

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** CNTUG ops

## Context

An in-house TOTP enrollment/recovery service (`tools/keystone-totp/`) was
built but never deployed. Audits found its wiring would not work if revived
(blueprints unregistered, wrong exception classes caught, rate-limit window
bug) even though its crypto fundamentals and container hardening are solid.
Horizon 2025.2+ ships native TOTP QR enrollment, but the end-to-end
self-service story (enrollment, recovery, lockout UX) upstream is still
immature for a community cloud with volunteer support capacity.

## Decision

Do not enforce TOTP for tenants until upstream Keystone/Horizon self-service
support matures. The keystone-totp tree stays archived out of the public
repo (gitignored, ARCHIVE_NOTICE retained locally) rather than deleted, as
reference for the eventual adoption. None of its known-broken wiring gets
fixed while archived.

## Alternatives considered

- Fix and deploy keystone-totp: rejected. Substantial security-sensitive
  surface (day-zero bearer-token enrollment links, password oracle risks) for
  a service upstream is expected to obsolete.
- Delete the tree outright: rejected for now. Preflight scripts and hardening
  patterns retain reference value; deletion loses them for no risk reduction
  since the tree is not published.
- Enforce TOTP via native Horizon enrollment today: rejected. No recovery
  flow means volunteer operators become the lockout help desk.

## Consequences

Tenant accounts remain password-only; compensating controls are the Keystone
lockout policy (5 failures / 1800s) and failed-auth detection work tracked in
the monitoring backlog. Revisit when Horizon/Keystone ship a usable
self-service recovery flow.

## References

- tools/keystone-totp/ARCHIVE_NOTICE (local, untracked)
- 2026-07-02 whole-repo/fleet audit, section 4 "keystone-totp fate"
