# 名詞表

[English](glossary.en.md)

收錄此 repo 與 fleet 文件中常用的縮寫與術語。新加入者建議先掃過，看不懂時回來查。

## Ansible inventory 群組

| 名詞 | 意義 |
|---|---|
| `managed_hosts` | 穩定運行的 x86 fleet。`bootstrap.yml`、`apply-tuning.yml`、`upgrade.yml` 等預設打這個 group |
| `temporary` | 非穩定狀態的主機（目前是 `arm01`）。**不**屬於 `managed_hosts`，bootstrap 不會碰 |
| `arm` | ARM 主機集合（目前只有 `arm01`） |
| `controller` | OpenStack 控制面節點（Keystone、Nova API、Glance、Neutron 等）。對應 Kolla 的 `control` group |
| `compute` | 跑 nova-compute、實際承載 VM 的節點 |
| `ceph_bootstrap` | Ceph cluster 的初始 bootstrap 主機（目前是 openstack01）。所有 `ceph-*.yml` playbook 都從這台呼叫 cephadm |
| `ceph_cluster` | 全部參與 Ceph cluster 的主機（OSD 跑在這些） |
| `pci_pass` | 需要 VFIO／IOMMU GRUB 設定的主機（PCI passthrough、SR-IOV） |
| `deploy_host` | Deploy host 本體（`deploy01`，192.168.0.1）。tools 容器與 Kolla CLI 跑這台 |

## Kolla-Ansible 群組與檔案

| 名詞 | 意義 |
|---|---|
| Kolla-Ansible | OpenStack 上游的部署框架。本 repo 包進 `kolla/` 目錄 |
| `globals.yml` | Kolla-Ansible 主設定檔（release、網路、enabled service、TLS、Ceph 整合） |
| `multinode` | Kolla-Ansible inventory。群組命名與 `ansible/hosts` 不同，但指向同一批實體主機（見 [fleet-topology.md](fleet-topology.md#kolla-ansible-group-對照)） |
| `passwords.yml` | OpenStack 服務密碼集合，以 Ansible Vault 加密 |
| `ansible_vault_pass` | Vault 解密用密碼檔（不納入 git） |
| Kolla `control` / `network` / `compute` / `storage` / `monitoring` | Kolla 對控制面、網路面、運算、儲存後端、監控的群組劃分 |
| `non-openstack` | Kolla 內表示「Kolla 不部署 OpenStack 服務」的群組（目前是 openstack06，純 Ceph 節點） |

## OpenStack 元件

| 名詞 | 意義 |
|---|---|
| Keystone | 認證／授權服務 |
| Glance | VM 映像檔服務 |
| Nova | 運算服務（nova-api、nova-compute） |
| Cinder | Block storage |
| Neutron | 網路服務（OVN 後端） |
| Octavia | Load Balancer as a Service |
| Horizon | 官方 Web 介面 |
| Skyline | 新版 Web 介面（fleet 同時提供 Horizon 與 Skyline） |
| OVN | Open Virtual Network。Neutron 後端，提供 L2/L3 與 distributed FIP |
| distributed FIP | Floating IP 流量直接走 compute 節點，不集中經 network 節點 |
| Geneve | OVN 使用的 tunnel encapsulation（走 `bond0.100`） |
| domain / project | Keystone 的多租戶模型；本 repo 在固定 domain 下開 project |
| `member` / `load-balancer_member` | 指派給使用者的 role（前者操作專案資源，後者額外授權 LBaaS） |
| VIP | Virtual IP，由 HAProxy + Keepalived 持有 |

## Ceph

| 名詞 | 意義 |
|---|---|
| cephadm | Ceph 官方 cluster lifecycle 工具（取代 ceph-ansible／ceph-deploy） |
| mon | Ceph monitor daemon |
| mgr | Ceph manager daemon |
| OSD | Object Storage Daemon。一個 OSD = 一顆磁碟 |
| RGW | RADOS Gateway。提供 S3／Swift 介面 |
| Tentacle | Ceph 19.x 之後的 release codename（fleet 跑 20.2.0） |
| BlueStore | OSD 的儲存後端 |
| implicit-tenant | RGW 的多租戶模式：使用者 ID 同時也是 tenant（影響 bucket 命名與刪除順序） |
| audit-apply-verify | 本 repo `roles/ceph-config` 的工作流程：審計差異 → 套用變更（需 opt-in 旗標） → 驗證 live 狀態 |
| day-2 config | cluster bootstrap 完成後的設定（config entries、labels、service specs、pool 設定） |

## 網路硬體與設定

| 名詞 | 意義 |
|---|---|
| bond / LACP | 雙口（25 GbE × 2）802.3ad 聚合 |
| Mellanox ConnectX | fleet 使用的 NIC 系列 |
| mlnx-en | Mellanox 官方 Linux 驅動（DKMS 安裝） |
| DKMS | Dynamic Kernel Module Support。kernel 更新時自動重編譯外掛模組 |
| `bond0`／`bond0.<vlan>` | 主 bond 與其 VLAN sub-interface。對應關係見 [fleet-topology.md](fleet-topology.md#網路命名慣例) |

## 主機端硬體／kernel 名詞

| 名詞 | 意義 |
|---|---|
| NUMA | Non-Uniform Memory Access。AMD CPU 為多 NUMA node 架構，影響記憶體分配策略 |
| THP | Transparent Hugepages。`roles/tuning` 對 KVM 主機做特定設定 |
| BBR | TCP 擁塞控制演算法（Google 出品） |
| zswap | Linux 壓縮 swap cache |
| swap | Swap 區。`roles/swap` 管理 swapfile |
| SR-IOV | Single Root I/O Virtualization。NIC／GPU 可被分割成多個 VF 給 VM 使用 |
| PCI passthrough | 整顆 PCI 裝置直通給 VM（透過 VFIO） |
| Battlemage | Intel Arc Battlemage GPU 系列（openstack05 上的卡） |

## 此 repo 的慣例

| 名詞 | 意義 |
|---|---|
| canary | 第一台先 apply 的目標。本 fleet 預設挑 openstack06（純 Ceph、無控制面、最小爆炸半徑） |
| rolling apply | 逐台套用，每台間隔（network role 預設 15 秒） |
| dry-run | 帶 `--check --diff`，不實際變更，但會連線並印出 proposed diff |
| `private/` | repo 內機敏檔案目錄，已 `.gitignore` |
| `host_vars/<host>.yml` | 各主機獨立變數（`node_num`、interface 名稱、GRUB 清單等） |
| `group_vars/all.yml` | 跨 group 的共用預設值 |
| `validate.sh` | 本地靜態驗證（yamllint、ansible-lint、syntax、inventory、template） |

## 工具與容器

| 名詞 | 意義 |
|---|---|
| supercronic | 容器內 cron 替代品。**不繼承容器 PATH**，crontab 必須用絕對路徑（見 [troubleshooting.md](troubleshooting.md#新增-cron-條目時的-command-not-found)） |
| Resend | Email 發送服務。`account_automation` 用它寄歡迎信／到期信 |
| certbot + Cloudflare DNS-01 | TLS 憑證自動續期方式 |
| MariaBackup | Kolla 內的 MariaDB 備份工具 |
