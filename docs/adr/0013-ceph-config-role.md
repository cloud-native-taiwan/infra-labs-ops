# ADR-0013: Ceph day-2 config management via ceph-config role

- **Status:** Accepted
- **Date:** 2026-05-01
- **Deciders:** CNTUG ops

## Context

The Ceph cluster is managed by cephadm. Day-2 configuration was applied with
ad-hoc `ceph config set` commands, which left no version-controlled record and
allowed drift between intent and live state. We needed a reproducible way to
manage cluster config, host labels, and cephadm service specs.

## Decision

Introduce a `ceph-config` Ansible role with an audit -> apply -> verify
workflow driven by structured data in `group_vars/ceph_cluster.yml`. The data
declares config entries (with section and mask matching), host labels, and
cephadm service specs (for example mon, mgr, and rgw.ty7 on port 8080).

Three playbooks separate the phases: ceph-audit, ceph-apply, and ceph-verify.
Because `ceph config get` rejects osd/host:hostname masks with EINVAL,
verification discovers per-host OSD IDs via `ceph osd ls-tree` and then queries
each OSD individually with `ceph config get osd.N`.

## Alternatives considered

- Manual cephadm/ceph config commands: rejected. Causes drift and is not
  reproducible.
- Querying host/osd masks directly in verify: rejected. Unsupported by the
  Ceph CLI and returns EINVAL.

## Consequences

Configuration becomes declarative and auditable, and service specs are codified
alongside it. Verification works within the limits of the Ceph CLI. The Ceph CLI
runs only inside the cephadm shell on openstack01.

## References

- commit 566f120 (ceph-config role)
- commit 65b7f7a (per-OSD verify syntax)
- ansible/group_vars/ceph_cluster.yml
