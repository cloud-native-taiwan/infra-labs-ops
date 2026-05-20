# Ansible

[English](README.en.md)

本目錄是 Infra Labs Ansible 設定的進入點。所有 `ansible-playbook` 指令請從 `ansible/` 執行，或設定 `ANSIBLE_CONFIG` 指向 [`ansible.cfg`](ansible.cfg)。

> Repo 層級的安裝、驗證、apply 流程說明請見 [Root README](../README.md)。本檔案僅作為目錄內導覽。

## 目錄結構

| 路徑 | 內容 |
|---|---|
| [`hosts`](hosts) | Inventory：group 成員與 `ansible_host` 設定 |
| [`group_vars/`](group_vars) | Group 變數（含 `all.yml`、`ceph_cluster.yml`） |
| [`host_vars/`](host_vars) | 各主機變數（`node_num`、`interface1/2`、GRUB list 等） |
| [`playbooks/`](playbooks) | 所有 playbook（見下方清單） |
| [`roles/`](roles) | Ansible role 集合（見下方清單） |
| [`scripts/`](scripts) | `validate.sh`（yamllint / ansible-lint / syntax check / inventory 驗證 / template 渲染） |
| [`tests/`](tests) | inventory 與 template 的 Python 測試 |
| `collections/` | 鎖定版本的 Ansible collection |
| `private/` | 機敏檔案（`authorized_keys`、`passwd.client`、`tools/<name>/`），**不納入 git** |
| [`ansible.cfg`](ansible.cfg) | inventory 路徑、role 路徑等預設值 |

## Playbook 清單

| Playbook | 目標 group | 用途 |
|---|---|---|
| [`bootstrap.yml`](playbooks/bootstrap.yml) | `managed_hosts`、`ceph_bootstrap` | 主要進入點。套用 base / network / mail / kvm / grub / swap / bbr / tuning，並準備 cephadm |
| [`apply-tuning.yml`](playbooks/apply-tuning.yml) | `managed_hosts`（rolling，serial: 1） | 僅套用 sysctl 與主機效能調校，不執行完整 bootstrap |
| [`verify-tuning.yml`](playbooks/verify-tuning.yml) | `managed_hosts` | 唯讀，驗證 tuning 設定是否與預期相符 |
| [`bbr.yml`](playbooks/bbr.yml) | `managed_hosts` | 單獨套用 BBR 設定 |
| [`swap.yml`](playbooks/swap.yml) | `managed_hosts` | 單獨管理 swapfile |
| [`pci-pass.yml`](playbooks/pci-pass.yml) | `pci_pass` | 對 PCI passthrough 主機套用 GRUB 設定 |
| [`gpu-monitor.yml`](playbooks/gpu-monitor.yml) | `openstack04` | 部署 GPU 溫度監控 script 與 systemd service |
| [`exporter.yml`](playbooks/exporter.yml) | `managed_hosts` | 部署 IPMI exporter container（Prometheus） |
| [`upgrade.yml`](playbooks/upgrade.yml) | `managed_hosts`（rolling，serial: 1） | apt dist-upgrade 並清除未使用套件 |
| [`ceph-audit.yml`](playbooks/ceph-audit.yml) | `ceph_bootstrap` | 唯讀，審計 Ceph 設定與預期狀態差異 |
| [`ceph-apply.yml`](playbooks/ceph-apply.yml) | `ceph_bootstrap`（需 `-e ceph_iac_apply=true`） | 套用 Ceph day-2 設定，含 pre-apply 快照 |
| [`ceph-verify.yml`](playbooks/ceph-verify.yml) | `ceph_bootstrap` | 驗證 Ceph live 狀態與預期一致 |
| [`setup-mariadb-backup.yml`](playbooks/setup-mariadb-backup.yml) | 第一台 controller | 建立 systemd timer：每日 02:00 完整備份、每小時 :30 增量備份 |
| [`setup-cert-renewal.yml`](playbooks/setup-cert-renewal.yml) | deploy host | 建立 certbot + Cloudflare DNS-01 自動續期 timer（每日 00:00 與 12:00） |
| [`deploy-account-automation.yml`](playbooks/deploy-account-automation.yml) | `deploy_host` | 部署 `tools/account_automation` container |

## Role 清單

| Role | 內容 | 文件 |
|---|---|---|
| [`base`](roles/base) | 基礎套件、sysctl、SSH authorized_keys、chrony | `tasks/main.yml` |
| [`network`](roles/network) | bond、VLAN、resolver；逐台 rolling 重啟（間隔 15s） | `tasks/main.yml` |
| [`mail`](roles/mail) | exim4 relay、`passwd.client` | `tasks/main.yml` |
| [`kvm`](roles/kvm) | KVM nested virtualization 設定 | `tasks/main.yml` |
| [`grub`](roles/grub) | GRUB kernel command line 管理（含 PCI passthrough） | `tasks/main.yml` |
| [`swap`](roles/swap) | swapfile 管理 | `tasks/main.yml` |
| [`bbr`](roles/bbr) | BBR congestion control | `tasks/main.yml` |
| [`tuning`](roles/tuning) | NVMe scheduler、THP、NIC ring buffer | [README](roles/tuning/README.md) / [en](roles/tuning/README.en.md) |
| [`openstack05_battlemage`](roles/openstack05_battlemage) | openstack05 專屬 GRUB flag、SR-IOV restore | `tasks/main.yml` |
| [`ceph-bootstrap`](roles/ceph-bootstrap) | Ceph apt repo（bookworm suite, tentacle release）、cephadm 安裝 | `tasks/main.yml` |
| [`ceph-config`](roles/ceph-config) | Ceph day-2 config IaC（audit / apply / verify） | [README](roles/ceph-config/README.md) / [en](roles/ceph-config/README.en.md) |

## 常用 hands-on 指令

開始任何變更前，先執行靜態驗證：

```bash
./scripts/validate.sh
```

對單一主機 dry-run：

```bash
ansible-playbook playbooks/bootstrap.yml --check --diff --limit openstack01
```

對整個 stable fleet dry-run：

```bash
ansible-playbook playbooks/bootstrap.yml --check --diff --limit managed_hosts
```

實際套用（rolling，本 repo 預設行為）：

```bash
ansible-playbook playbooks/bootstrap.yml --limit openstack01
```

Ceph day-2 設定 audit / apply / verify：

```bash
ansible-playbook playbooks/ceph-audit.yml  --limit ceph_bootstrap
ansible-playbook playbooks/ceph-apply.yml  --limit ceph_bootstrap -e ceph_iac_apply=true
ansible-playbook playbooks/ceph-verify.yml --limit ceph_bootstrap
```

## Inventory 慣例

- [`hosts`](hosts) 僅定義 group 成員與 `ansible_host`，不放 per-host facts。
- per-host facts 一律放在 [`host_vars/<hostname>.yml`](host_vars)。新主機至少需要 `node_num`、`interface1`、`interface2`；若由 GRUB role 管理，加上 GRUB list。
- 共用預設值放在 [`group_vars/all.yml`](group_vars/all.yml)，Ceph 相關設定放在 [`group_vars/ceph_cluster.yml`](group_vars/ceph_cluster.yml)。
- `ansible/hosts` 與 [`../kolla/multinode`](../kolla/multinode) 參照相同的實體主機；新增或移除主機時兩個檔案需同步更新（[`tests/`](tests) 會驗證）。

## 機敏檔案

所有機敏檔案放在 `ansible/private/`，已從 git 排除。

執行 `bootstrap.yml`（與其他 fleet-bootstrap 類 playbook）需要：

```
ansible/private/
  authorized_keys             # roles/base 寫入 /home/debian/.ssh/authorized_keys
  passwd.client               # roles/mail 寫入 /etc/exim4/passwd.client
```

執行 tools deploy playbook 才需要的對應檔案：

```
# deploy-account-automation.yml
ansible/private/tools/account_automation/
  .env
  service-account.json
  clouds.yaml
```

> 各工具實際所需的機敏檔案請以對應工具的 README 為準。

## 進一步閱讀

- 完整 apply 流程、Cephadm bookworm-on-trixie 設計理由、首次設定主機流程：[Root README](../README.md)
- Tuning 各 sysctl 設定的設計依據：[`roles/tuning/README.md`](roles/tuning/README.md)
- Ceph day-2 設定的 audit-apply-verify 工作流程：[`roles/ceph-config/README.md`](roles/ceph-config/README.md)
- 全部文件索引：[`docs/README.md`](../docs/README.md)
