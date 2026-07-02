# ADR-0020: MariaDB backup via kolla-ansible from deploy host

- **Status:** Accepted
- **Date:** 2026-04-01
- **Deciders:** CNTUG ops

## Context

A prior playbook ran `docker exec mariabackup` on a controller against a
container that does not exist. As a result, backups were silently broken.

## Decision

Use the documented kolla-ansible `mariadb-backup` approach, scheduled from the
deploy host via systemd timers:

- Weekly full backup (Sunday 02:00).
- Daily incremental backup (Monday through Saturday 02:00).

## Alternatives considered

- **`docker exec mariabackup` on a controller** — rejected; it targeted the
  wrong, non-existent container.
- **Ad-hoc manual dumps** — rejected; no schedule and error-prone.

## Consequences

Backups follow the supported Kolla path and run unattended. Restore is part of
the upgrade rollback plan (ADR-0011).

## References

- commit 4593b72 (rewrite MariaDB backup to use kolla-ansible from deploy host)
