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
| [`bootstrap.yml`](playbooks/bootstrap.yml) | `managed_hosts`、`ceph_bootstrap` | 主要進入點。套用 base / network / mail / kvm / grub / swap / bbr / tuning，並準備 cephadm。network role 在獨立的 `serial: 1` play 中依 inventory 順序執行；其他 role 照常平行執行 |
| [`apply-tuning.yml`](playbooks/apply-tuning.yml) | `managed_hosts`（rolling，serial: 1） | 僅套用 sysctl 與主機效能調校，不執行完整 bootstrap |
| [`verify-tuning.yml`](playbooks/verify-tuning.yml) | `managed_hosts` | 唯讀，驗證 tuning 設定是否與預期相符 |
| [`bbr.yml`](playbooks/bbr.yml) | `managed_hosts` | 單獨套用 BBR 設定 |
| [`swap.yml`](playbooks/swap.yml) | `managed_hosts` | 單獨管理 swapfile |
| [`pci-pass.yml`](playbooks/pci-pass.yml) | `pci_pass` | 對 PCI passthrough 主機套用 GRUB 設定 |
| [`gpu-monitor.yml`](playbooks/gpu-monitor.yml) | `openstack04` | 部署 GPU 溫度監控 script 與 systemd service |
| [`exporter.yml`](playbooks/exporter.yml) | `managed_hosts` | 部署 IPMI exporter container（Prometheus） |
| [`upgrade.yml`](playbooks/upgrade.yml) | `managed_hosts`（rolling，serial: 1） | apt dist-upgrade 並清除未使用套件；每台主機前先跑 `health-gate` HA 健康閘門 |
| [`ceph-audit.yml`](playbooks/ceph-audit.yml) | `ceph_bootstrap` | 唯讀，審計 Ceph 設定與預期狀態差異 |
| [`ceph-apply.yml`](playbooks/ceph-apply.yml) | `ceph_bootstrap`（需 `-e ceph_iac_apply=true`） | 套用 Ceph day-2 設定，含 pre-apply 快照 |
| [`ceph-verify.yml`](playbooks/ceph-verify.yml) | `ceph_bootstrap` | 驗證 Ceph live 狀態與預期一致 |
| [`setup-mariadb-backup.yml`](playbooks/setup-mariadb-backup.yml) | deploy host | 建立 systemd timer：每週日 02:00 完整備份、週一至週六 02:00 增量備份（以 deploy 使用者執行 `kolla-ansible mariadb-backup`）；每次成功執行會記錄時間戳供 `DeployHostTaskStale` 告警使用 |
| [`setup-cert-renewal.yml`](playbooks/setup-cert-renewal.yml) | deploy host | 建立 certbot + Cloudflare DNS-01 自動續期 timer（每日 00:00 與 12:00），續期後各自獨立更新邊緣 HAProxy、Harbor 與 kolla fleet（其中一項失敗不會跳過其他項，但整次執行判定失敗）；成功執行會記錄時間戳供 `DeployHostTaskStale` 告警使用 |
| [`setup-control-plane-alert-collector.yml`](playbooks/setup-control-plane-alert-collector.yml) | `compute` | 部署控制平面地雷告警 collector（per-host systemd timer，RabbitMQ 分區 / OVN chassis）寫入 node_exporter textfile |
| [`deploy-haproxy.yml`](playbooks/deploy-haproxy.yml) | `deploy_host` | 部署 HAProxy edge reverse proxy（不管理或移除 NGINX） |
| [`deploy-account-automation.yml`](playbooks/deploy-account-automation.yml) | `deploy_host` | 部署 `tools/account_automation` container |

## Role 清單

| Role | 內容 | 文件 |
|---|---|---|
| [`base`](roles/base) | 基礎套件、sysctl、SSH authorized_keys、chrony | `tasks/main.yml` |
| [`network`](roles/network) | bond、VLAN、resolver。bond0 有待套用變更時逐台 rolling：重啟前跑 `health-gate`，重啟後等 Galera/RabbitMQ/Ceph/OVN 收斂才換下一台；沒有變更的主機跳過 gate。若 play batch 超過一台會拒絕重啟。僅對尚未運行 OpenStack/Ceph 的主機使用 `-e network_health_gate_enabled=false` | `tasks/main.yml` |
| [`mail`](roles/mail) | exim4 relay、`passwd.client` | `tasks/main.yml` |
| [`kvm`](roles/kvm) | KVM nested virtualization 設定 | `tasks/main.yml` |
| [`grub`](roles/grub) | GRUB kernel command line 管理（含 PCI passthrough） | `tasks/main.yml` |
| [`swap`](roles/swap) | swapfile 管理 | `tasks/main.yml` |
| [`bbr`](roles/bbr) | BBR congestion control | `tasks/main.yml` |
| [`tuning`](roles/tuning) | NVMe scheduler、THP、NIC ring buffer | [README](roles/tuning/README.md) / [en](roles/tuning/README.en.md) |
| [`openstack05_battlemage`](roles/openstack05_battlemage) | openstack05 專屬 backports kernel APT pin、GRUB flag、SR-IOV restore | `tasks/main.yml` |
| [`ceph-bootstrap`](roles/ceph-bootstrap) | Ceph apt repo（bookworm suite, tentacle release）、cephadm 安裝 | `tasks/main.yml` |
| [`ceph-config`](roles/ceph-config) | Ceph day-2 config IaC（audit / apply / verify） | [README](roles/ceph-config/README.md) / [en](roles/ceph-config/README.en.md) |
| [`health-gate`](roles/health-gate) | 擾動性操作前的 HA 健康閘門（Galera/RabbitMQ/Ceph/OVN/hazards，fail closed） | [README](roles/health-gate/README.md) / [en](roles/health-gate/README.en.md) |
| [`control-plane-alert-collector`](roles/control-plane-alert-collector) | 控制平面地雷告警 collector（RabbitMQ 分區 / OVN chassis），per-host systemd timer，鏡像 health-gate 述詞 | [README](roles/control-plane-alert-collector/README.md) / [en](roles/control-plane-alert-collector/README.en.md) |
| [`haproxy`](roles/haproxy) | HAProxy edge proxy、TLS PEM bundle、Harbor routing | [README](roles/haproxy/README.md) |
| [`deploy_host_textfile`](roles/deploy_host_textfile) | deploy host 的 textfile-only node_exporter（綁內部位址）、供 systemd unit 以 `ExecStartPost=+` 呼叫的 `deploy-host-task-success` 記錄器，並將各 task 的成功指標預設為 0（從未成功）。所有會輸出指標的 deploy host playbook 都會套用；告警在 `kolla/config/prometheus/deploy-host-alerts.rules` | `defaults/main.yml`, `vars/main.yml` |

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

若本次執行會變更任何主機的 `bond0`，network gate 需要 Kolla 的 Galera 密碼
（`-e @/path/to/kolla/passwords.yml --ask-vault-pass`）；openstack01 另需
`-e health_gate_ack_hazards=true` 且有人在 console 前待命（`bond0_boot_failure`
hazard）。`--check` 執行會跳過 gate。

Ceph day-2 設定 audit / apply / verify：

```bash
ansible-playbook playbooks/ceph-audit.yml  --limit ceph_bootstrap
ansible-playbook playbooks/ceph-apply.yml  --limit ceph_bootstrap -e ceph_iac_apply=true
ansible-playbook playbooks/ceph-verify.yml --limit ceph_bootstrap
```

部署 HAProxy edge proxy：

```bash
ansible-playbook playbooks/deploy-haproxy.yml --check --diff --limit deploy01
ansible-playbook playbooks/deploy-haproxy.yml --limit deploy01
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
