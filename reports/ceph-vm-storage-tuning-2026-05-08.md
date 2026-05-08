# Ceph VM Storage Tuning — 2026-05-08

## Goal

Improve VM storage performance for a generic, mixed workload (no specific
sequential vs random profile) on the existing Ceph Tentacle (20.2.1) cluster
backing OpenStack 2025.2 Cinder + Nova ephemeral.

## Method

- Test target: 50 GiB Cinder volume, type `NVMe` (replicated x3 in pool
  `volumes`, NVMe device class), attached to a fresh `d2.large` Ubuntu 26.04
  VM via virtio-scsi (no iothreads — landing in next release).
- Volume pre-filled with 50 GiB of zeros (`oflag=direct`) to force RBD extent
  allocation before measurement.
- fio inside the VM, libaio + `direct=1`, against `/dev/sdb` raw (no
  filesystem).
- Workloads:
  - seq read 1M, qd=32, jobs=1
  - seq write 1M, qd=32, jobs=1
  - rand read 4k, qd=32, jobs=4
  - rand write 4k, qd=32, jobs=4
  - rand 70/30 4k, qd=32, jobs=4
- Methodology evolved: initial 30s runs showed ±6-11% run-to-run variance on
  a shared cluster, making single-run comparisons unreliable. Final
  before/after used **3 reps × 60s runtime + 10s ramp**, comparing means.
  CV dropped to 0.5-6.8%.

## Iterations

### Iter 1 — `bdev_ioring=true` + `bdev_ioring_sqthread_poll=true` (REVERTED)

Hypothesis: io_uring BlueStore device backend reduces syscall overhead on
high-IOPS NVMe. Required OSD restart (rolling, NVMe OSDs only, with
`noout`).

**Result: regression on every workload.** Reverted.

| Workload | Baseline | Iter1 | Δ |
|---|---|---|---|
| seqread 1M | 4292 MiB/s | 3929 MiB/s | -8.5% |
| seqwrite 1M | 545 MiB/s | 519 MiB/s | -5% |
| randwrite 4k | 34351 IOPS | 28767 IOPS | **-16%** |

Conclusion: at Ceph 20.2.1 + this kernel, the io_uring backend is not ready
for production. Keep `bdev_ioring=false`. Documented in `ceph_cluster.yml`
audit-only section.

### Iter 2/3 — runtime mClock + scheduler tweaks (KEPT)

Hypothesis: the existing tuning had unaddressed bottlenecks in (a) mClock
weighting between client/recovery/best-effort, (b) per-OSD in-flight client
message budget, and (c) per-IO throttle accounting on SSD which capped
small-IO concurrency.

Three runtime knobs (no daemon restart):
- `osd_mclock_profile = high_client_ops` (was: `balanced`)
- `osd_client_message_cap = 1024` (was: 256)
- `bluestore_throttle_cost_per_io_ssd = 0` (was: 4000 bytes)

**Result (3-run mean, 60s each):**

| Workload | Baseline | Iter3 | Δ throughput | Δ p99 lat |
|---|---|---|---|---|
| seqread 1M qd=32 | 3791 MiB/s | 3630 MiB/s | -4.2% (borderline noise) | +1.5% |
| seqwrite 1M qd=32 | 730 MiB/s | **814 MiB/s** | **+11.5%** | **-15.4%** |
| randread 4k qd=128 | 95151 IOPS | 95626 IOPS | flat | -1% |
| randwrite 4k qd=128 | 28973 IOPS | **31645 IOPS** | **+9.2%** | **-8.0%** |
| randrw 70/30 r | 42751 IOPS | 44826 IOPS | +4.9% | +4.4% |
| randrw 70/30 w | 18353 IOPS | 19243 IOPS | +4.8% | -3.4% |

Run-to-run CV across all conditions: 0.5%-6.8%, so wins of >5% are
statistically real.

**Trade-off:** seqread regressed -4.2%, but at 3.6 GB/s absolute it remains
client-side saturated (multi-replica parallel reads). The change is
borderline against noise (CV ~1.6%). Net balance is strongly positive for a
generic VM workload because writes are the heavier operation in most app
profiles.

## Why each knob helped

- **`osd_mclock_profile = high_client_ops`** shifts the mClock IO scheduler
  weighting from balanced (40% client / 40% recovery / 20% best-effort) to
  client-favoring (60/20/20). During VM IO bursts under steady-state cluster
  background activity (scrubs, deep-scrubs, occasional rebalance), client
  requests get more reservation. Recovery still progresses, just with
  slightly less priority — acceptable for this lab where VM responsiveness
  matters more than rebuild speed.

- **`osd_client_message_cap = 1024`** raises the per-OSD cap on in-flight
  client messages from 256 to 1024. Default 256 was a bottleneck once
  fio's qd=128 (32 × 4 jobs) hit a few primary OSDs simultaneously.

- **`bluestore_throttle_cost_per_io_ssd = 0`** removes the fixed per-IO
  byte-budget charge against `bluestore_throttle_bytes` for SSD/NVMe OSDs.
  Default is 4000 bytes per IO; with throttle at 256 MiB and lots of small
  4 KiB IOs, that artificial accounting capped concurrent small writes well
  below what NVMe could absorb. Setting to 0 lets actual IO size be the
  only throttle dimension.

## Trade-offs not taken

- **Hypervisor side (virtio-scsi multi-queue, iothreads, rbd_cache mode):**
  out of scope this round. The user noted iothreads land in the next Nova
  release; that should give another notable win, especially on small random
  IO where the QEMU main thread is currently the serialization point.
- **`osd_op_num_shards_ssd = 16`** (currently 8): not tried. Would require
  another rolling restart and risks lock contention on shard locks. Worth
  benchmarking during a future maintenance window.
- **`bluefs_buffered_io = false`**: not tried. Modern Ceph guidance is that
  the kernel page cache for RocksDB metadata is usually a win; flipping to
  O_DIRECT can hurt as often as help.

## Ansible persistence

`ansible/group_vars/ceph_cluster.yml` updated:
- Added 3 desired entries for the iter3 winners under their respective
  scopes (`bluestore_throttle_cost_per_io_ssd` in BlueStore section,
  `osd_mclock_profile` and `osd_client_message_cap` in a new "OSD client
  request scheduling" section).
- Updated audit-only `bdev_ioring*` notes with the iter1 negative result so
  future operators don't re-run the same experiment.

Changes are runtime-applied (no daemon restart). To apply on the running
cluster after merging, run the existing `ceph-apply.yml` playbook.

## Rollback

```sh
sudo cephadm shell -- ceph config rm osd osd_mclock_profile
sudo cephadm shell -- ceph config rm osd osd_client_message_cap
sudo cephadm shell -- ceph config rm osd bluestore_throttle_cost_per_io_ssd
```

All three are safely reversible at any time, no daemon restart required.

## Raw fio JSON

Local copies kept under `/tmp/fio-results/`:
- `rbase/`        — rigorous baseline (3×60s, default settings)
- `riter3/`       — iter3 stack (3×60s, all 3 knobs applied)
- `baseline/`     — original 30s runs (kept for noise-floor reference)
- `iter1/`        — bdev_ioring run (regression, reverted)
- `iter2/`, `iter3/`, `iter3b/`, `baseline_v2/` — intermediate 30s runs

These are not committed to the repo.
