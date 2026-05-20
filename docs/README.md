# 文件索引

[English](README.en.md)

本檔案是整個 repo 所有文件的入口索引。新加入的操作者請從本頁開始。

## 新加入者必讀

| 文件 | 用途 |
|---|---|
| [`quickstart.md`](quickstart.md) | 從零到第一個 dry-run 的 10–20 分鐘路徑（含可複製貼上指令） |
| [`fleet-topology.md`](fleet-topology.md) | 哪台主機跑什麼、群組對照、網路命名、軟體版本 |
| [`glossary.md`](glossary.md) | inventory 群組、OpenStack/Ceph/Kolla/網路名詞 |
| [`troubleshooting.md`](troubleshooting.md) | 新加入者最常遇到的問題與解法 |

## 快速進入點

| 我想做什麼 | 從這裡開始 |
|---|---|
| 第一次操作此 repo | [`quickstart.md`](quickstart.md) |
| 對 fleet 套用 Ansible 變更 | [`ansible/README.md`](../ansible/README.md) |
| 管理 OpenStack 帳號 | [`tools/account_automation/README.md`](../tools/account_automation/README.md) |
| 微調 Ceph day-2 設定 | [`ansible/roles/ceph-config/README.md`](../ansible/roles/ceph-config/README.md) |
| 微調主機效能 | [`ansible/roles/tuning/README.md`](../ansible/roles/tuning/README.md) |
| 處理 OVN floating IP 偏移 | [`ovn-floating-ip-runbook.md`](ovn-floating-ip-runbook.md) |
| 建置自訂 Neutron container image | [`neutron-kolla-custom-image-guide.md`](neutron-kolla-custom-image-guide.md) |

## 子元件 README

每個子元件都有自己的 README，內含 quickstart 與 hands-on 操作指令：

| 子元件 | 內容 | README |
|---|---|---|
| Root | repo 總覽、前置需求、playbook apply 流程 | [README.md](../README.md) / [README.en.md](../README.en.md) |
| `ansible/` | playbook 清單、role 結構、inventory 慣例 | [README.md](../ansible/README.md) / [README.en.md](../ansible/README.en.md) |
| `ansible/roles/tuning/` | sysctl、NVMe scheduler、THP、NIC ring buffer 調校 | [README.md](../ansible/roles/tuning/README.md) / [README.en.md](../ansible/roles/tuning/README.en.md) |
| `ansible/roles/ceph-config/` | Ceph day-2 設定（audit / apply / verify） | [README.md](../ansible/roles/ceph-config/README.md) / [README.en.md](../ansible/roles/ceph-config/README.en.md) |
| `tools/` | 工具目錄結構、deploy 慣例、機敏檔案位置 | [README.md](../tools/README.md) / [README.en.md](../tools/README.en.md) |
| `tools/account_automation/` | OpenStack 帳號生命週期自動化 | [README.md](../tools/account_automation/README.md) / [README.en.md](../tools/account_automation/README.en.md) |

未獨立提供 README 的 Ansible role（`base`、`bbr`、`ceph-bootstrap`、`grub`、`kvm`、`mail`、`network`、`openstack05_battlemage`、`swap`）請直接閱讀對應的 `roles/<name>/tasks/main.yml`，或參考 root README 的相關章節。

## 依用途分類

### 操作 runbook 與調查紀錄

實際發生過、未來可能再用到的處理流程或現場記錄。

- [`ovn-floating-ip-runbook.md`](ovn-floating-ip-runbook.md) — OVN floating IP 偏移時的偵測與修復流程。
- [`openstack04-numa-oom.md`](openstack04-numa-oom.md) — openstack04 NUMA OOM 事件調查紀錄。
- [`neutron-kolla-custom-image-guide.md`](neutron-kolla-custom-image-guide.md) — 建置與部署自訂 Neutron container image 的步驟。

### 對外公告

`announcements/` 為對使用者公告的草稿。

- [`announcements/2026-05-06-aup-update-and-service-info.md`](announcements/2026-05-06-aup-update-and-service-info.md)

### 事後分析報告

部署後的調校或事件分析報告，存放於 `reports/`。

- [`../reports/ceph-vm-storage-tuning-2026-05-08.md`](../reports/ceph-vm-storage-tuning-2026-05-08.md) — Ceph VM 儲存效能調校的完整 FIO benchmark 與結論。

## 文件慣例

- 中文為主、英文為輔。Repo 範圍的文件採用 `README.md`（中文） + `README.en.md`（英文）的命名。
- 日期格式：檔名前綴 `YYYY-MM-DD-`。計畫類文件再加上序號（例：`2026-05-07-001-...`）。
- `announcements/`、`reports/` 內的文件預設為時間點快照，**完成後不修改原檔**；如有後續更新，請新增新檔案而非覆寫舊檔。
