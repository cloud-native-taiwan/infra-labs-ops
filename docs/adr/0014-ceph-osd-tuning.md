# ADR-0014: Ceph OSD tuning validated by FIO

- **Status:** Accepted
- **Date:** 2026-05-08
- **Deciders:** CNTUG ops

## Context

VM storage performance (RBD/Cinder on NVMe) needed improvement. Any tuning had
to be evidence-based rather than guesswork, so every knob was measured before
adoption.

## Decision

Apply OSD knobs validated by 3x60s FIO runs (coefficient of variation
0.6 to 2.4 percent). The iter3 set: `bluestore_throttle_cost_per_io_ssd=0`,
`osd_mclock_profile=high_client_ops`, and `osd_client_message_cap=1024`. These
produced +9 percent randwrite, +12 percent seqwrite, and -8 to -15 percent
write p99. Additionally, raise the BlueStore write throttle
`bluestore_throttle_bytes` and `bluestore_throttle_deferred_bytes` to 256 MiB.
The io_uring backend is rejected because it regressed performance (-16 percent
randwrite, -5 percent seqwrite). The cluster stays HEALTH_OK after apply.

## Alternatives considered

- io_uring store backend: rejected. Measured regression.
- Untested or default throttles: rejected. FIO showed headroom.
- Tuning by intuition: rejected. Each knob is FIO-validated with a documented
  rollback.

## Consequences

Measured write gains with reduced tail latency and no read regression. The
methodology and results are recorded for repeatability. The knobs are applied
through the ceph-config role (ADR-0013).

## References

- commit 5864670 (iter3 OSD tuning)
- commit ffc8355 (256 MiB write throttle)
- commit 6a12fe2 (2026-05-08 storage tuning report)
