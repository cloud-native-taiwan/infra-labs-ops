# ADR-0011: OpenStack 2025.2 -> 2026.1 upgrade strategy

- **Status:** Accepted
- **Date:** 2026-05-21
- **Deciders:** CNTUG ops

## Context

A major-release upgrade of the control plane was needed while keeping tenant
compute running, within a maintenance window of about three hours.

## Decision

Run a full `kolla-ansible upgrade` per service in dependency order (MariaDB ->
RabbitMQ -> Keystone -> ... -> Horizon, about 150 minutes) rather than piecemeal
per-service deploys, for atomic cluster state. Pre-pull the 2026.1 images
fleet-wide (about 20-40 minutes) before the window to avoid download latency
inside it. Optionally freeze HAProxy frontends during cutover.

Bump `openstack_release` to 2026.1 and enable the new exporters
(prometheus-openstack-network-exporter for OVS/OVN, valkey exporter). Health-gate
the pre-flight per host (ADR-0017). Manual cleanup is needed on openstack06,
which is removed from `common:children` — Kolla does not auto-remove services
outside `common:children`. A fast path (one or two services) is reserved for
isolated regressions, for example Octavia alone.

Rollback: revert git to the pre-upgrade commit, restore the MariaDB backup, and
redeploy 2025.2.

## Alternatives considered

- Per-service incremental deploy as the primary path. Rejected for that role
  (non-atomic state), but kept as a fast path for isolated fixes.
- No pre-pull. Rejected: download latency would eat the window.

## Consequences

A predictable window and a clean rollback. Inventory renames and prunes are
required: `kolla_toolbox`/`kolla_logs` renames; drop zun/influxdb/telegraf; add
vpn-agent and the new exporter groups. RabbitMQ 4.1->4.2, Horizon Valkey, Glance
in-process TLS, and ProxySQL are handled by Kolla.

## References

- docs/openstack-2026.1-upgrade-runbook.md
- commits 60ed650 (upgrade + exporters), a40380d (rename/prune groups), c7c65f8
  (exporter inventory groups), 4ddabd4 (vpn-agent inventory), d82866d (remove
  non-openstack from common)
