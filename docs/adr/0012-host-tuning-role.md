# ADR-0012: Host performance tuning role for 25GbE hyperconverged nodes

- **Status:** Accepted
- **Date:** 2026-04-01
- **Deciders:** CNTUG ops

## Context

Hyperconverged OpenStack and Ceph hosts showed memory pressure (openstack04
swapping, high PSI), network packet misses, and kernel-parameter drift. Tunables
were scattered across manual steps with no single source of truth.

## Decision

Centralize system tuning in a `tuning` role, applied rolling (`serial: 1`) with a
read-only `verify-tuning.yml` companion.

- Storage: NVMe I/O scheduler `none`, readahead 0, THP `madvise`; persisted via
  udev/tmpfiles/if-up.
- Network (25GbE): NIC ring buffers maximized per interface (query max, set it,
  warn if unsupported), `net.core.rmem_max`/`wmem_max` 64 MiB,
  `tcp_rmem`/`tcp_wmem` max 64 MiB (sized for 25 Gbps x ~1 ms RTT), `somaxconn`
  32768, `netdev_max_backlog` 50000, `tcp_tw_reuse` fixed, `ip_local_port_range`
  and reserved ports, `tcp_fin_timeout` 15, neighbor `gc_thresh` per IPv4/IPv6.
- Memory/NUMA: `vm.swappiness` 10, `vm.zone_reclaim_mode` 0 (avoid AMD NUMA
  reclaim stalls starving Ceph OSDs), `vm.max_map_count` 524288 (BlueStore mmap
  plus many QEMU VMs), per-host `vm.min_free_kbytes`.
- conntrack: `nf_conntrack_max` 524288, module pre-loaded via modules-load.d
  (kernel 6.12+ auto-sizes the hashsize, so manual config removed).
- zswap on memory-constrained nodes (openstack01: enabled, max_pool_percent=25,
  zstd).

Canary soak on the safest host (openstack06) before fleet rollout, with a 48h
watch on the riskiest (openstack04).

## Alternatives considered

- Manual per-host tuning. Rejected: drift and not reproducible.
- Manual conntrack hashsize. Rejected: kernel 6.12+ auto-sizes it.
- Ring buffers larger than NIC max. Impossible: already at hardware max.
- Broader scope (huge pages, dirty ratios). Deferred.

## Consequences

Reproducible, verifiable tuning; rolling apply limits blast radius. Some values
remain per-host (`min_free_kbytes`, zswap). Physical-world calibration (NIC max
query, per-host memory) is kept as explicit knobs.

## References

- docs/TODOS.md
- commit 978632a (host tuning role + sysctl)
- ansible/group_vars/all.yml, ansible/roles/tuning/*,
  ansible/host_vars/openstack01.yml
