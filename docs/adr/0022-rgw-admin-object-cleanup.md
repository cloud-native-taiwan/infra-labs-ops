# ADR-0022: Object-storage cleanup via RGW Admin REST API

- **Status:** Accepted
- **Date:** 2026-05-01
- **Deciders:** CNTUG ops

## Context

With `rgw_keystone_implicit_tenants=true`, Swift cross-project access is broken:
Ceph RadosGW ignores `AUTH_{id}` URL segments under an admin token and always
serves the admin account's containers. The Swift/Keystone path therefore cannot
list or delete another project's buckets for cleanup.

## Decision

An `RgwAdminClient` using AWS SigV4 / S3 credentials against the RGW Admin REST
API, bypassing Keystone token scoping. It discovers all orphaned
implicit-tenant UIDs without per-project hints. Because buckets live under a
tenant namespace equal to the Keystone project ID, `delete_bucket()` passes a
tenant field (otherwise requests 404 against the legacy empty tenant). RGW
region is configurable for SigV4 credential scope.

The destructive path is hardened:

- Fail closed on non-HTTPS admin URLs (loopback exempt).
- Shape guards: `delete_user` accepts only implicit-tenant UIDs via
  `re.fullmatch`; `delete_bucket` rejects malformed project-id shapes,
  defeating a newline-bypass.
- Re-verify orphan status immediately before deletion and skip any project that
  reappeared, closing the TOCTOU window.
- Delete RGW users only after all their buckets are gone (user delete is
  idempotent, bucket delete is not).

## Alternatives considered

- **Swift API with Keystone tokens** — rejected; implicit-tenant scoping is
  ignored by RGW.
- **Per-project hints to find buckets** — rejected; the admin API enumerates
  all implicit-tenant UIDs.
- **Signing over cleartext HTTP** — rejected; credential exposure, now fails
  closed.

## Consequences

Cleanup works for any implicit-tenant project. Admin credentials are powerful,
so HTTPS plus shape guards plus re-verify are mandatory. Gating user deletion
on bucket-purge success allows safe retry.

## References

- commits 3b3023c (Swift → RGW admin API), f42fb08 (tenant for implicit-tenant
  deletion), 05f4569 (configurable region), 9e6fbc4 (harden destructive path I5)
- tools/account_automation/scripts/purge_dangling_resources.py
