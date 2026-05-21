# Ansible

[中文](README.md)

This directory is the entry point for the Infra Labs Ansible configuration. All `ansible-playbook` commands run from `ansible/`, or set `ANSIBLE_CONFIG` to [`ansible.cfg`](ansible.cfg).

> Repo-level installation, validation, and apply workflows are documented in the [Root README](../README.en.md). This file is just an in-directory navigator.

## Layout

| Path | What it holds |
|---|---|
| [`hosts`](hosts) | Inventory: group membership and `ansible_host` |
| [`group_vars/`](group_vars) | Group variables (`all.yml`, `ceph_cluster.yml`, ...) |
| [`host_vars/`](host_vars) | Per-host variables (`node_num`, `interface1/2`, GRUB lists, ...) |
| [`playbooks/`](playbooks) | All playbooks (see list below) |
| [`roles/`](roles) | Ansible roles (see list below) |
| [`scripts/`](scripts) | `validate.sh` (yamllint / ansible-lint / syntax / inventory / template rendering) |
| [`tests/`](tests) | Python tests for inventory and templates |
| `collections/` | Pinned Ansible collections |
| `private/` | Sensitive files (`authorized_keys`, `passwd.client`, `tools/<name>/`), **not in git** |
| [`ansible.cfg`](ansible.cfg) | Defaults for inventory path, role path, etc. |

## Playbooks

| Playbook | Target | Purpose |
|---|---|---|
| [`bootstrap.yml`](playbooks/bootstrap.yml) | `managed_hosts`, `ceph_bootstrap` | Main entrypoint. Applies base / network / mail / kvm / grub / swap / bbr / tuning, plus cephadm prep |
| [`apply-tuning.yml`](playbooks/apply-tuning.yml) | `managed_hosts` (rolling, serial: 1) | Apply sysctl and host tuning only, without the full bootstrap |
| [`verify-tuning.yml`](playbooks/verify-tuning.yml) | `managed_hosts` | Read-only verification of tuning settings |
| [`bbr.yml`](playbooks/bbr.yml) | `managed_hosts` | Apply BBR settings standalone |
| [`swap.yml`](playbooks/swap.yml) | `managed_hosts` | Manage swapfile standalone |
| [`pci-pass.yml`](playbooks/pci-pass.yml) | `pci_pass` | Apply GRUB settings for PCI passthrough hosts |
| [`gpu-monitor.yml`](playbooks/gpu-monitor.yml) | `openstack04` | Deploy GPU temperature monitor script + systemd service |
| [`exporter.yml`](playbooks/exporter.yml) | `managed_hosts` | Deploy IPMI exporter container (Prometheus) |
| [`upgrade.yml`](playbooks/upgrade.yml) | `managed_hosts` (rolling, serial: 1) | apt dist-upgrade and autoremove |
| [`ceph-audit.yml`](playbooks/ceph-audit.yml) | `ceph_bootstrap` | Read-only audit of Ceph config vs. desired state |
| [`ceph-apply.yml`](playbooks/ceph-apply.yml) | `ceph_bootstrap` (requires `-e ceph_iac_apply=true`) | Apply Ceph day-2 config, with a pre-apply snapshot |
| [`ceph-verify.yml`](playbooks/ceph-verify.yml) | `ceph_bootstrap` | Verify Ceph live state matches desired |
| [`setup-mariadb-backup.yml`](playbooks/setup-mariadb-backup.yml) | First controller | systemd timer: daily 02:00 full, hourly :30 incremental |
| [`setup-cert-renewal.yml`](playbooks/setup-cert-renewal.yml) | deploy host | certbot + Cloudflare DNS-01 auto-renewal timer (00:00 and 12:00 daily) |
| [`deploy-haproxy.yml`](playbooks/deploy-haproxy.yml) | `deploy_host` | Deploy HAProxy edge reverse proxy; does not manage or remove NGINX |
| [`deploy-account-automation.yml`](playbooks/deploy-account-automation.yml) | `deploy_host` | Deploy the `tools/account_automation` container |

## Roles

| Role | What it does | Docs |
|---|---|---|
| [`base`](roles/base) | Base packages, sysctl, SSH authorized_keys, chrony | `tasks/main.yml` |
| [`network`](roles/network) | bond, VLAN, resolver; rolling restart, one host at a time (15s pause) | `tasks/main.yml` |
| [`mail`](roles/mail) | exim4 relay, `passwd.client` | `tasks/main.yml` |
| [`kvm`](roles/kvm) | KVM nested virtualization | `tasks/main.yml` |
| [`grub`](roles/grub) | GRUB kernel command line (incl. PCI passthrough) | `tasks/main.yml` |
| [`swap`](roles/swap) | swapfile management | `tasks/main.yml` |
| [`bbr`](roles/bbr) | BBR congestion control | `tasks/main.yml` |
| [`tuning`](roles/tuning) | NVMe scheduler, THP, NIC ring buffer | [README](roles/tuning/README.md) / [en](roles/tuning/README.en.md) |
| [`openstack05_battlemage`](roles/openstack05_battlemage) | openstack05-only GRUB flags, SR-IOV restore unit | `tasks/main.yml` |
| [`ceph-bootstrap`](roles/ceph-bootstrap) | Ceph apt repo (bookworm suite, tentacle release), cephadm install | `tasks/main.yml` |
| [`ceph-config`](roles/ceph-config) | Ceph day-2 config IaC (audit / apply / verify) | [README](roles/ceph-config/README.md) / [en](roles/ceph-config/README.en.md) |
| [`haproxy`](roles/haproxy) | HAProxy edge proxy, TLS PEM bundle, Harbor routing | [README](roles/haproxy/README.md) |

## Hands-on commands

Run static validation before any change:

```bash
./scripts/validate.sh
```

Dry-run against a single host:

```bash
ansible-playbook playbooks/bootstrap.yml --check --diff --limit openstack01
```

Dry-run against the whole stable fleet:

```bash
ansible-playbook playbooks/bootstrap.yml --check --diff --limit managed_hosts
```

Apply for real (rolling is the repo default):

```bash
ansible-playbook playbooks/bootstrap.yml --limit openstack01
```

Ceph day-2 audit / apply / verify:

```bash
ansible-playbook playbooks/ceph-audit.yml  --limit ceph_bootstrap
ansible-playbook playbooks/ceph-apply.yml  --limit ceph_bootstrap -e ceph_iac_apply=true
ansible-playbook playbooks/ceph-verify.yml --limit ceph_bootstrap
```

Deploy the HAProxy edge proxy:

```bash
ansible-playbook playbooks/deploy-haproxy.yml --check --diff --limit deploy01
ansible-playbook playbooks/deploy-haproxy.yml --limit deploy01
```

## Inventory conventions

- [`hosts`](hosts) only defines group membership and `ansible_host`. No per-host facts here.
- Per-host facts go in [`host_vars/<hostname>.yml`](host_vars). A new host needs at least `node_num`, `interface1`, `interface2`, plus GRUB lists if managed by the GRUB role.
- Shared defaults live in [`group_vars/all.yml`](group_vars/all.yml); Ceph-specific defaults in [`group_vars/ceph_cluster.yml`](group_vars/ceph_cluster.yml).
- `ansible/hosts` and [`../kolla/multinode`](../kolla/multinode) reference the same physical hosts. Update both when adding or removing a host (the [`tests/`](tests) suite validates this).

## Secrets

All sensitive files live under `ansible/private/`, excluded from git.

To run `bootstrap.yml` (and other fleet-bootstrap-class playbooks):

```
ansible/private/
  authorized_keys             # roles/base writes to /home/debian/.ssh/authorized_keys
  passwd.client               # roles/mail writes to /etc/exim4/passwd.client
```

Only required when running the corresponding deploy playbook:

```
# deploy-account-automation.yml
ansible/private/tools/account_automation/
  .env
  service-account.json
  clouds.yaml
```

> Check each tool's README for the authoritative list of required secrets.

## Further reading

- Full apply workflow, the bookworm-on-trixie Cephadm rationale, first-time host setup: [Root README](../README.en.md)
- Why each tuning sysctl exists: [`roles/tuning/README.en.md`](roles/tuning/README.en.md)
- The audit-apply-verify workflow for Ceph day-2 config: [`roles/ceph-config/README.en.md`](roles/ceph-config/README.en.md)
- Full documentation index: [`docs/README.en.md`](../docs/README.en.md)
