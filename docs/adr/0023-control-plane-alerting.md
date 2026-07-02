# ADR-0023: Control-plane alerting mirroring health-gate predicates

- **Status:** Accepted
- **Date:** 2026-06-27
- **Deciders:** CNTUG ops

## Context

Control-plane services were scraped but never alerted on. A down Galera node,
a RabbitMQ partition, a stale OVN chassis, or a crash-looping Kolla container
stayed invisible; opensearch was unhealthy for roughly four weeks unnoticed.
Some failure modes (a RabbitMQ partition under `partition_handling=ignore`, an
OVN stale chassis) do not map cleanly to standard exporter metrics.

## Decision

A hybrid path:

- **PromQL rules** (in `kolla/config/prometheus/`, auto-loaded via
  `node_custom_config`) cover Galera wsrep state and size, RabbitMQ node-down
  and high-memory, and Kolla container liveness via cAdvisor.
- **A per-controller textfile collector** (`control-plane-alert-collector`
  role, systemd timer) reuses the health-gate's docker-exec checks for the
  unmeasurable cases, emitting gauges (`cpa_rabbitmq_partitions`,
  `cpa_ovn_chassis_missing`, etc.) read by
  `node_exporter --collector.textfile`.

A RabbitMQ partition is detected by querying every node and aggregating in
PromQL (any node reporting a partition fires) — the only reliable signal under
`partition_handling=ignore`. The collector fails safe: on a check error it
emits `*_check_failed=1` (never a healthy value) plus a
`cpa_collector_last_run_timestamp`, so a dead collector is distinguishable from
a healthy cluster (alert if it stops advancing >600s). Each rule comments its
health-gate counterpart so drift is visible. `KollaContainerDown` excludes
ephemeral containers by name (`neutron_ovn_metadata_agent_haproxy_ovnmeta-*`
and short-lived cephadm `ceph-volume` helpers) and uses a 120s window (cAdvisor
drops removed series past the 5m lookback-delta, so steady-state absence is a
documented gap). Alerts ship at warning first, promoted to critical after soak.
Promtool unit tests validate rule logic. Alerts that fight intentional cluster
design were tuned out (kernel-deviation on openstack05's newer kernel,
undeployed CephFS/MDS).

## Alternatives considered

- **Pure PromQL for everything** — rejected; partition and stale chassis do not
  express as metrics.
- **Refactor health-gate into a shared library now** — deferred; v1 mirrors
  predicates with a mapping comment.
- **New Alertmanager routing** — rejected; reuse existing severity labels.
- **Exclude containers by image** — rejected; name patterns keep the persistent
  agent monitored.

## Consequences

Dual coverage (gate plus alerts) with explicit sync via comments. The
fail-safe collector never reads broken-as-healthy. Warn-first soak lets
operators tune debounce. A documented blind spot remains for hours-long
steady-state container absence.

## References

- docs/plans/2026-06-27-002-feat-control-plane-alerts-plan.md
- commits 45d82f9 (control-plane alerts + collector + textfile), ac1136f
  (exclude OVN metadata haproxy), bbfae2f (exclude cephadm helpers), a59223b
  (tune alerts to cluster design)
- kolla/config/prometheus/control-plane-alerts.rules
- ansible/roles/control-plane-alert-collector/
