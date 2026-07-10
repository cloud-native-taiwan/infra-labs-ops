# ADR-0033: Attended Ansible rolling-reboot playbook

- **Status:** Accepted
- **Date:** 2026-07-09
- **Deciders:** CNTUG ops

## Context

Kernel CVE-2026-53359 requires every host to reboot into a patched kernel.
The previous procedure was manual: set Ceph flags, reboot one host, run
role-specific checks, unset flags, wait for VMs, then repeat. The fleet
already has machine-readable hazard metadata and the `health-gate` role from
ADR-0017, so the missing piece is orchestration, not a new state machine.

## Decision

Use an attended Ansible playbook, `ansible/playbooks/reboot.yml`, to reboot
`managed_hosts` one at a time in `canary_tier` inventory order.

Use `ceph osd set noout` and `ceph osd set norebalance` around OSD-bearing
hosts. Do not use `ceph orch host maintenance enter` yet; this fleet's
manual procedure is better exercised, and cephadm maintenance mode has extra
manager/colocation behavior to validate before trusting it on controller/OSD
hosts.

Reboot VMs in place and rely on Nova's
`resume_guests_state_on_host_boot = True`. Do not live-migrate guests during
this first version because GPU-passthrough VMs cannot migrate.

Keep pacing attended: prompt before each reboot and before continuing to the
next host. The playbook logs one summary line per host under `reports/`
(`status=skipped|rebooted|aborted`).

On any failure or operator abort, leave the Ceph flags as they are and fail
with explicit cleanup instructions instead of auto-unsetting them: if the
host is still down, unsetting `noout` would start recovery I/O on the
degraded cluster. To honor this contract, PG recovery to `active+clean` is
proven while the flags are still set; only then are the flags unset, followed
by a final wait for the pre-reboot health status (which cannot be checked
earlier because the flags themselves hold the cluster in `HEALTH_WARN`).

## Alternatives considered

- Python campaign tool / state machine: rejected for v1. The existing Ansible
  inventory, role, and hazard data already express the needed control points.
- `ceph orch host maintenance enter`: rejected for now. It needs a separate
  fleet-specific validation pass, especially around colocated mon/mgr/OSD
  hosts and the bootstrap manager host.
- Live migration: rejected. It does not work for all current guests, notably
  GPU-passthrough workloads.
- Unattended campaign: rejected. `openstack01` has a console-only bond0
  recovery path, and operators want deliberate go/no-go pacing for the first
  campaign.

## Consequences

The reboot campaign becomes resumable: hosts already on the newest installed
kernel are skipped. The play fails closed on missing health proof, missing VM
recovery, wrong kernel, bad `bond0` state/MTU, or Ceph not returning to the
pre-reboot health state with `active+clean` PGs.

The first version is intentionally operator-heavy. Future work may validate
cephadm maintenance mode or unattended pacing, but only after this playbook
has campaign history.

## References

- `ansible/playbooks/reboot.yml`
- `docs/runbooks/rolling-reboot.md`
- `docs/plans/2026-07-09-001-ops-rolling-reboot-playbook-plan.md`
- ADR-0017: health-gate
- ADR-0026: volumes-sata-ssd size 2 / min_size 1
