# ADR-0019: Automated TLS certificate renewal

- **Status:** Accepted
- **Date:** 2026-04-05
- **Deciders:** CNTUG ops

## Context

HAProxy public certificates required a manual `certbot` run followed by a
`kolla-ansible reconfigure` every 60-90 days. Forgetting the renewal window
risks an outage of the API and dashboard endpoints once the certificate
expires.

## Decision

A systemd oneshot service paired with a twice-daily timer (00:00 and 12:00
UTC+8, `RandomizedDelaySec=1h`, `Persistent=true` to catch missed runs) on the
deploy host. A wrapper script:

1. Runs `certbot renew`.
2. Compares the renewed cert fingerprint against a persisted deployed-stamp
   (`haproxy.pem.deployed`) — written only after a successful fleet push, so a
   failed push is retried on the next run rather than silently skipped. A
   lineage guard verifies the renewed cert matches `cloudnative.tw`.
3. Only on a real change: backs up the old `haproxy.pem`, atomically writes the
   new PEM (fullchain + privkey via temp file + `mv`), runs
   `kolla-ansible reconfigure -t haproxy` as the deploy user, propagates the
   exit code, then records the fingerprint to the deployed-stamp.

certbot runs as root (needs `/etc/letsencrypt`), but `kolla-ansible` runs as
the deploy user via `runuser` because that user's SSH key is authorized on the
fleet; `haproxy.pem` is chowned to the deploy user so it is readable. The
service unit injects the operator virtualenv plus the `uv` tool bin (certbot is
installed via `uv tool install certbot`) on `PATH`. Deployed via Ansible,
mirroring the MariaDB backup pattern (ADR-0020).

## Alternatives considered

- **cron** — rejected; systemd provides `Persistent=true`, journald logging,
  and a randomized delay.
- **`certbot --deploy-hook`** — rejected; it does not propagate certbot's exit
  code, whereas a wrapper does.
- **Reconfigure on every run** — rejected; the fingerprint compare avoids
  needless reconfigures.

## Consequences

Renewal is hands-off with failure detection and a manual rollback path (backup
plus atomic write). A stale certificate left by a missed run is caught at boot
via `Persistent=true`.

## References

- docs/plans/2026-04-05-001-feat-auto-cert-renewal-plan.md
- commit ef960a5 (cert-renewal playbook + script)
