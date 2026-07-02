# ADR-0027: Keep unrestricted scrub scheduling and high_client_ops mClock profile

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** CNTUG ops

## Context

HCI best practice suggests confining scrubs to an off-peak window and points
out that the `high_client_ops` mClock profile (a Quincy-era default; Tentacle
defaults to `balanced`) starves recovery. The 2026-07-02 audit raised both.
This cluster, however, has a flat low-utilization load profile with no
identifiable peak hours, and `osd_scrub_load_threshold` already defers scrubs
under real load.

## Decision

Defer both changes. Scrubs stay unrestricted (begin/end hour 0); the mClock
profile stays `high_client_ops`. During planned maintenance, rebuilds, or any
extended recovery event, the operator flips to `high_recovery_ops` for the
duration and back afterward:

    ceph config set osd osd_mclock_profile high_recovery_ops   # during event
    ceph config set osd osd_mclock_profile high_client_ops     # after

## Alternatives considered

- Off-peak scrub window (22:00-06:00): rejected for now. With no peak to
  protect, it only compresses scrub work into fewer hours and risks
  overdue-scrub warnings.
- Switch to `balanced` profile: rejected for now. Day-to-day client latency is
  the priority; recovery pressure is handled situationally as above.

## Consequences

Recovery/backfill remains deprioritized by default: after a failure, remember
the profile flip or expect slow rebuilds. Revisit both decisions if the
cluster develops a diurnal load pattern or utilization grows materially.

## References

- 2026-07-02 whole-repo/fleet audit, findings P1-4 and P1-5
- ADR-0014 (mClock profile originally FIO-validated)
