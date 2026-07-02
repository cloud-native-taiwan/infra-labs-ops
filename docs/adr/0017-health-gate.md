# ADR-0017: Fail-closed health-gate pre-flight for disruptive ops

- **Status:** Accepted
- **Date:** 2026-04-01
- **Deciders:** CNTUG ops

## Context

Disruptive operations such as rolling reboots and upgrades previously used
blind, unconditional pauses that never verified the cluster had the redundancy
to absorb the disruption. A down Galera node or a RabbitMQ split could turn a
routine operation into an outage or data loss.

## Decision

Add a `health_gate` Ansible role run as a per-host serial pre-flight, wired into
upgrade.yml, replacing blind pauses. It checks: Galera Synced/Primary/ready at
full size; RabbitMQ with no partition; Ceph HEALTH_OK plus ok-to-stop per OSD;
and OVN chassis registered with ovn-controller live.

The gate fails closed: any check that cannot prove safety aborts the operation.
There is no vacuous pass and no silent fallback. It guards against rabbitmqctl
schema changes by refusing if the running_nodes or partitions keys are missing.
Because `partition_handling=ignore` (ADR-0006) lets a minority node look
healthy, RabbitMQ partition detection queries every controller and fails if any
reports a partition or fewer running nodes than expected. Per-host hazards.yml
(ADR-0002) can block unattended operations with operator acknowledgment and
canary-tier ordering.

## Alternatives considered

- Blind timed pauses: rejected. No proof of safety.
- Single-node RabbitMQ check: rejected. Cannot detect split-brain under ignore.
- Defaulting missing JSON fields: rejected. Masks formatter changes and passes
  unsafely.

## Consequences

Operations refuse rather than guess. An over-cautious gate occasionally blocks
work, with an explicit operator bypass available. The same predicates are
mirrored by the control-plane alert collector (ADR-0023).

## References

- commit 7c8588f (health-gate role)
- ansible/roles/health-gate/* (defaults/main.yml, tasks/rabbitmq.yml, tasks/hazards.yml)
- docs/ideation/2026-05-01-rolling-reboot-ha-gate-ideation.md
