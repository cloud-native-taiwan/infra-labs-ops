# ADR-0016: openstack04 NUMA BIOS remediation

- **Status:** Accepted
- **Date:** 2026-03-31
- **Deciders:** CNTUG ops

## Context

openstack04 OOM-killed VMs while roughly 60 GiB was free host-wide. Kernel logs
showed CONSTRAINT_CPUSET with mems_allowed=0: the host exposed 4 Linux NUMA
nodes, a result of the "L3 cache as NUMA Domain" BIOS setting. VMs were strictly
pinned to node 0's local memory, so node 0 exhausted while nodes 1 to 3 still
had free RAM. openstack05, an identical AMD EPYC 7413, ran fine with NPS1 and
L3-as-NUMA disabled.

## Decision

Reconfigure the openstack04 BIOS to match the working openstack05: set NUMA
Nodes Per Socket to 1 and disable L3 cache as NUMA Domain, collapsing the host
to a single NUMA node.

## Alternatives considered

- Increase ring buffers: rejected. Irrelevant; this is NUMA locality, not
  buffers.
- Tune Linux NUMA policies: rejected. The root cause is BIOS, not kernel
  settings.
- NPS change alone: insufficient. L3-as-NUMA is the deciding factor.

## Consequences

VMs can use all host memory. This is a one-time hardware calibration that the
repo cannot encode in software (BIOS-level). It documents the L3-as-NUMA gotcha
for any future hosts.

## References

- docs/openstack04-numa-oom.md
