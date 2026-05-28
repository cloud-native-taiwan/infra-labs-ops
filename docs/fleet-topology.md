# Fleet 拓樸

[English](fleet-topology.en.md)

讓你一眼看出「哪台主機跑什麼」。內容由 [`ansible/hosts`](../ansible/hosts) 與 [`kolla/multinode`](../kolla/multinode) 推導，請以實際 inventory 為準。

## 主機總覽

| 主機 | IP | 角色摘要 | 備註 |
|---|---|---|---|
| `openstack01` | 192.168.0.21 | OpenStack controller + compute + Ceph (bootstrap + OSD + mgr) | 472 GiB RAM、AMD、Mellanox 25 GbE。已知 [bond 開機未起來](troubleshooting.md#bond-開機未起來已知問題openstack01) |
| `openstack02` | 192.168.0.22 | OpenStack controller + compute + Ceph (OSD + mgr) | 503 GiB RAM、AMD |
| `openstack04` | 192.168.0.24 | OpenStack controller + compute + Ceph (OSD) + PCI passthrough + GPU monitor | 251 GiB RAM、AMD。記憶體壓力較高的 canary |
| `openstack05` | 192.168.0.25 | OpenStack compute + Ceph (OSD) + PCI passthrough + Intel Battlemage GPU | 251 GiB RAM、AMD、kernel 6.19.6（其餘為 6.12.74）。專屬 `openstack05_battlemage` role |
| `openstack06` | 192.168.0.26 | Ceph (OSD) only — 不跑任何 OpenStack 控制面 | 62 GiB RAM、AMD。**最安全的 canary**，建議第一次 apply 從這台開始 |
| `arm01` | 192.168.0.51 | 暫時主機 | ARM64 (Ampere)，**不在** `managed_hosts`，不會被 bootstrap 影響 |
| `deploy01` | 192.168.0.1 | Deploy host：跑 Kolla-Ansible CLI、tools container、certbot 續期 | 不是 fleet member，但所有 `deploy-*.yml` playbook 都打這台 |

## Ansible group 矩陣

| 主機 | managed_hosts | controller | compute | ceph_bootstrap | ceph_cluster | pci_pass | deploy_host | temporary |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| openstack01 | ✓ | ✓ | ✓ | ✓ | ✓ | | | |
| openstack02 | ✓ | ✓ | ✓ | | ✓ | | | |
| openstack04 | ✓ | ✓ | ✓ | | ✓ | ✓ | | |
| openstack05 | ✓ | | ✓ | | ✓ | ✓ | | |
| openstack06 | ✓ | | | | ✓ | | | |
| arm01 | | | | | | | | ✓ |
| deploy01 | | | | | | | ✓ | |

> 名詞定義見 [glossary.md](glossary.md#ansible-inventory-群組)。

## Kolla-Ansible group 對照

`kolla/multinode` 的群組與 `ansible/hosts` 不同名，但指向同一批實體主機：

| Kolla group | 成員 | 用途 |
|---|---|---|
| `control` | openstack01, 02, 04 | OpenStack 控制面（Keystone、Nova API、Glance、Neutron 等） |
| `network` | openstack01, 02, 04 | l3-agent、Octavia loadbalancer |
| `compute` | openstack01, 02, 04, 05 | nova-compute（VM 跑在這裡） |
| `storage` | openstack01, 02 | Cinder / Manila backend |
| `monitoring` | openstack01, 02 | Prometheus、Grafana |
| `ceph-mgr` | openstack01, 02 | Ceph mgr daemon |
| `non-openstack` | openstack06 | 純 Ceph 節點，被 Kolla 視為 baremetal 但不部署 OpenStack 服務 |

## 拓樸圖（簡化）

```
                    Internet
                       │
                       ▼
              192.168.113.253 (External VIP)
              openstack.cloudnative.tw
                       │
                       ▼  HAProxy + Keepalived
              192.168.113.252 (Internal VIP)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │openstack │   │openstack │   │openstack │   ← Controllers / Kolla control + network
  │   01     │   │   02     │   │   04     │     + monitoring (01/02) + compute
  │  AMD     │   │  AMD     │   │  AMD     │     + Ceph OSD + mgr (01/02)
  │ 472 GiB  │   │ 503 GiB  │   │ 251 GiB  │     04 has PCI passthrough + GPU monitor
  └────┬─────┘   └────┬─────┘   └────┬─────┘
       │              │              │
       │              │              │
       │         ┌────┴───────┐      │
       │         ▼            ▼      │
       │   ┌──────────┐  ┌──────────┐│
       │   │openstack │  │openstack ││  ← Compute-only (+ Ceph OSD)
       │   │   05     │  │   06     ││     05: Battlemage GPU + PCI passthrough
       │   │  AMD     │  │  AMD     ││     06: Ceph-only (non-openstack)
       │   │ 251 GiB  │  │  62 GiB  ││
       │   └──────────┘  └──────────┘│
       │                              │
       └──────────────┬───────────────┘
                      │
                      ▼
              Ceph cluster (Tentacle 20.2.0)
              bootstrap: openstack01
              mgr:      openstack01, 02
              OSD:      01, 02, 04, 05, 06

         ──── 另外 ──────────────────────────────────
         arm01  (ARM64, temporary, 不在 fleet)
         deploy01  (192.168.0.1, 跑 Kolla CLI + tools)
```

## 網路命名慣例

每台 OpenStack 節點都有一條 bond + 多個 VLAN sub-interface（[`group_vars/all.yml`](../ansible/group_vars/all.yml) 與 `kolla/multinode` 都會引用）：

| Interface | 用途 |
|---|---|
| `bond0` | Mellanox ConnectX 25 GbE 雙口 LACP；同時是 Ceph storage 網路 |
| `bond0.100` | OVN tunnel（Geneve） |
| `bond0.101` | Live migration |
| `bond0.1113` | API / network / management（VIP 也在此網段） |
| `bond0.2116`、`bond0.2240` | Neutron external（floating IP 用） |

## 軟體版本

| 元件 | 版本／設定 |
|---|---|
| OpenStack | 2025.2，Debian source install |
| Neutron 後端 | OVN，distributed FIP |
| Ceph | Tentacle 20.2.0，cephadm 部署（Debian 13 主機跑 bookworm apt suite） |
| Kernel | 6.12.74（openstack01/02/04/06），6.19.6（openstack05，配合 Battlemage） |
| NIC | Mellanox ConnectX 25 GbE，mlnx-en DKMS driver |
| Docker registry | registry.cloudnative.tw |
| TLS | 僅 external（`*.cloudnative.tw` 由 certbot + Cloudflare DNS-01 續期） |

## 部署什麼到哪台？快速對照

| 變更類別 | 哪些主機被影響 | 用哪個 playbook |
|---|---|---|
| 主機基礎設定（sysctl、bond、mail、KVM、GRUB、tuning） | `managed_hosts`（openstack01, 02, 04, 05, 06；無 03） | [`bootstrap.yml`](../ansible/playbooks/bootstrap.yml) |
| Ceph day-2 設定 | `ceph_cluster` 上的 daemon（透過 `ceph_bootstrap` 主機操作） | [`ceph-audit.yml`](../ansible/playbooks/ceph-audit.yml) / [`ceph-apply.yml`](../ansible/playbooks/ceph-apply.yml) / [`ceph-verify.yml`](../ansible/playbooks/ceph-verify.yml) |
| 主機效能微調 | `managed_hosts`，rolling | [`apply-tuning.yml`](../ansible/playbooks/apply-tuning.yml) |
| MariaDB 備份 timer | 第一台 controller（openstack01） | [`setup-mariadb-backup.yml`](../ansible/playbooks/setup-mariadb-backup.yml) |
| TLS 憑證續期 timer | deploy host | [`setup-cert-renewal.yml`](../ansible/playbooks/setup-cert-renewal.yml) |
| account_automation 部署 | deploy host | [`deploy-account-automation.yml`](../ansible/playbooks/deploy-account-automation.yml) |
| GPU 溫度監控 | openstack04 only | [`gpu-monitor.yml`](../ansible/playbooks/gpu-monitor.yml) |

完整 playbook 一覽：[`ansible/README.md`](../ansible/README.md#playbook-清單)。
