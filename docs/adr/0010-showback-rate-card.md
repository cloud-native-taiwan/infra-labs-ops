# ADR-0010: Budget-anchored showback rate card

- **Status:** Accepted
- **Date:** 2026-06-01
- **Deciders:** CNTUG ops

## Context

Placeholder rates blocked accurate cost reporting. The lab runs heterogeneous
hardware (multiple CPU generations plus GPUs), so a flat public-cloud percentage
would be wrong, and the lab offers no uptime SLA and no storage QoS.

## Decision

Use a custom rate card anchored at roughly 65% of DigitalOcean/Vultr budget-tier
pricing:

- $0.006 / vCPU-hr
- $0.002 / GB-RAM-hr
- $0.04 / GiB-month storage
- per-hour GPU adders by GPU type

Network is not metered. `setup_hashmap.sh` auto-derives per-flavor costs to keep
the rates DRY. This is a showback model intended to communicate value, not a
cost-recovery model.

## Alternatives considered

- A public-cloud pricing formula or flat percentage. Rejected: heterogeneous CPU
  generations and GPUs make a single percentage wrong.
- Cost-recovery pricing. Rejected: this is free community infrastructure and the
  intent is showback.

## Consequences

Rates map directly to hardware tiers, and below-market pricing reflects the
no-SLA, no-QoS reality. Rate changes require re-rating historical data via the
reprocess API, and rates must be set before collection begins.

## References

- commit d91a84a (budget-anchored rate card)
- docs/runbooks/cloudkitty-rate-card.md
