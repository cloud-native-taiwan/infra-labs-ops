# ADR-0026: volumes-sata-ssd stays at size 2 / min_size 1 (cache-only data)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** CNTUG ops

## Context

The 2026-07-02 fleet audit flagged `volumes-sata-ssd` as the only pool below
the Ceph-recommended size 3 / min_size 2, on the fullest device class
(sata_ssd, 3 OSDs, one per host). At min_size 1 a single OSD restart during a
degraded window can lose acknowledged writes.

## Decision

Keep size 2 / min_size 1. The pool holds only operator-owned cache volumes:
every byte is reconstructible, and losing the pool costs a re-warm, not data.
Raising to size 3 would push the class to roughly 75 percent raw utilization
with zero rebalance headroom on host failure, which is a worse operational
position than the accepted risk.

## Alternatives considered

- size 3 / min_size 2: rejected. Capacity cost on a 3-OSD class outweighs
  protection for disposable data.
- min_size 2 at size 2: rejected. Blocks cache IO whenever one OSD is down,
  trading availability for durability the data does not need.
- Migrate volumes to the NVMe pool and retire sata_ssd: viable later if the
  class shrinks or the drives age out.

## Consequences

Tenant-facing pools remain 3/2 everywhere. Nothing that is not
reconstructible may be placed on `volumes-sata-ssd`; the pool name and this
ADR are the guardrail. Revisit if the pool ever carries non-cache data.

## References

- 2026-07-02 whole-repo/fleet audit, finding P0-1 (docs/ideation, not committed)
