# ADR-0032: librbd client-side cache stays disabled

- **Status:** Accepted (tested live 2026-07-04)
- **Date:** 2026-07-04
- **Deciders:** CNTUG ops

## Context

The 2026-07 audit deferred a P3 suggestion to enable librbd client-side
caching (`rbd_cache` / libvirt `cache='writeback'`) pending performance
testing (ADR-0031 recorded the deferral). Tested 2026-07-04 on a live guest
on openstack04 against a fresh 12G RBD-backed cinder volume, flipping the
disk cache mode live via libvirt (not nova), fio with direct=1, 5 runs per
workload per mode.

Results (mean of 5, `cache='none'` vs `cache='writeback'`):

- randread 4k: 61573 vs 61349 IOPS (-0.4%)
- randwrite 4k: 21418 vs 20924 IOPS (-2.3%)
- seqwrite 1M: 490 vs 361 MiB/s (writeback worse; both noisy, stdev 75-128)
- seqread 1M: invalid — the fresh volume served zero-fills for unallocated
  objects, producing non-physical throughput; treat as no cache effect.

librbd writeback mainly helps buffered small-write coalescing and re-reads
of a hot working set; a direct-IO deep-queue matrix gains nothing and pays
an extra copy.

## Decision

Keep `cache='none'` (librbd cache disabled) as the fleet default. Writeback
showed no benefit on this workload matrix and adds per-VM memory footprint
plus a crash-consistency nuance (acknowledged writes buffered until guest
flush).

## Alternatives considered

- **Enable writeback fleet-wide** (`disk_cachemodes network=writeback` +
  `rbd cache writethrough until flush = true`): rejected on data — no
  measurable gain on any workload tested, plus memory and crash-consistency
  costs.
- **Per-flavor/per-guest writeback** for buffered read-heavy workloads:
  possible later, but requires a targeted retest (direct=0, pre-filled
  volume, re-read working set) demonstrating a win first.

## Consequences

- No configuration change; the audit item closes as rejected-with-data.
- A targeted retest (direct=0, pre-filled volume, working-set re-read
  pattern) could still justify per-workload writeback for specific guests,
  but not as a default.

## References

- ADR-0031 (deferral), 2026-07-02 whole-repo audit section 1 P3.
