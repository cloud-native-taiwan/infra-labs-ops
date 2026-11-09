# ADR-0024: Crypto-miner detection via CPU-saturation alert

- **Status:** Accepted
- **Date:** 2026-06-30
- **Deciders:** CNTUG ops

## Context

Tenant VMs were observed running unauthorized crypto miners, showing sustained
all-core CPU saturation matching flavor capacity for roughly 24 hours on shared
lab infrastructure.

## Decision

A `VMSuspectedMinerSustainedCPU` Prometheus alert built from libvirt-exporter
metrics. CPU-time rate is normalized by vCPU count to a 0..1 saturation ratio,
firing when a VM holds sustained high saturation (window and level are in the rule file; the window
was shortened 2026-09-27 after detection proved too slow). The flavor-independent
ratio avoids false positives from workloads that legitimately peg large vCPU
counts. The rule matches on `(domain, instance, job)` rather than `domain`
alone to prevent duplicate-series errors during live migration, and joins
`libvirt_domain_openstack_info` to attach owner identity for routing and
triage. Validated with promtool unit tests.

## Alternatives considered

- **Absolute CPU-time threshold** — rejected; false positives scale with vCPU
  count.
- **Match on `domain` alone** — rejected; duplicate-series errors during live
  migration.
- **Shorter window** — originally rejected; revisited 2026-09-27 and shortened
  because detection was too slow. The rate window still smooths short bursts;
  expect more CPU-heavy-tenant false positives, handled by per-project
  Alertmanager suppression.
- **Deferred ideas**: additional corroborating signals, a per-tenant
  allowlist, and a critical tier.

## Consequences

Sustained abuse is surfaced with owner identity for action. Thresholds live in
`kolla/config/prometheus/`. The rule awaits
deployment via `kolla-ansible reconfigure` to the live Prometheus.

## References

- commit 62cf906 (detect crypto-miner VMs via sustained CPU saturation)
- libvirt exporter; promtool tests
