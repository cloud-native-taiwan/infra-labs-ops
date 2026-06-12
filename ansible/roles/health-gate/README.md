# Health Gate Role

[English](README.en.md)

`health-gate` 是擾動性操作（rolling 套件升級、憑證更新、未來的 rolling-reboot
play）前的 HA 健康閘門。過去 `upgrade.yml` 主機之間唯一的安全機制是盲目的
`pause: 30`；此 role 改在每台主機擾動前做真正的檢查，並且 **fail closed**：
只要無法證明 fleet 可以安全被擾動，就帶著原因中止 play。（`pause: 30` 在
`upgrade.yml` 仍保留，但只作為 apt 之後等容器穩定的等待，不再是安全機制。）
此 role 為唯讀，不會更動 fleet。

30 秒的 sleep 對每個已知的 fleet 地雷都是盲的 —— 卡在 Donor 狀態的 Galera 節點、
被 `partition_handling=ignore` 遮蔽的 RabbitMQ split-brain、不是 `ok-to-stop` 的
OSD、殘留的 OVN Chassis。這個 role 會在擾動發生前逐一檢查。

## 用法

在擾動主機之前 include，搭配 `serial: 1` 一次只對一台主機把關並擾動：

```yaml
- name: Run HA health gate before disrupting this host
  ansible.builtin.include_role:
    name: health-gate
  vars:
    health_gate_target_host: "{{ inventory_hostname }}"
```

`upgrade.yml` 已接好。若要在無人值守下對帶有 blocking hazard（見下）的主機跑升級，
必須明確 acknowledge：

```bash
cd ansible
ansible-playbook playbooks/upgrade.yml -e health_gate_ack_hazards=true
```

## 檢查項目

所有檢查預設開啟，且可個別關閉（`health_gate_<name>_enabled: false`）。關閉是明確
且會被記錄的決定。

| 檢查 | 「健康」的定義 | 為什麼盲目 sleep 看不到 |
| --- | --- | --- |
| **Galera** | 每個 controller 節點都回報 `wsrep_local_state_comment = Synced`、`wsrep_cluster_status = Primary`、`wsrep_ready = ON`，且 `wsrep_cluster_size` 等於預期成員數。 | Donor/Desync/Joining 節點仍會回應 TCP。某節點是 Donor 時再掉一台，可能破壞寫入 quorum。 |
| **RabbitMQ** | 從 **每個** 節點查詢：無 partition，且 `running_nodes` 等於預期數量。 | 本 fleet 刻意採 `partition_handling=ignore`（Bug #2126768、`stream_fanout=false`）。`ignore` 代表被切割的節點不會自我暫停，所以單一節點的 `cluster_status` 可能看起來乾淨，實際已 split。 |
| **Ceph** | `ceph health` 不是 `HEALTH_ERR`（除非 `health_gate_ceph_allow_warn=true`，否則也不接受 `HEALTH_WARN`），且 `ceph osd ok-to-stop <目標主機 OSD ids>` 回傳成功。 | 叢集可能因良性原因處於 `HEALTH_WARN`，卻仍不能安全失去某個 OSD。`ok-to-stop` 才是權威答案，單看 health 字串不夠。 |
| **OVN** | 每台預期執行 ovn-controller 的主機（`compute` group）在 OVN SB DB 都有 Chassis row、沒有非預期（殘留）的 row，**且**每台的 ovn-controller daemon 回報 SB 連線狀態為 `connected`（`ovn-appctl -t ovn-controller connection-status`）。 | 殘留 Chassis 會讓邏輯拓樸謊報 port/gateway 的位置；而 row 本身不代表 daemon 活著——死掉的 ovn-controller 會留下它的 row，單看集合比對會誤判健康。 |
| **Hazards** | 目標主機沒有未被 acknowledge 的 blocking hazard（`blocks_unattended: true`）。 | 見 [Per-host hazards](#per-host-hazards)。 |

### 檢查在哪裡執行

- Galera 與 RabbitMQ 是 controller 上的 Kolla Docker container；role 會
  `delegate_to` 各 controller 並 `docker exec`。
- Ceph CLI 只存在於 bootstrap 主機的 `cephadm shell` 內
  （`groups['ceph_bootstrap'][0]`）；所有 Ceph 指令都 delegate 到那裡。
- OVN SB 透過第一台 controller 上 `ovn_sb_db` container 內的 `ovn-sbctl` 讀取；
  liveness 探測則 delegate 到每台 compute，在 `ovn_controller` container 內跑
  `ovn-appctl`。

## Per-host hazards

`host_vars/<host>.yml` 帶有機器可讀的 `host_hazards` 清單 —— fleet 的地雷地圖，
作為資料被讀取，而不是依賴操作者記憶。每筆包含：

- `key`：穩定識別碼（例如 `bond0_boot_failure`）。
- `reason`：人類可讀的危害說明。
- `source`：文件參照（例如 `docs/troubleshooting.md#...`）。
- `blocks_unattended`（選填）：`true` 表示除非 `health_gate_ack_hazards=true`，
  否則閘門拒絕對此主機做無人值守擾動。
- `canary_tier`（選填）：rolling 操作的數字排序，越小越早擾動。openstack06（純
  Ceph）最先；openstack01（cephadm bootstrap + mgr + bond0 危害）最後。目前還
  沒有任何 play 讀取它：這是留給未來 rolling-reboot play 的文件化資料（structure
  test 會維持其一致性），今天的 `upgrade.yml` 仍按 inventory 順序進行。

目前的 blocking hazard：**openstack01 `bond0_boot_failure`** —— bond0 重開機後不會
自動 up，需要手動 `systemctl restart networking`，因此絕不可無人值守 reboot。

## 覆寫（以及為何危險）

| 覆寫 | 效果 | 危險 |
| --- | --- | --- |
| `health_gate_<name>_enabled=false` | 完全跳過該檢查。 | 你在不知道該子系統是否安全的情況下擾動 fleet。只在子系統確實不在本次範圍時才跳過（例如純 Ceph 維護、無 network 擾動時跳過 OVN）。 |
| `health_gate_ceph_allow_warn=true` | 接受 `HEALTH_WARN` 繼續（預設拒絕）。 | 只有在你已讀過該 warning 並確認其良性（例如單純的 noout flag）時才設定。`ok-to-stop` 仍會檢查，但 health 字串可能藏著它看不到的問題。 |
| `health_gate_ack_hazards=true` | 越過 blocking hazard 繼續。 | 你要負責復原，例如在 openstack01 上手動把 bond0 拉回來。切勿在無人值守／cron 情境設定。 |
| `health_gate_galera_container` / `health_gate_rabbitmq_container` | 覆寫 container 名稱。 | 名稱錯誤會讓 `docker exec` 失敗，閘門因此 fail closed（安全）。 |

覆寫永遠不會讓 fleet 更安全 —— 它只是壓掉警告。這個閘門存在，正是因為盲目 sleep
已被證明不夠。

## 認證資訊

此 role 不從 repo 讀取任何 secret。Galera 查詢在 `mariadb` container 內，透過 Kolla
設定的本地 unix-socket root 登入（`mysql` 無密碼）執行。若 fleet 停用 socket auth，
請在 runtime 提供帶認證的指令 —— 切勿把 secret commit 進 repo。

## 檔案結構

```
ansible/roles/health-gate/
  defaults/main.yml   # toggles、container 名稱、retries、hazard 旋鈕
  tasks/
    main.yml          # 編排（hazards、galera、rabbitmq、ceph、ovn）
    hazards.yml       # 讀取 host_hazards；拒絕 blocking hazard
    galera.yml        # 每個節點 wsrep Synced + cluster size
    rabbitmq.yml      # 每個節點 no partitions + 完整成員
    ceph.yml          # health 可接受 + 目標主機 OSD ok-to-stop
    ovn.yml           # chassis liveness、無殘留 chassis
```
