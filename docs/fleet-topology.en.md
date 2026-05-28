# Fleet topology

[中文](fleet-topology.md)

"Which host runs what" at a glance. Derived from [`ansible/hosts`](../ansible/hosts) and [`kolla/multinode`](../kolla/multinode); the live inventory is authoritative.

## Host summary

| Host | IP | Role summary | Notes |
|---|---|---|---|
| `openstack01` | 192.168.0.21 | OpenStack controller + compute + Ceph (bootstrap + OSD + mgr) | 472 GiB RAM, AMD, Mellanox 25 GbE. Known [bond-at-boot bug](troubleshooting.en.md#bond-does-not-come-up-at-boot-known-issue-openstack01) |
| `openstack02` | 192.168.0.22 | OpenStack controller + compute + Ceph (OSD + mgr) | 503 GiB RAM, AMD |
| `openstack04` | 192.168.0.24 | OpenStack controller + compute + Ceph (OSD) + PCI passthrough + GPU monitor | 251 GiB RAM, AMD. Memory-pressure canary |
| `openstack05` | 192.168.0.25 | OpenStack compute + Ceph (OSD) + PCI passthrough + Intel Battlemage GPU | 251 GiB RAM, AMD, kernel 6.19.6 (others on 6.12.74). Carries the `openstack05_battlemage` role |
| `openstack06` | 192.168.0.26 | Ceph (OSD) only -- no OpenStack control plane | 62 GiB RAM, AMD. **The safest canary**; start your first apply here |
| `arm01` | 192.168.0.51 | Temporary host | ARM64 (Ampere). **Not** in `managed_hosts`; unaffected by bootstrap |
| `deploy01` | 192.168.0.1 | Deploy host: runs the Kolla-Ansible CLI, tool containers, and certbot renewal | Not a fleet member, but every `deploy-*.yml` playbook targets it |

## Ansible group matrix

| Host | managed_hosts | controller | compute | ceph_bootstrap | ceph_cluster | pci_pass | deploy_host | temporary |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| openstack01 | x | x | x | x | x | | | |
| openstack02 | x | x | x | | x | | | |
| openstack04 | x | x | x | | x | x | | |
| openstack05 | x | | x | | x | x | | |
| openstack06 | x | | | | x | | | |
| arm01 | | | | | | | | x |
| deploy01 | | | | | | | x | |

> Group definitions: [glossary.en.md](glossary.en.md#ansible-inventory-groups).

## Kolla-Ansible group mapping

`kolla/multinode` uses different group names but points at the same physical hosts:

| Kolla group | Members | Purpose |
|---|---|---|
| `control` | openstack01, 02, 04 | OpenStack control plane (Keystone, Nova API, Glance, Neutron, etc.) |
| `network` | openstack01, 02, 04 | l3-agent, Octavia loadbalancer |
| `compute` | openstack01, 02, 04, 05 | nova-compute (VMs run here) |
| `storage` | openstack01, 02 | Cinder / Manila backends |
| `monitoring` | openstack01, 02 | Prometheus, Grafana |
| `ceph-mgr` | openstack01, 02 | Ceph mgr daemon |
| `non-openstack` | openstack06 | Pure Ceph node, baremetal-managed by Kolla but no OpenStack services |

## Topology (simplified)

```
                    Internet
                       |
                       v
              192.168.113.253 (External VIP)
              openstack.cloudnative.tw
                       |
                       v  HAProxy + Keepalived
              192.168.113.252 (Internal VIP)
                       |
        +--------------+--------------+
        v              v              v
  +----------+   +----------+   +----------+
  |openstack |   |openstack |   |openstack |   <- Controllers / Kolla control + network
  |   01     |   |   02     |   |   04     |      + monitoring (01/02) + compute
  |  AMD     |   |  AMD     |   |  AMD     |      + Ceph OSD + mgr (01/02)
  | 472 GiB  |   | 503 GiB  |   | 251 GiB  |      04 has PCI passthrough + GPU monitor
  +----+-----+   +----+-----+   +----+-----+
       |              |              |
       |         +----+-------+      |
       |         v            v      |
       |   +----------+  +----------+|
       |   |openstack |  |openstack ||   <- Compute-only (+ Ceph OSD)
       |   |   05     |  |   06     ||      05: Battlemage GPU + PCI passthrough
       |   |  AMD     |  |  AMD     ||      06: Ceph-only (non-openstack)
       |   | 251 GiB  |  |  62 GiB  ||
       |   +----------+  +----------+|
       |                              |
       +--------------+---------------+
                      |
                      v
              Ceph cluster (Tentacle 20.2.0)
              bootstrap: openstack01
              mgr:      openstack01, 02
              OSD:      01, 02, 04, 05, 06

         ---- Outside fleet ------------------------
         arm01    (ARM64, temporary, not managed)
         deploy01 (192.168.0.1, runs Kolla CLI + tools)
```

## Network naming

Each OpenStack node has one bond plus several VLAN sub-interfaces (referenced in [`group_vars/all.yml`](../ansible/group_vars/all.yml) and `kolla/multinode`):

| Interface | Purpose |
|---|---|
| `bond0` | Mellanox ConnectX 25 GbE dual-port LACP; also the Ceph storage network |
| `bond0.100` | OVN tunnel (Geneve) |
| `bond0.101` | Live migration |
| `bond0.1113` | API / network / management (VIPs live here) |
| `bond0.2116`, `bond0.2240` | Neutron external (floating IPs) |

## Software versions

| Component | Version / configuration |
|---|---|
| OpenStack | 2025.2, Debian source install |
| Neutron backend | OVN with distributed FIP |
| Ceph | Tentacle 20.2.0, cephadm-deployed (Debian 13 hosts run the bookworm apt suite) |
| Kernel | 6.12.74 (openstack01/02/04/06), 6.19.6 (openstack05, for Battlemage) |
| NIC | Mellanox ConnectX 25 GbE, mlnx-en DKMS driver |
| Docker registry | registry.cloudnative.tw |
| TLS | External only (`*.cloudnative.tw` renewed by certbot + Cloudflare DNS-01) |

## Which playbook hits which hosts?

| Change | Hosts affected | Playbook |
|---|---|---|
| Host baseline (sysctl, bond, mail, KVM, GRUB, tuning) | `managed_hosts` (openstack01, 02, 04, 05, 06; no 03) | [`bootstrap.yml`](../ansible/playbooks/bootstrap.yml) |
| Ceph day-2 config | Ceph daemons on `ceph_cluster`, driven from `ceph_bootstrap` | [`ceph-audit.yml`](../ansible/playbooks/ceph-audit.yml) / [`ceph-apply.yml`](../ansible/playbooks/ceph-apply.yml) / [`ceph-verify.yml`](../ansible/playbooks/ceph-verify.yml) |
| Host performance tuning | `managed_hosts`, rolling | [`apply-tuning.yml`](../ansible/playbooks/apply-tuning.yml) |
| MariaDB backup timer | First controller (openstack01) | [`setup-mariadb-backup.yml`](../ansible/playbooks/setup-mariadb-backup.yml) |
| TLS cert renewal timer | Deploy host | [`setup-cert-renewal.yml`](../ansible/playbooks/setup-cert-renewal.yml) |
| account_automation deploy | Deploy host | [`deploy-account-automation.yml`](../ansible/playbooks/deploy-account-automation.yml) |
| GPU temperature monitor | openstack04 only | [`gpu-monitor.yml`](../ansible/playbooks/gpu-monitor.yml) |

Full playbook list: [`ansible/README.en.md`](../ansible/README.en.md#playbooks).
