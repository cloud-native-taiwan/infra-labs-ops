# ADR-0009: Usage metering with CloudKitty

- **Status:** Accepted
- **Date:** 2026-05-27
- **Deciders:** CNTUG ops

## Context

Users had no visibility into resource cost and no incentive to right-size, and
the community funding the lab had no way to communicate the value it provides. A
metering stack was needed without standing up Ceilometer/Gnocchi.

## Decision

Deploy CloudKitty on the Prometheus collector and Prometheus fetcher, reusing
the already-deployed Prometheus and openstack-exporter. The fetcher discovers
scopes from `openstack_identity_project_info` with `scope_key=tenant_id`. The
collection period is 600s (not the 3600s default) so short-lived VMs are billed
fairly.

Storage is OpenSearch (CloudKitty v2). InfluxDB was dropped in kolla 2026.1
(community v1 EOL), and v2 storage is required for the `/v2/task/reprocesses`
re-rating API; SQLAlchemy is v1-only. OpenSearch Dashboards is enabled
internally only (`enable_opensearch_dashboards: yes`, `_external: no`) to satisfy
a first-start POST without exposing a UI nobody browses.

Rating uses a custom pyscript (`rate.py`) that keeps a Nova-backed
`instance_uuid`->flavor cache and prices by vCPU, RAM, GPU, and storage
GiB-month. It is not hashmap: prometheus-openstack-exporter emits no `flavor_id`
label, so hashmap rules keyed on `flavor_id` always rated $0. To keep identity
metrics fresh within the Prometheus lookback-delta (5m), the openstack-exporter
scrape interval is 2m (timeout 1m).

## Alternatives considered

- Ceilometer/Gnocchi collector. Rejected: an extra stack; Prometheus is already
  present.
- InfluxDB storage. Forced out: EOL and dropped in 2026.1.
- SQLAlchemy storage. Rejected: v1 only, no reprocess API.
- Hashmap rating on `flavor_id`. Rejected: the label is absent, so it rated $0.
- A lightweight custom script instead of CloudKitty. Rejected: CloudKitty
  handles edge cases and scales if the lab grows.

## Consequences

No Ceilometer to operate. Rating logic lives in a pyscript with a Nova
dependency: during a Nova outage, cached UUIDs keep rating while unknowns rate $0
with loud warnings. Re-rating is possible via the reprocess API. Metering stalls
if scope metrics go stale, mitigated by the 2m scrape.

## References

- docs/plans/2026-05-27-001-feat-monthly-usage-cost-reports-plan.md
- commits fe18a53 (deploy CloudKitty), b3d030e (influxdb->opensearch), 17ff21c
  (internal dashboards), 30192bd (hashmap->pyscript), 149b336 (2m scrape)
- kolla/globals.yml ~600-625, 829-830
- CloudKitty scope-state sharded note
