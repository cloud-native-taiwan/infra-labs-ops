# ADR-0015: Glance RBD thin provisioning and CoW image locations

- **Status:** Accepted
- **Date:** 2026-05-01
- **Deciders:** CNTUG ops

## Context

Shelve and snapshot uploads pushed the full virtual disk size, taking hours for
sparse images. Every boot, snapshot, and shelve also ran a full qemu-img convert
plus upload, costing minutes for GPU instances. The slow-shelve-upload
investigation traced the compounding causes: thin_provisioning off, single
upload stream, HDD WAL, 3x replication, and NVMe/HDD CoW blocking.

## Decision

1. Set `rbd_thin_provisioning=true` on the `[rbd]` multistore backend so
   all-zero chunks are skipped (Ceph returns zeros for unwritten objects). This
   is roughly 6x less write for a 200 GiB disk holding 32 GiB of real data.
2. Set `show_image_direct_url` and `show_multiple_locations` so Nova and Cinder
   can copy-on-write clone raw Glance images within the same Ceph cluster
   instead of downloading and flattening.

## Alternatives considered

- Keep full-size uploads: rejected. Hours-long shelves.
- qemu-img convert each time: rejected. CoW clone avoids it.

CoW requires raw images co-located in the same Ceph cluster. Exposing image
locations carries a mild trust implication, accepted for this single-cluster lab.

## Consequences

Far faster shelve, snapshot, and boot for sparse and GPU images. CoW only helps
raw images co-located in Ceph. Direct-URL exposure is accepted within the
trusted single cluster.

## References

- commit 2e7290e (rbd_thin_provisioning)
- commit 10c37c9 (RBD image locations for CoW)
- Glance shelve slow-upload investigation note
