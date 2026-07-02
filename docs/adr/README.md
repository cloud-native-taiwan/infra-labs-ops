# Architecture Decision Records

This directory records the significant architectural and operational decisions
made for the CNTUG infra-labs OpenStack/Ceph fleet. Each ADR captures **what**
was decided and **why**, plus the alternatives weighed and the consequences we
live with.

These were reconstructed from the repository's plans, runbooks, commit history,
and code comments. Pure bug fixes and mechanical follow-ups are intentionally
omitted; only decisions with a real trade-off are recorded here.

## Format

Lightweight [MADR](https://adr.github.io/madr/). Each record has: Status,
Context, Decision, Alternatives considered, Consequences, References. Status is
one of `Accepted`, `Superseded by ADR-N`, or `Reverted`.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-iac-baseline-ansible-kolla.md) | Infrastructure-as-code baseline: unified Ansible + Kolla-Ansible | Accepted |
| [0002](0002-per-host-inventory-host-vars.md) | Per-host inventory via `host_vars/` with data-driven hazards | Accepted |
| [0003](0003-tools-convention.md) | `tools/` convention for deploy-host operational services | Accepted |
| [0004](0004-tool-images-ci-ghcr.md) | Build tool images in CI to GHCR; deploy host pulls | Accepted |
| [0005](0005-public-repo-leak-prevention.md) | Layered secret-leak prevention for the public repo | Accepted |
| [0006](0006-rabbitmq-messaging-topology.md) | RabbitMQ messaging topology and partition handling | Accepted |
| [0007](0007-octavia-ovn-only.md) | Octavia OVN-only provider (drop amphora) | Accepted |
| [0008](0008-neutron-ovn-fip.md) | Neutron OVN distributed FIPs + custom patched Kolla images | Accepted |
| [0009](0009-cloudkitty-metering.md) | Usage metering with CloudKitty (Prometheus + OpenSearch + pyscript) | Accepted |
| [0010](0010-showback-rate-card.md) | Budget-anchored showback rate card | Accepted |
| [0011](0011-openstack-2026.1-upgrade.md) | OpenStack 2026.1 upgrade strategy | Accepted |
| [0012](0012-host-tuning-role.md) | Host performance tuning role | Accepted |
| [0013](0013-ceph-config-role.md) | Ceph day-2 config management via `ceph-config` role | Accepted |
| [0014](0014-ceph-osd-tuning.md) | Ceph OSD tuning validated by FIO | Accepted |
| [0015](0015-glance-rbd-thin-cow.md) | Glance RBD thin provisioning + CoW image locations | Accepted |
| [0016](0016-numa-bios-remediation.md) | openstack04 NUMA BIOS remediation | Accepted |
| [0017](0017-health-gate.md) | Fail-closed health-gate pre-flight for disruptive ops | Accepted |
| [0018](0018-period-job-integrity.md) | Period-job integrity contract for scheduled jobs | Accepted |
| [0019](0019-tls-cert-renewal.md) | Automated TLS certificate renewal | Accepted |
| [0020](0020-mariadb-backup.md) | MariaDB backup via kolla-ansible from deploy host | Accepted |
| [0021](0021-account-lifecycle-automation.md) | Account lifecycle automation and deletion safety | Accepted |
| [0022](0022-rgw-admin-object-cleanup.md) | Object-storage cleanup via RGW Admin REST API | Accepted |
| [0023](0023-control-plane-alerting.md) | Control-plane alerting mirroring health-gate predicates | Accepted |
| [0024](0024-cryptominer-detection.md) | Crypto-miner detection via CPU-saturation alert | Accepted |
| [0025](0025-nova-vtpm.md) | Nova vTPM for Windows 11 guests | Reverted |
| [0026](0026-sata-ssd-cache-pool-replication.md) | volumes-sata-ssd stays at size 2 / min_size 1 (cache-only data) | Accepted |
| [0027](0027-scrub-mclock-defaults-deferred.md) | Keep unrestricted scrub scheduling and high_client_ops mClock profile | Accepted |
| [0028](0028-keystone-totp-archived.md) | keystone-totp archived; TOTP enforcement deferred to upstream | Accepted |
| [0029](0029-initial-credential-delivery.md) | Initial credential delivery for tenant accounts | Accepted |
| [0030](0030-infra-labs-common-shared-library.md) | Shared `infra_labs_common` library, vendored into tool images | Accepted |
