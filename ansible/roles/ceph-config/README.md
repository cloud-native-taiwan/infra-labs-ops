# Ceph Config Role

[English](README.en.md)

此 role 管理 Ceph day-2 configuration，透過 audit-apply-verify 工作流程維護 cephadm 部署的 Ceph cluster 設定。所有設定集中於 `group_vars/ceph_cluster.yml`，以結構化資料定義，支援新增（present）與移除（absent）操作。

## 使用方式

### 審計（Audit）

檢視目前 Ceph 設定與預期狀態的差異（唯讀，不做任何變更）：

```bash
cd ansible
ansible-playbook playbooks/ceph-audit.yml --limit ceph_bootstrap
```

### 套用（Apply）

套用設定變更。需明確傳入 `-e ceph_iac_apply=true` 才會執行變更：

```bash
cd ansible
ansible-playbook playbooks/ceph-apply.yml --limit ceph_bootstrap -e ceph_iac_apply=true
```

套用前會自動建立設定快照於 `/var/lib/ceph-iac/snapshots/`。

### 驗證（Verify）

確認 live cluster 狀態與預期設定一致：

```bash
cd ansible
ansible-playbook playbooks/ceph-verify.yml --limit ceph_bootstrap
```

## 管理範圍

- **config entries**：`ceph_config_desired` 清單中的 `ceph config` 設定（network、mon、mgr、OSD memory、BlueStore、scrub、heartbeat、RGW Keystone）
- **cephadm labels**：從 Ansible inventory group 自動衍生（`cephadm_label_map`）
- **service specs**：`ansible/files/cephadm/specs/` 下的 MON、MGR、RGW spec

## 不在管理範圍

- OSD drivegroup specs
- Pool 設定
- CephFS/MDS
- Dashboard 設定
- Ceph bootstrap 生命週期

## 設定檔結構

- `group_vars/ceph_cluster.yml`：設定基準線（desired + audit-only + ignore patterns + label map）
- `roles/ceph-config/defaults/main.yml`：role 預設值（apply gate、snapshot 路徑）
- `files/cephadm/specs/*.yml`：cephadm service spec 檔案

## 檔案結構

```
ansible/roles/ceph-config/
  defaults/main.yml      # ceph_iac_apply, snapshot/staging paths
  tasks/
    main.yml             # full audit-apply-verify cycle
    audit.yml            # config dump + drift detection
    apply.yml            # snapshot + set/rm config entries
    verify.yml           # assert live state matches desired
    labels.yml           # inventory-derived label reconciliation
    specs.yml            # cephadm service spec application
```
