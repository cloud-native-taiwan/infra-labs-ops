# ADR-0031: Ceph tuning and drift-cleanup batch (2026-07)

- **Status:** Accepted (applied live 2026-07-03, end-state verified)
- **Date:** 2026-07-03
- **Deciders:** CNTUG ops

## Context

The 2026-07-02 whole-fleet audit compared live Ceph state (20.2.1 Tentacle,
17 OSDs, HEALTH_OK) against small-HCI best practices and found: severely
undersized OSD memory targets on the OSD-only node openstack06 (~1.5 GiB/OSD
on a host with ~26 GiB available), a default CRUSH rule key pointing at an
unused rule, Prometheus RBD stats configured for a nonexistent pool, the
balancer still in plain upmap despite an all-Squid client population,
uncompressed HDD pools, missing bulk flags on the two large RBD pools, and
accumulated drift (four unused CRUSH rules, a zero-weight arm01 host bucket,
an abandoned testbench pool).

## Decision

Applied live by the operator on 2026-07-03, one change at a time with health
checks between steps:

- **openstack06 OSD memory**: `mgr/cephadm/autotune_memory_target_ratio 0.7`
  with a `host:openstack06` mask (0.6 was planned; 0.7 chosen at apply time —
  OSD-only node, no control plane competing for RAM). OSDs now target ~9 GiB.
- **CPU governor**: openstack06 exposes only `performance`/`powersave`
  (intel_pstate active mode — no `schedutil`). Kept `powersave` for now;
  `host_vars/openstack06.yml` overrides `cpu_governor` so the base role's
  persistence unit does not attempt an unavailable governor.
- **Config drift**: removed `global osd_pool_default_crush_rule` (pointed at
  the since-deleted rgw data rule; default -1 now resolves to
  `replicated_rule`); `mgr/prometheus/rbd_stats_pools` trimmed to
  `vms,volumes,volumes-sata-ssd` (volumes-hdd does not exist).
- **Balancer**: `require-min-compat-client reef` + `balancer mode upmap-read`
  (eligibility verified: all daemons and clients report Squid).
- **HDD pool compression**: `lz4` / `aggressive` on `images`, `backups`,
  `default.rgw.buckets.data` (new writes only; no rewrite of existing data).
- **PG bulk**: `bulk true` on `volumes` and `images` so the autoscaler sizes
  them for their steady-state footprint (background PG-split rebalance
  expected and observed).
- **Drift cleanup (destructive)**: deleted the `testbench` pool (operator
  confirmed RBD `image01` disposable; `mon_allow_pool_delete` re-set to
  `false` afterwards), removed unused CRUSH rules
  `default.rgw.buckets.data`, `testbench`, `volumes-hdd`, `images`, and the
  zero-weight `arm01` host bucket. `replicated_rule` (rule 0) was KEPT — it
  is the default again after the config key removal.

## Alternatives considered

- Explicit per-OSD `osd_memory_target` + `_no_autotune_memory` label —
  fallback only; the host-masked autotune ratio was honored, so the
  ratio-based approach (self-adjusting if RAM or OSD count changes) won.
- `schedutil` governor on openstack06 — unavailable under intel_pstate
  active mode; switching the driver to passive mode was not worth the reboot
  for an OSD-only node. Revisit if OSD latency under load becomes an issue.
- Compressing NVMe pools too — declined; CPU cost on the latency-sensitive
  tier outweighs capacity savings on the small NVMe pools.

## Consequences

openstack06 OSDs cache effectively (~9 GiB vs ~1.5 GiB); reads spread via
upmap-read; HDD-pool writes compress; autoscaler no longer undersizes the two
big RBD pools. The CRUSH map and config now match the intent tracked in
`ansible/roles/ceph-config`. Pool deletion is disabled again. Remaining open
P3 items (librbd cache, NIC rings, availability-status monitoring) stay in
the audit report.

## References

- docs/ideation/2026-07-03-live-ceph-runbook.md (uncommitted, gitignored)
- ADR-0026 (sata_ssd cache pool accepted risk), ADR-0027 (scrub/mClock deferred)
- commit 566f120 (ceph-config role, day-2 config intent), 4d09b1a (audit
  redaction), d70a420 (mon_host sync)
