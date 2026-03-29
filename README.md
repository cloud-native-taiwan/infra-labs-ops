# Infra Labs Ops

[English](README.en.md)

本 repo 管理 Infra Labs 的主機設定與部署設定。Ansible 相關的 inventory、playbook、role 集中於 [`ansible/`](ansible) 目錄，Kolla-Ansible 部署設定位於 [`kolla/`](kolla)。密碼檔（`kolla/passwords.yml`）以 Ansible Vault 加密後納入版本控制；其餘機敏檔案（如 private key、certificate）則透過 `.gitignore` 排除。

## Repo 結構

```
├── ansible/                 # Ansible 主機設定與 Kolla-Ansible 部署設定
│   ├── hosts                # inventory（group 與 ansible_host 定義）
│   ├── host_vars/           # 各主機變數與 host-specific policy
│   ├── group_vars/all.yml   # 共用預設值
│   ├── playbooks/           # 所有 Ansible playbook
│   ├── roles/               # 各功能的 Ansible role
│   ├── scripts/             # 驗證與工具腳本
│   ├── tests/               # 測試
│   ├── collections/         # Ansible collections
│   └── private/             # 機敏檔案（不納入 git）
├── tools/                   # Deploy host 上的營運工具（各自獨立建置）
│   └── account_automation/  # OpenStack 帳號生命週期自動化
├── kolla/                   # Kolla-Ansible 部署設定
├── admin_scripts/           # 營運工具腳本
├── docs/                    # 文件
├── README.md
└── README.en.md
```

## 本 Repo 的功能

管理 Infra Labs fleet 的基礎主機設定：

- 基礎套件與 sysctl tuning
- bond 與 resolver 設定
- mail relay 設定
- KVM nested virtualization 設定
- GRUB kernel command line 管理
- BBR tuning
- 主機效能調校（NVMe I/O scheduler、Transparent Hugepages、NIC ring buffer）
- Ceph bootstrap 主機準備
- `openstack05` 的 Battlemage 專屬設定
- Ceph day-2 設定管理（audit-apply-verify 工作流程）
- MariaDB 備份排程

主要進入點為 [`ansible/playbooks/bootstrap.yml`](ansible/playbooks/bootstrap.yml)，依據 [`ansible/hosts`](ansible/hosts) 中定義的 inventory group 套用對應的 role。

## 工作站前置需求

控制機需具備：

- Python 3
- 以 `debian` 使用者 SSH 連線至目標主機的權限
- 目標主機上該 SSH 使用者的 passwordless sudo
- 本地安裝 Ansible 工具

本 repo 的驗證流程假設本地有 `.venv/` virtualenv。若尚未建立：

```bash
python3 -m venv .venv
.venv/bin/pip install ansible-core ansible-lint yamllint jinja2 pyyaml
```

## 機敏檔案

執行 bootstrap playbook 前，需建立以下本地檔案：

- `ansible/private/authorized_keys`
- `ansible/private/passwd.client`

`roles/base` 會將 `private/authorized_keys` 複製至 `/home/debian/.ssh/authorized_keys`。
`roles/mail` 會將 `private/passwd.client` 複製至 `/etc/exim4/passwd.client`。

```text
ansible/private/
  authorized_keys
  passwd.client
```

## Inventory 架構

本 repo 使用：

- [`ansible/hosts`](ansible/hosts) 中的 group membership 與 `ansible_host`
- [`ansible/host_vars/`](ansible/host_vars) 中的各主機變數

重要 group：

- `managed_hosts`：穩定運行的 x86 fleet
- `temporary`：非穩定狀態的主機（如 `arm01`）
- `controller`：Kolla controller 節點
- `compute`：compute 節點
- `ceph_bootstrap`：Ceph 初始 bootstrap 主機
- `ceph_cluster`：參與 Ceph cluster 的主機
- `pci_pass`：需要 VFIO 相關 boot/module 設定的主機

## Cephadm 套件說明

[`ansible/roles/ceph-bootstrap`](ansible/roles/ceph-bootstrap) 目前在 Debian 13 `trixie` 主機上使用 Ceph upstream 的 `bookworm` apt repository：

- repo release：`tentacle`
- package suite：`bookworm`
- 目標套件版本：`20.2.0`

這是刻意的設計。Ceph upstream 的 Debian repository 目前尚未發布 `trixie` suite，但 `bookworm` 的 cephadm package 是經過驗證的路徑，用於在 Debian 13 上 bootstrap 與管理 Tentacle cluster。待 Ceph 發布原生 `trixie` suite 後再行調整。

## 驗證

對主機進行任何操作前，先執行本地靜態驗證：

```bash
./ansible/scripts/validate.sh
```

驗證內容：

- `yamllint`
- `ansible-lint`
- playbook syntax check
- inventory target 驗證
- template rendering 檢查

## 對實際主機進行 Dry Run

可對實際主機執行 Ansible dry-run。所有 `ansible-playbook` 指令需在 `ansible/` 目錄下執行，或指定 `ANSIBLE_CONFIG`。

單一主機範例：

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff --limit openstack01
```

穩定 fleet 範例：

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff --limit managed_hosts
```

注意事項：

- `--check` 仍會連線至主機並收集 facts
- `--diff` 對 GRUB、網路、mail 等 rendered file 特別有用
- command-driven task 在 check mode 下的資訊較有限
- `ansible/private/` 下的機敏檔案在 dry-run 或 apply 前必須存在
- 網路重啟會逐台進行，主機間間隔 15 秒

針對特定變更，可指定個別 playbook：

```bash
cd ansible
ansible-playbook playbooks/gpu-monitor.yml --check --diff --limit openstack04
ansible-playbook playbooks/swap.yml --check --diff --limit openstack05
ansible-playbook playbooks/exporter.yml --check --diff --limit managed_hosts
```

## 建議的 Apply 流程

日常變更：

1. 更新 inventory、host vars、role 或 template。
2. 執行 `./ansible/scripts/validate.sh`。
3. 在 `ansible/` 目錄下執行 `ansible-playbook playbooks/<playbook> --check --diff --limit <host-or-group>`。
4. 檢視 proposed file diff 與 changed task。
5. dry-run 無誤後，移除 `--check` 正式套用。

當 `bond0` 設定變更時，network role 會逐台重啟網路，主機間等待 15 秒，以減少對 Ceph 及其他 cluster service 的影響。

範例：

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff --limit openstack06
ansible-playbook playbooks/bootstrap.yml --limit openstack06
```

## 新主機首次設定

本 repo 假設：

- 主機可透過 SSH 連線
- `debian` 使用者已存在或可供 Ansible 使用
- 該使用者具有 passwordless sudo
- 操作者已決定該主機屬於哪些 inventory group

新增穩定主機的步驟：

1. 將主機名稱與 `ansible_host` 加入 [`ansible/hosts`](ansible/hosts)。
2. 建立 [`ansible/host_vars/<hostname>.yml`](ansible/host_vars)，至少包含：
   - `node_num`
   - `interface1`
   - `interface2`
   - 若由 GRUB role 管理，需包含 GRUB 清單
3. 手動確認 SSH 與 sudo 存取。
4. 執行 `./ansible/scripts/validate.sh`。
5. 在 `ansible/` 目錄下對該主機 dry-run `playbooks/bootstrap.yml`。
6. 正式 apply `playbooks/bootstrap.yml`。

## Ceph Bootstrap 流程

本 repo 的 Ceph role 會在 `ceph_bootstrap` 主機上準備 upstream Ceph apt repository 並安裝 `cephadm`。

典型流程：

1. Bootstrap 主機基礎設定：

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --limit ceph_bootstrap
```

2. 確認主機端工具已安裝：

```bash
ssh debian@<bootstrap-host> 'which cephadm && cephadm version'
```

3. 在 bootstrap 主機上執行 Ceph cluster bootstrap：

```bash
ssh debian@<bootstrap-host> 'sudo cephadm bootstrap --mon-ip <mon-ip>'
```

4. Bootstrap 完成後，透過 `cephadm` 加入其餘 cluster 主機。

本 repo 尚未完全自動化 cluster bootstrap 指令本身，僅準備好主機環境，讓操作者能以正確的 monitor IP 與 cluster 選項手動執行。

## 主機效能調校

[`tuning`](ansible/roles/tuning) role（包含於 `bootstrap.yml`）負責 NVMe I/O scheduler、Transparent Hugepage、NIC ring buffer 等調校。各設定的詳細說明請參閱 [`ansible/roles/tuning/README.md`](ansible/roles/tuning/README.md)。

```bash
cd ansible
ansible-playbook playbooks/apply-tuning.yml    # 套用（逐台 rolling apply）
ansible-playbook playbooks/verify-tuning.yml   # 驗證（唯讀，不做變更）
```

## Ceph 設定管理

[`ceph-config`](ansible/roles/ceph-config) role 管理 Ceph day-2 設定（config entries、cephadm labels、service specs），透過 audit-apply-verify 工作流程運作。詳細文件請參閱 [`ansible/roles/ceph-config/README.md`](ansible/roles/ceph-config/README.md)。

```bash
cd ansible
ansible-playbook playbooks/ceph-audit.yml --limit ceph_bootstrap     # 審計差異（唯讀）
ansible-playbook playbooks/ceph-apply.yml --limit ceph_bootstrap -e ceph_iac_apply=true  # 套用變更
ansible-playbook playbooks/ceph-verify.yml --limit ceph_bootstrap    # 驗證 live 狀態
```

設定基準線定義於 [`ansible/group_vars/ceph_cluster.yml`](ansible/group_vars/ceph_cluster.yml)。套用 playbook 需明確傳入 `-e ceph_iac_apply=true`，並自動建立套用前的設定快照。

## MariaDB 備份

```bash
cd ansible
ansible-playbook playbooks/setup-mariadb-backup.yml
```

在第一台 controller 節點建立 systemd timer：每日凌晨 2:00 完整備份、每小時 :30 增量備份（跳過 02:30），透過 `docker exec` 執行 Kolla mariabackup container。備份資料存放於 mariabackup Docker volume，異地備份尚未自動化。

## 主機專屬說明

- `openstack05`：Battlemage 專屬 GRUB flag、SR-IOV restore unit。
- `openstack04`：目前唯一被 [`ansible/playbooks/gpu-monitor.yml`](ansible/playbooks/gpu-monitor.yml) 管理的主機。
- `arm01`：標記為 temporary，不屬於 `managed_hosts`。

## Kolla-Ansible 設定

`kolla/` 目錄包含所有 Kolla-Ansible 部署設定，先前維護於獨立的 `infra-labs-kolla-ansible` repository。

### 目錄結構

- `kolla/globals.yml`：Kolla-Ansible 主設定檔（OpenStack release、網路、啟用的 service、Ceph 整合、TLS、Prometheus 等）
- `kolla/multinode`：Kolla-Ansible inventory，定義 control、compute、network、storage、monitoring group
- `kolla/config/`：各 service 的設定覆寫，部署時會放置於主機的 `/etc/kolla/config/`

### 主要設定

| 設定項目 | 值 |
|---------|-------|
| OpenStack release | 2025.2 |
| Base distro | Debian (source install) |
| Neutron plugin | OVN |
| Internal VIP | 192.168.113.252 |
| External VIP | 192.168.113.253 |
| External FQDN | openstack.cloudnative.tw |
| Docker registry | registry.cloudnative.tw |
| TLS | External only |
| Ceph integration | Glance, Cinder, Nova, RGW |
| Monitoring | Prometheus + Grafana |

### 搭配 Kolla-Ansible 使用

Kolla-Ansible 指令參照本目錄的檔案：

```bash
# 部署（--configdir 確保使用 repo 中的 globals.yml）
kolla-ansible -i kolla/multinode --configdir kolla deploy

# 重新設定特定 service
kolla-ansible -i kolla/multinode --configdir kolla reconfigure --tags nova

# 部署前檢查
kolla-ansible -i kolla/multinode --configdir kolla prechecks
```

`kolla/config/` 目錄對應 `globals.yml` 中的 `node_custom_config` 設定。部署時，請確保此路徑可存取，或將其 symlink 至 deploy host 上的 `/etc/kolla/config`。

### Inventory 關聯

`ansible/hosts`（Ansible inventory）與 `kolla/multinode`（Kolla-Ansible inventory）參照相同的實體主機。新增或移除主機時，須同時更新兩個檔案。test suite 會驗證 `kolla/multinode` 中的主機與 `ansible/hosts` 一致。

### 機敏檔案

`kolla/passwords.yml` 以 Ansible Vault 加密後納入 git 版本控制。僅 vault password（`kolla/ansible_vault_pass`）不納入 git。

以下 kolla 檔案透過 `.gitignore` 排除於 git 之外：
- `kolla/*certificates/`
- `kolla/*.keyring`
- `kolla/*.pem`
- `kolla/clouds.yaml`
- `kolla/ansible_vault_pass`
- `kolla/prometheus-alertmanager.yml`

這些檔案須由操作者在部署前自行提供。
