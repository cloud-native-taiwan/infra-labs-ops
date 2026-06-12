# Infra Labs Ops

[中文](README.md)

This repository manages host configuration and deployment settings for the Infra Labs fleet. Ansible inventory, playbooks, and roles live in [`ansible/`](ansible), while Kolla-Ansible deployment configuration lives in [`kolla/`](kolla). Passwords (`kolla/passwords.yml`) are Ansible Vault-encrypted and tracked in git; other sensitive files (private keys, certificates) are excluded via `.gitignore`.

This is a public repository: every push is scanned by gitleaks in CI ([`.github/workflows/gitleaks.yml`](.github/workflows/gitleaks.yml)) and GitHub secret scanning with push protection is enabled. Contributors are encouraged to install the local hook with `pre-commit install` so leaks are blocked before they ever reach a commit.

## Documentation map

- **New to this repo?** Start with [`docs/quickstart.en.md`](docs/quickstart.en.md) (10-20 min, copy-pasteable commands).
- **Hit a problem?** [`docs/troubleshooting.en.md`](docs/troubleshooting.en.md)
- **Which host runs what?** [`docs/fleet-topology.en.md`](docs/fleet-topology.en.md)
- **Unfamiliar term?** [`docs/glossary.en.md`](docs/glossary.en.md)
- **Looking for a specific document?** Full index: [`docs/README.en.md`](docs/README.en.md).

| If you want to... | Start here |
|---|---|
| Run or change Ansible (playbooks, roles, inventory) | [`ansible/README.en.md`](ansible/README.en.md) |
| Operate OpenStack account lifecycle | [`tools/account_automation/README.en.md`](tools/account_automation/README.en.md) |
| Tune host performance (sysctl / NVMe / THP / NIC) | [`ansible/roles/tuning/README.en.md`](ansible/roles/tuning/README.en.md) |
| Tune Ceph day-2 configuration | [`ansible/roles/ceph-config/README.en.md`](ansible/roles/ceph-config/README.en.md) |
| Recover from OVN floating IP drift | [`docs/ovn-floating-ip-runbook.md`](docs/ovn-floating-ip-runbook.md) |
| See all plans, requirements, reports, runbooks | [`docs/README.en.md`](docs/README.en.md) |

## Repo Layout

```
├── ansible/                 # Ansible host configuration
│   ├── hosts                # inventory (group membership only)
│   ├── host_vars/           # per-host facts and host-specific policy
│   ├── group_vars/all.yml   # shared defaults
│   ├── playbooks/           # all Ansible playbooks
│   ├── roles/               # focused Ansible roles
│   ├── scripts/             # validation and utility scripts
│   ├── tests/               # test suite
│   ├── collections/         # Ansible collections
│   └── private/             # sensitive files (not in git)
├── tools/                   # Deploy-host operational tools (independently built)
│   └── account_automation/  # OpenStack account lifecycle automation
├── kolla/                   # Kolla-Ansible deployment configuration
├── docs/                    # documentation (entry: docs/README.en.md)
├── reports/                 # Post-mortem and tuning reports
├── README.md
└── README.en.md
```

## What This Repo Does

This repo manages baseline host configuration for the Infra Labs fleet:

- base packages and sysctl tuning
- bond and resolver configuration
- mail relay configuration
- KVM nested virtualization settings
- GRUB kernel command line management
- BBR tuning
- host tuning (NVMe I/O scheduler, Transparent Hugepages, NIC ring buffers)
- Ceph bootstrap host setup
- host-specific Battlemage settings for `openstack05`
- Ceph day-2 configuration management (audit-apply-verify workflow)
- MariaDB backup scheduling
- TLS certificate auto-renewal (certbot + Cloudflare DNS-01)

The main entrypoint is [`ansible/playbooks/bootstrap.yml`](ansible/playbooks/bootstrap.yml), which applies focused roles to the inventory groups defined in [`ansible/hosts`](ansible/hosts).

## Workstation Prerequisites

You need a control machine with:

- Python 3
- SSH access to the target hosts as `debian`
- sudo access on the target hosts for that SSH user
- Ansible tooling available locally

The validation workflow in this repo assumes a local virtualenv at `.venv/`. If it does not exist yet:

```bash
python3 -m venv .venv
.venv/bin/pip install ansible-core ansible-lint yamllint jinja2 pyyaml
```

## Private Inputs

Create these local files before running bootstrap-style playbooks:

- `ansible/private/authorized_keys`
- `ansible/private/passwd.client`

`roles/base` copies `private/authorized_keys` to `/home/debian/.ssh/authorized_keys`.
`roles/mail` copies `private/passwd.client` to `/etc/exim4/passwd.client`.

```text
ansible/private/
  authorized_keys
  passwd.client
```

## Inventory Model

This repo uses:

- group membership in [`ansible/hosts`](ansible/hosts)
- per-host variables in [`ansible/host_vars/`](ansible/host_vars)

Important groups:

- `managed_hosts`: steady-state x86 fleet
- `temporary`: non-steady-state hosts like `arm01`
- `controller`: Kolla controller nodes
- `compute`: compute nodes
- `ceph_bootstrap`: the initial Ceph bootstrap host
- `ceph_cluster`: hosts participating in the Ceph cluster
- `pci_pass`: hosts that need VFIO-related boot/module handling

## Cephadm Packaging Note

[`ansible/roles/ceph-bootstrap`](ansible/roles/ceph-bootstrap) currently uses Ceph's upstream `bookworm` apt repository on Debian 13 `trixie` hosts:

- repo release: `tentacle`
- package suite: `bookworm`
- target package version: `20.2.0`

This is intentional. Upstream Ceph's Debian repository currently does not publish a `trixie` suite, but the `bookworm` cephadm package path is the operator-verified path used here to bootstrap and manage Tentacle clusters on Debian 13. Revisit this once Ceph publishes a native `trixie` suite.

## Validation

Run local static validation before touching hosts:

```bash
./ansible/scripts/validate.sh
```

This runs:

- `yamllint`
- `ansible-lint`
- playbook syntax checks
- inventory target validation
- template rendering checks

## Dry Run Against Real Hosts

Yes, Ansible dry-run is possible against the real hosts. All `ansible-playbook` commands should be run from the `ansible/` directory.

Example, single host:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff --limit openstack01
```

Example, steady-state fleet:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff --limit managed_hosts
```

Notes:

- `--check` still connects to the live hosts and gathers facts
- `--diff` is useful for rendered files such as GRUB, network, and mail config
- command-driven tasks are less informative in check mode than declarative tasks
- local secret inputs under `ansible/private/` must exist before dry-run or apply
- network restarts are throttled one host at a time with a 15 second pause between hosts

For narrower changes, target individual playbooks:

```bash
cd ansible
ansible-playbook playbooks/gpu-monitor.yml --check --diff --limit openstack04
ansible-playbook playbooks/swap.yml --check --diff --limit openstack05
ansible-playbook playbooks/exporter.yml --check --diff --limit managed_hosts
```

## Recommended Apply Procedure

For routine changes:

1. Update inventory, host vars, role code, or templates.
2. Run `./ansible/scripts/validate.sh`.
3. From `ansible/`, run `ansible-playbook playbooks/<playbook> --check --diff --limit <host-or-group>`.
4. Review the proposed file diffs and changed tasks.
5. Apply for real without `--check` once the dry-run is clean.

When `bond0` changes, the network role restarts networking one host at a time and waits 15 seconds before the next host. This is intended to reduce avoidable disruption to Ceph and other clustered services.

Example:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff --limit openstack06
ansible-playbook playbooks/bootstrap.yml --limit openstack06
```

## First-Time Host Setup

This repo assumes:

- the host is reachable by SSH
- the `debian` user exists or is otherwise available for Ansible access
- that user has passwordless sudo
- the operator has decided which inventory group(s) the host belongs to

For a new steady-state host:

1. Add the hostname to [`ansible/hosts`](ansible/hosts).
2. Create [`ansible/host_vars/<hostname>.yml`](ansible/host_vars) with at least:
   - `node_num`
   - `interface1`
   - `interface2`
   - GRUB lists if the host is managed by the GRUB role
3. Confirm SSH and sudo access manually.
4. Run `./ansible/scripts/validate.sh`.
5. From `ansible/`, dry-run `playbooks/bootstrap.yml` against that host.
6. Apply `playbooks/bootstrap.yml` for real.

## Ceph Bootstrap Procedure

This repo's Ceph role prepares the `ceph_bootstrap` host with the upstream Ceph apt repository and installs `cephadm`.

Typical flow:

1. Bootstrap the host baseline:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --limit ceph_bootstrap
```

2. Confirm the host-side tool is present:

```bash
ssh debian@<bootstrap-host> 'which cephadm && cephadm version'
```

3. Run the actual Ceph cluster bootstrap on the bootstrap host:

```bash
ssh debian@<bootstrap-host> 'sudo cephadm bootstrap --mon-ip <mon-ip>'
```

4. Add the remaining cluster hosts through `cephadm` after bootstrap.

This repo does not fully automate the cluster bootstrap command itself yet. It prepares the host so that step can be run intentionally with the correct monitor IP and cluster-specific options.

## Host Tuning

The [`tuning`](ansible/roles/tuning) role (included in `bootstrap.yml`) applies NVMe I/O scheduling, Transparent Hugepage settings, and NIC ring buffer tuning. See [`ansible/roles/tuning/README.en.md`](ansible/roles/tuning/README.en.md) for detailed explanations of each setting.

```bash
cd ansible
ansible-playbook playbooks/apply-tuning.yml    # apply (rolling, serial: 1)
ansible-playbook playbooks/verify-tuning.yml   # verify (read-only)
```

## Ceph Configuration Management

The [`ceph-config`](ansible/roles/ceph-config) role manages day-2 Ceph configuration (config entries, cephadm labels, service specs) through an audit-apply-verify workflow. See [`ansible/roles/ceph-config/README.en.md`](ansible/roles/ceph-config/README.en.md) for detailed documentation.

```bash
cd ansible
ansible-playbook playbooks/ceph-audit.yml --limit ceph_bootstrap     # audit drift (read-only)
ansible-playbook playbooks/ceph-apply.yml --limit ceph_bootstrap -e ceph_iac_apply=true  # apply changes
ansible-playbook playbooks/ceph-verify.yml --limit ceph_bootstrap    # verify live state
```

Configuration baseline is defined in [`ansible/group_vars/ceph_cluster.yml`](ansible/group_vars/ceph_cluster.yml). The apply playbook requires explicit `-e ceph_iac_apply=true` and creates a pre-apply config snapshot automatically.

## MariaDB Backup

```bash
cd ansible
ansible-playbook playbooks/setup-mariadb-backup.yml
```

Creates systemd timers on the first controller node: daily full backup (02:00), hourly incremental (:30, skipping 02:30), via `docker exec` into the Kolla mariabackup container. Backup data lives in the mariabackup Docker volume. Offsite transfer is not yet automated.

## TLS Certificate Auto-Renewal

```bash
cd ansible
ansible-playbook playbooks/setup-cert-renewal.yml
```

Installs a systemd timer on the deploy host that attempts to renew the `*.cloudnative.tw` wildcard certificate twice daily (00:00 and 12:00 with randomized delay) via certbot + Cloudflare DNS-01 challenge. On successful renewal, it atomically assembles the HAProxy PEM and runs `kolla-ansible reconfigure -t haproxy`.

Prerequisites:
- certbot and python3-certbot-dns-cloudflare installed on the deploy host
- Cloudflare API credentials at `/home/igene/.certbot/cloudflare.ini`
- Initial certificate obtained via `certbot certonly` (`/etc/letsencrypt/renewal/cloudnative.tw.conf` must exist)

## Account Automation

[`tools/account_automation/`](tools/account_automation/) manages the OpenStack user/project lifecycle for Infra Labs. It runs as a daily cron job inside a Docker container on the deploy host.

Deploy or update using the Ansible playbook:

```bash
cd ansible
ansible-playbook playbooks/deploy-account-automation.yml
```

Before the first deploy, populate the secrets in `ansible/private/tools/account_automation/` (excluded from git):

```
ansible/private/tools/account_automation/
  .env                  # copy from tools/account_automation/.env.example
  service-account.json  # Google service account key
  clouds.yaml           # OpenStack credentials
```

See [`tools/account_automation/README.en.md`](tools/account_automation/README.en.md) for full configuration reference and the complete status lifecycle.

## Host-Specific Notes

- `openstack05` Battlemage-specific GRUB flags, and an SR-IOV restore unit.
- `openstack04` is the only host currently targeted by [`ansible/playbooks/gpu-monitor.yml`](ansible/playbooks/gpu-monitor.yml).
- `arm01` is tracked as temporary and is intentionally not part of `managed_hosts`.

## Kolla-Ansible Configuration

The `kolla/` directory contains all Kolla-Ansible deployment configuration, previously maintained in the separate `infra-labs-kolla-ansible` repository.

### Layout

- `kolla/globals.yml`: main Kolla-Ansible configuration (OpenStack release, networking, enabled services, Ceph integration, TLS, Prometheus, etc.)
- `kolla/multinode`: Kolla-Ansible inventory defining control, compute, network, storage, and monitoring group membership
- `kolla/config/`: per-service configuration overrides deployed to `/etc/kolla/config/` on hosts

### Key Configuration

| Setting | Value |
|---------|-------|
| OpenStack release | 2026.1 |
| Base distro | Debian (source install) |
| Neutron plugin | OVN |
| Internal VIP | 192.168.113.252 |
| External VIP | 192.168.113.253 |
| External FQDN | openstack.cloudnative.tw |
| Docker registry | registry.cloudnative.tw |
| TLS | External only |
| Ceph integration | Glance, Cinder, Nova, RGW |
| Monitoring | Prometheus + Grafana |

### Usage with Kolla-Ansible

Kolla-Ansible commands reference files from this directory:

```bash
# Deploy (--configdir ensures the checked-in globals.yml is used)
kolla-ansible -i kolla/multinode --configdir kolla deploy

# Reconfigure a service
kolla-ansible -i kolla/multinode --configdir kolla reconfigure --tags nova

# Pre-deploy checks
kolla-ansible -i kolla/multinode --configdir kolla prechecks
```

The `kolla/config/` directory maps to the `node_custom_config` setting in `globals.yml`. When deploying, ensure this path is accessible or symlinked to `/etc/kolla/config` on the deploy host.

### Inventory Relationship

Both `ansible/hosts` (Ansible inventory) and `kolla/multinode` (Kolla-Ansible inventory) reference the same physical hosts. When adding or removing a host from the fleet, update both files. The test suite validates that hosts in `kolla/multinode` match hosts in `ansible/hosts`.

### Sensitive Files

`kolla/passwords.yml` is tracked in git as an Ansible Vault-encrypted file. Only the vault password (`kolla/ansible_vault_pass`) stays out of git.

The following kolla files are excluded from git via `.gitignore`:
- `kolla/*certificates/`
- `kolla/*.keyring`
- `kolla/*.pem`
- `kolla/clouds.yaml`
- `kolla/ansible_vault_pass`
- `kolla/prometheus-alertmanager.yml`

These must be provided by the operator before deployment.
