# ADR-0002: Per-host inventory via host_vars/ with data-driven hazards and canary tiers

- **Status:** Accepted
- **Date:** 2026-03-26
- **Deciders:** CNTUG ops

## Context
NIC interface names drifted between inventory and live hosts (openstack04 inventory `enp2` vs live `ens1`; openstack05 on-disk `enp3` vs runtime `ens1`). GRUB parameters were scattered across playbooks through fragile `lineinfile` regex. Per-host facts were mixed with group membership in a flat `host.yml`. Some hosts carry known risks (the bond0 boot failure on openstack01) that should block unattended disruptions.

## Decision
Introduce a `host_vars/` directory with one YAML file per host.

- Each file carries `node_num`, `interface1`/`interface2`, `grub_cmdline_*` lists, and a structured `host_hazards` list (`key`, `reason`, `source`, `blocks_unattended`, `canary_tier`).
- `host.yml` is reduced to group membership only.
- The GRUB role templates the entire `/etc/default/grub` from host_vars (idempotent, no regex).
- Hazards are consulted by the health-gate and disruptive playbooks; `canary_tier` orders rolling ops (openstack01 = tier 90, disrupted last, to keep the Ceph bootstrap/control surface alive longest).

## Alternatives considered
- Keep inlined host_vars in `host.yml` — rejected; NIC drift, no override mechanism.
- host_vars alongside `host.yml` for selective overrides — rejected; multiplies confusion.
- Reimplement hazard knowledge per playbook — rejected; stable fleet knowledge belongs in data.

## Consequences
Per-host facts have a clear home and hazards become reusable stable data. `.gitignore` can hide sensitive per-host IPs while topology stays in the repo. Ceph-group renaming is deferred until confirmed bootstrap-only.

## References
- docs/LIVE_HOST_SYNC_PLAN.md
- ansible/host_vars/openstack01.yml (host_hazards, canary_tier)
- ansible/roles/health-gate/tasks/hazards.yml
