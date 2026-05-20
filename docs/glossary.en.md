# Glossary

[中文](glossary.md)

Abbreviations and terms used across this repo and the fleet docs. New operators: skim once, return when something is unclear.

## Ansible inventory groups

| Term | Meaning |
|---|---|
| `managed_hosts` | The steady-state x86 fleet. Default target of `bootstrap.yml`, `apply-tuning.yml`, `upgrade.yml`, etc. |
| `temporary` | Non-steady-state hosts (currently `arm01`). **Not** part of `managed_hosts`; bootstrap leaves it alone |
| `arm` | ARM hosts (currently only `arm01`) |
| `controller` | OpenStack control-plane nodes (Keystone, Nova API, Glance, Neutron, etc.). Maps to Kolla `control` |
| `compute` | Nodes running nova-compute that actually host VMs |
| `ceph_bootstrap` | The host used to bootstrap the Ceph cluster (currently openstack01). All `ceph-*.yml` playbooks drive cephadm from here |
| `ceph_cluster` | Every host participating in the Ceph cluster (OSDs live here) |
| `pci_pass` | Hosts that need VFIO/IOMMU GRUB settings (PCI passthrough, SR-IOV) |
| `deploy_host` | The deploy host itself (`deploy01`, 192.168.0.1). Runs tool containers and the Kolla CLI |

## Kolla-Ansible groups and files

| Term | Meaning |
|---|---|
| Kolla-Ansible | OpenStack's upstream deployment framework. Lives under `kolla/` in this repo |
| `globals.yml` | Kolla's main config (release, networking, enabled services, TLS, Ceph integration) |
| `multinode` | Kolla's inventory. Different group names than `ansible/hosts`, but the same physical hosts (see [fleet-topology.en.md](fleet-topology.en.md#kolla-ansible-group-mapping)) |
| `passwords.yml` | OpenStack service passwords, Ansible Vault-encrypted |
| `ansible_vault_pass` | Vault password file (not in git) |
| Kolla `control` / `network` / `compute` / `storage` / `monitoring` | How Kolla partitions control-plane, network, compute, storage backend, and monitoring services |
| `non-openstack` | Kolla group for "baremetal-managed but no OpenStack services" (currently openstack06, a Ceph-only node) |

## OpenStack components

| Term | Meaning |
|---|---|
| Keystone | Identity / authorization service |
| Glance | VM image service |
| Nova | Compute service (nova-api, nova-compute) |
| Cinder | Block storage |
| Neutron | Networking (OVN backend) |
| Octavia | Load Balancer as a Service |
| Horizon | The classic web UI |
| Skyline | The newer web UI (fleet offers both) |
| OVN | Open Virtual Network. Neutron backend providing L2/L3 and distributed FIP |
| distributed FIP | Floating IP traffic exits via the compute node hosting the VM, not a centralized network node |
| Geneve | The tunnel encapsulation OVN uses (rides `bond0.100`) |
| domain / project | Keystone's multi-tenant model; this repo creates projects under a fixed domain |
| `member` / `load-balancer_member` | Roles assigned to users (resource ops; extra role for LBaaS) |
| VIP | Virtual IP held by HAProxy + Keepalived |

## Ceph

| Term | Meaning |
|---|---|
| cephadm | Ceph's official cluster lifecycle tool (replaces ceph-ansible / ceph-deploy) |
| mon | Ceph monitor daemon |
| mgr | Ceph manager daemon |
| OSD | Object Storage Daemon. One OSD per disk |
| RGW | RADOS Gateway. Provides S3/Swift APIs |
| Tentacle | The Ceph release codename after 19.x (fleet runs 20.2.0) |
| BlueStore | The OSD storage backend |
| implicit-tenant | RGW multi-tenant mode where the user ID is also the tenant (affects bucket naming and deletion order) |
| audit-apply-verify | This repo's `roles/ceph-config` workflow: audit drift -> apply (opt-in flag required) -> verify live state |
| day-2 config | Post-bootstrap configuration (config entries, labels, service specs, pool settings) |

## Network hardware and config

| Term | Meaning |
|---|---|
| bond / LACP | Dual-port (25 GbE x 2) 802.3ad aggregation |
| Mellanox ConnectX | NIC family used across the fleet |
| mlnx-en | Mellanox's official Linux driver (installed via DKMS) |
| DKMS | Dynamic Kernel Module Support. Auto-rebuilds out-of-tree modules on kernel updates |
| `bond0` / `bond0.<vlan>` | Main bond and its VLAN sub-interfaces. Mapping in [fleet-topology.en.md](fleet-topology.en.md#network-naming) |

## Host hardware / kernel

| Term | Meaning |
|---|---|
| NUMA | Non-Uniform Memory Access. AMD CPUs are multi-NUMA-node, affecting memory allocation strategy |
| THP | Transparent Hugepages. `roles/tuning` configures these for KVM hosts |
| BBR | TCP congestion control algorithm from Google |
| zswap | Linux compressed swap cache |
| swap | Swap area. `roles/swap` manages the swapfile |
| SR-IOV | Single Root I/O Virtualization. A NIC/GPU is split into multiple VFs presentable to VMs |
| PCI passthrough | Hand an entire PCI device to a VM (via VFIO) |
| Battlemage | Intel Arc Battlemage GPU series (the card in openstack05) |

## Repo conventions

| Term | Meaning |
|---|---|
| canary | The first host you apply to. Default pick: openstack06 (Ceph-only, no control plane, smallest blast radius) |
| rolling apply | Apply one host at a time, with a pause between (`roles/network` defaults to 15s) |
| dry-run | `--check --diff`. No real change, but connects to hosts and prints the proposed diff |
| `private/` | In-repo directory for secrets, already `.gitignore`d |
| `host_vars/<host>.yml` | Per-host variables (`node_num`, interface names, GRUB lists, etc.) |
| `group_vars/all.yml` | Cross-group default values |
| `validate.sh` | Local static validation (yamllint, ansible-lint, syntax, inventory, template) |

## Tools and containers

| Term | Meaning |
|---|---|
| supercronic | Container-friendly cron replacement. **Does not inherit container PATH** -- crontab entries must use absolute paths (see [troubleshooting.en.md](troubleshooting.en.md#command-not-found-when-adding-a-cron-entry)) |
| Resend | Email sending service. `account_automation` uses it for welcome/expiry mails |
| certbot + Cloudflare DNS-01 | The TLS cert auto-renewal path |
| MariaBackup | The MariaDB backup tool bundled with Kolla |
