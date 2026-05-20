# Documentation Index

[中文](README.md)

This file is the entry point to every piece of documentation in the repo. New operators should start here.

## Required reading for newcomers

| Doc | Purpose |
|---|---|
| [`quickstart.en.md`](quickstart.en.md) | Zero-to-first-dry-run in 10-20 minutes (copy-pasteable commands) |
| [`fleet-topology.en.md`](fleet-topology.en.md) | Which host runs what, group mapping, network naming, software versions |
| [`glossary.en.md`](glossary.en.md) | Inventory groups and OpenStack/Ceph/Kolla/network terminology |
| [`troubleshooting.en.md`](troubleshooting.en.md) | Common problems and resolutions for new operators |

## Quick Jump

| If you want to... | Start here |
|---|---|
| Operate this repo for the first time | [`quickstart.en.md`](quickstart.en.md) |
| Apply an Ansible change to the fleet | [`ansible/README.en.md`](../ansible/README.en.md) |
| Manage OpenStack accounts | [`tools/account_automation/README.en.md`](../tools/account_automation/README.en.md) |
| Tune Ceph day-2 settings | [`ansible/roles/ceph-config/README.en.md`](../ansible/roles/ceph-config/README.en.md) |
| Tune host performance | [`ansible/roles/tuning/README.en.md`](../ansible/roles/tuning/README.en.md) |
| Recover from OVN floating IP drift | [`ovn-floating-ip-runbook.md`](ovn-floating-ip-runbook.md) |
| Build a custom Neutron container image | [`neutron-kolla-custom-image-guide.md`](neutron-kolla-custom-image-guide.md) |

## Sub-component READMEs

Every sub-component has its own README with a quickstart and hands-on commands:

| Sub-component | What it covers | README |
|---|---|---|
| Root | Repo overview, prerequisites, apply workflow | [README.md](../README.md) / [README.en.md](../README.en.md) |
| `ansible/` | Playbook list, role layout, inventory conventions | [README.md](../ansible/README.md) / [README.en.md](../ansible/README.en.md) |
| `ansible/roles/tuning/` | sysctl, NVMe scheduler, THP, NIC ring buffer | [README.md](../ansible/roles/tuning/README.md) / [README.en.md](../ansible/roles/tuning/README.en.md) |
| `ansible/roles/ceph-config/` | Ceph day-2 IaC (audit / apply / verify) | [README.md](../ansible/roles/ceph-config/README.md) / [README.en.md](../ansible/roles/ceph-config/README.en.md) |
| `tools/` | Tool layout, deploy convention, secrets path | [README.md](../tools/README.md) / [README.en.md](../tools/README.en.md) |
| `tools/account_automation/` | OpenStack account lifecycle automation | [README.md](../tools/account_automation/README.md) / [README.en.md](../tools/account_automation/README.en.md) |

Ansible roles without a dedicated README (`base`, `bbr`, `ceph-bootstrap`, `grub`, `kvm`, `mail`, `network`, `openstack05_battlemage`, `swap`) are documented through their `roles/<name>/tasks/main.yml` and the corresponding sections of the root README.

## By Purpose

### Operational runbooks and investigations

Field notes for things that have actually happened, useful when they happen again.

- [`ovn-floating-ip-runbook.md`](ovn-floating-ip-runbook.md) -- Detect and recover from OVN floating IP drift.
- [`openstack04-numa-oom.md`](openstack04-numa-oom.md) -- Investigation log for the openstack04 NUMA OOM incident.
- [`neutron-kolla-custom-image-guide.md`](neutron-kolla-custom-image-guide.md) -- Build and deploy a custom Neutron container image.

### User-facing announcements

Drafts of user-visible announcements live in `announcements/`.

- [`announcements/2026-05-06-aup-update-and-service-info.md`](announcements/2026-05-06-aup-update-and-service-info.md)

### Post-mortems / tuning reports

Tuning and incident analysis reports live under `reports/`.

- [`../reports/ceph-vm-storage-tuning-2026-05-08.md`](../reports/ceph-vm-storage-tuning-2026-05-08.md) -- Full FIO benchmark and conclusions for Ceph VM storage tuning.

## Documentation conventions

- Chinese primary, English secondary. Repo-scoped docs use `README.md` (Chinese) + `README.en.md` (English).
- Filename date prefix: `YYYY-MM-DD-`. Plan-class documents add a serial (e.g. `2026-05-07-001-...`).
- Documents under `announcements/` and `reports/` are point-in-time snapshots. **Do not rewrite the originals after completion** -- add a new dated file instead.
