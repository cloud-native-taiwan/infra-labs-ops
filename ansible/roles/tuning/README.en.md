# Tuning Role

[中文](README.md)

This role applies host-level performance tuning optimized for AMD NUMA architectures, NVMe storage, Ceph hyperconverged deployments, and OVN networking. All settings take effect immediately without reboot and are persisted via udev rules, tmpfiles.d, and if-up.d scripts.

## Usage

This role is included in `bootstrap.yml` and applied automatically when bootstrapping new hosts.

To apply tuning changes on existing hosts (rolling, one host at a time):

```bash
cd ansible
ansible-playbook playbooks/apply-tuning.yml
```

To verify all settings are correct (read-only, makes no changes):

```bash
cd ansible
ansible-playbook playbooks/verify-tuning.yml
```

## Sysctl Settings

Defined in `group_vars/all.yml` under `sysctl_config`, deployed by `roles/base` to `/etc/sysctl.d/99-infra-labs.conf`.

### vm.zone_reclaim_mode = 0

**Problem:** AMD CPUs have multiple NUMA nodes. By default, when a local NUMA node runs low on free memory, the kernel aggressively reclaims (evicts) cached pages from that node rather than allocating from a remote node.

**Impact:** Ceph OSD's BlueStore cache and QEMU's disk cache get repeatedly evicted and re-read from NVMe, causing random latency spikes (tail latency). Operations that should take <1ms can spike to 10-50ms.

**With mode 0:** The kernel allocates from a remote NUMA node when local is full. Remote access latency is ~100ns (vs ~80ns local), which is negligible compared to the cost of evicting and re-reading cached data.

**Applies to:** All multi-NUMA systems running Ceph or databases. Recommended by Red Hat, SUSE, and Ceph upstream documentation.

### vm.max_map_count = 524288

**Problem:** Ceph BlueStore uses `mmap()` extensively for RocksDB metadata. Each OSD process uses thousands of memory mappings.

**Default:** 65530.

**Risk:** With 4+ OSDs plus QEMU processes, the default limit can be exhausted. When this happens, `mmap()` fails and Ceph OSDs crash with a confusing "Cannot allocate memory" error even though free RAM exists.

**Setting 524288:** Provides ample headroom. This is a safety valve, not a performance setting.

### vm.overcommit_memory = 1

**Problem:** Nova's scheduler computes memory allocation ratios. QEMU processes request large memory regions upfront.

**Default (mode 0, heuristic):** The kernel uses internal heuristics to decide if there's "enough" memory, and can deny allocations even when RAM is available.

**With mode 1:** Always allow allocation requests. Trust that Nova's scheduler has already done the math. The OOM killer handles genuine exhaustion.

**Exception:** `openstack06` is a Ceph-only node (no Nova/QEMU) and keeps `vm.overcommit_memory=0` to avoid OOM-killing Ceph daemons instead of rejecting allocations. This override is in `host_vars/openstack06.yml` under `host_sysctl_config`.

### net.netfilter.nf_conntrack_max = 524288

**Problem:** OVN uses conntrack for security group rules and NAT. Every VM network connection creates a conntrack entry.

**Default:** Usually 65536.

**Risk:** Multiple VMs running web servers can generate thousands of entries. When the conntrack table fills, **new connections are silently dropped** with no error visible to the VM, just timeouts. This is one of the hardest problems to debug in OpenStack networking.

**Related timeout settings:**
- `nf_conntrack_tcp_timeout_established: 1800` (30 minutes, default 5 days is too long)
- `nf_conntrack_tcp_timeout_time_wait: 30` (30 seconds, speeds up cleanup of finished connections)

### fs.file-max = 2097152

**Problem:** A single host running 30+ OpenStack containers, Ceph OSDs, and QEMU processes can approach the default file descriptor limit (~1M) under peak load.

### fs.inotify.max_user_instances = 8192 / max_user_watches = 1048576

**Problem:** Containerized services use inotify for config file watching. Many containers require many inotify instances. The defaults (128 instances, 8192 watches) are too low for a node running 30+ OpenStack containers.

## NVMe I/O Tuning

Files: `tasks/nvme.yml`, `files/99-nvme-ceph.rules`

### I/O Scheduler: none

**Problem:** Linux's default I/O scheduler (e.g., `mq-deadline`) reorders and merges I/O requests, optimizing for spinning disks or SATA SSDs.

**Why none:** NVMe drives have their own multi-queue controller with sophisticated scheduling. The kernel scheduler just adds CPU overhead and latency on top of hardware that already handles queuing better. Setting `none` passes requests directly to the device.

### Read-ahead: 0

**Default:** 128 KB.

**Problem:** Read-ahead tells the kernel to pre-fetch data beyond what was requested, betting that the application will read sequentially. Ceph RADOS I/O is predominantly random (4K-4M objects scattered across OSDs). Pre-fetching data that won't be read wastes NVMe bandwidth and pollutes the page cache.

**Persistence:** Live settings are applied by writing to `/sys/block/nvme*/queue/`. Boot persistence is via udev rule (`/etc/udev/rules.d/99-nvme-ceph.rules`).

**Note:** If a host has no NVMe devices, the tasks are automatically skipped.

## Transparent Hugepages (THP)

Files: `tasks/thp.yml`, `files/99-thp.conf`

### enabled = madvise

**Background:** Normal memory pages are 4 KB. Hugepages are 2 MB. Using hugepages means fewer page table entries, fewer TLB misses, and faster memory access for large workloads.

**Problem with default `always`:** The kernel automatically tries to use 2 MB hugepages for all allocations. When it can't find a contiguous 2 MB region, it triggers **synchronous compaction**, moving thousands of pages to create space. On a busy hyperconverged node, this causes random multi-millisecond stalls.

**With `madvise`:** Only processes that explicitly request hugepages (via `madvise(MADV_HUGEPAGE)`) get them. Ceph and QEMU both know to request when beneficial. Everything else uses normal 4 KB pages and never triggers compaction.

### defrag = defer+madvise

**Effect:** Even for processes that request hugepages, defer compaction to a background kernel thread instead of stalling the process. Best of both worlds: hugepages when available, no stalls when they're not.

**Persistence:** Live settings are applied by writing to `/sys/kernel/mm/transparent_hugepage/`. Boot persistence is via tmpfiles.d (`/etc/tmpfiles.d/99-thp.conf`).

## NIC Ring Buffer Tuning

Files: `tasks/ring_buffers.yml`, `templates/ring-buffers.j2`

### Maximize RX/TX Ring Buffers

**Problem:** Most 25 GbE NICs ship with conservative default ring buffer sizes (typically 256-512). Under burst traffic, small ring buffers can cause packet drops.

**Approach:** Query each NIC's maximum supported RX/TX ring buffer size via `ethtool -g`, then set to max via `ethtool -G`. On mlx5_core drivers (common 25 GbE), ring buffer changes do not cause a NIC reset.

**Persistence:** Deploys `/etc/network/if-up.d/ring-buffers` script that automatically sets max ring buffers when bond slave interfaces come up. Uses `interface1` and `interface2` variables from each host's `host_vars/`.

**Error handling:** If the driver doesn't support ring buffer changes, the task logs a warning instead of failing silently.

## Ceph OSD Memory Configuration

File: `playbooks/apply-tuning.yml` (Ceph play)

### osd_memory_target = 3 GiB (constrained hosts)

**Problem:** `openstack04` and `openstack05` are 251 GiB hosts running controller/compute + Ceph (4 OSDs each). With default `osd_memory_target=4GiB`, that's 4 OSDs x 4 GiB = 16 GiB for Ceph, plus controller services, compute overhead, and OS. This causes memory pressure.

**Setting 3 GiB:** Saves 4 GiB per host (4 OSDs x 1 GiB reduction). Minimal performance impact. Hosts with 472-503 GiB (openstack01/02) keep the 4 GiB target.

**osd_memory_target_autotune = false:** cephadm defaults to autotune enabled (`autotune_memory_target_ratio=0.7`), which tries to claim 70% of host RAM for Ceph. This is not appropriate for hyperconverged nodes. Autotune is explicitly disabled before setting the manual target.

**Applied via:** `cephadm shell -- ceph config set osd/host:<hostname>`, affecting only OSDs on the specified host.

## File Structure

```
ansible/roles/tuning/
  tasks/
    main.yml            # includes sub-tasks
    nvme.yml            # NVMe I/O scheduler + readahead
    thp.yml             # Transparent Hugepages
    ring_buffers.yml    # NIC ring buffer maximization
  files/
    99-nvme-ceph.rules  # NVMe udev rule (boot persistence)
    99-thp.conf         # THP tmpfiles.d (boot persistence)
  templates/
    ring-buffers.j2     # if-up.d script template (boot persistence)
```
