# Health Gate Role

[English](README.en.md)

`health-gate` 是擾動性操作（rolling 套件升級、憑證更新、`playbooks/reboot.yml`）
前的 HA 健康閘門。過去 `upgrade.yml` 主機之間唯一的安全機制是盲目的
`pause: 30`；此 role 改在每台主機擾動前做真正的檢查，並且 **fail closed**：
只要無法證明 fleet 可以安全被擾動，就帶著原因中止 play。（`pause: 30` 在
`upgrade.yml` 仍保留，但只作為 apt 之後等容器穩定的等待，不再是安全機制。）
此 role 為唯讀，不會更動 fleet。

30 秒的 sleep 對每個已知的 fleet 地雷都是盲的 —— 卡在 Donor 狀態的 Galera 節點、
單一節點 `cluster_status` 看不到的 RabbitMQ split、不是 `ok-to-stop` 的
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
| **RabbitMQ** | 從 **每個** 節點查詢：無 partition，且 `running_nodes` 等於預期數量。 | 每個節點的 `cluster_status` 只列出它連得到的 peer，所以單一節點的視角可能看起來乾淨，實際已 split。RabbitMQ 4.3（kolla 2026.2）的 `partitions` 永遠是空的，`cluster_partition_handling` 也不再生效，真正抓到 split 的是 `running_nodes` 數量（[ADR-0006](../../../docs/adr/0006-rabbitmq-messaging-topology.md)）。 |
| **Ceph** | `ceph health` 不是 `HEALTH_ERR`（除非 `health_gate_ceph_allow_warn=true`，否則也不接受 `HEALTH_WARN`）；即使 `allow_warn`，只要出現 `health_gate_ceph_blocking_checks`（`MON_DOWN`、`MON_NETSPLIT`、`OSD_DOWN`、`OSD_HOST_DOWN`、`OSD_ROOT_DOWN`、`PG_AVAILABILITY`、`PG_DEGRADED`）就失敗；且 `ceph osd ok-to-stop <目標主機 OSD ids>` 與 `ceph mon ok-to-stop <目標主機 mons>` 都回傳成功。 | 叢集可能因良性原因處於 `HEALTH_WARN`，卻仍不能安全失去某個 OSD 或 monitor。`ok-to-stop` 才是權威答案，單看 health 字串不夠；而籠統的 `allow_warn` 否則也會放過掛掉的 monitor。 |
| **OVN** | 每台預期執行 ovn-controller 的主機（`compute` group）在 OVN SB DB 都有 Chassis row、沒有非預期（殘留）的 row，**且**每台的 ovn-controller daemon 回報 SB 連線狀態為 `connected`（`ovn-appctl -t ovn-controller connection-status`）。 | 殘留 Chassis 會讓邏輯拓樸謊報 port/gateway 的位置；而 row 本身不代表 daemon 活著——死掉的 ovn-controller 會留下它的 row，單看集合比對會誤判健康。 |
| **OVN raft**（`health_gate_ovn_raft_enabled`，OVN 檢查開啟時執行） | 每台 controller 上 `ovn-appctl -t /run/ovn/ovn{nb,sb}_db.ctl cluster/status` 回報 `Status: cluster member`、role 為 leader 或 follower、leader 已知、server 數等於 controller 數，且 `Connections:` 列對每個 peer 都有 incoming 連線 `<-id`（outgoing 連線可能保留 `->0000` 佔位名稱，因此不以 id 比對）。輪詢約 2 分鐘。 | Chassis row 看起來正常時，NB/SB raft 可能已有成員脫隊、選舉卡住，或兩個成員彼此斷線（設定中的 server 不等於已連線）；此時再擾動一台 controller 就會失去 quorum。 |
| **Hazards** | 目標主機沒有未被 acknowledge 的 blocking hazard（`blocks_unattended: true`）。 | 見 [Per-host hazards](#per-host-hazards)。 |

每個遠端指令都有單次嘗試的 `timeout`（`health_gate_command_timeout_seconds`，
預設 300 秒），卡住的 `docker exec` 或 `cephadm shell` 會讓 gate 失敗，而不是讓
play 永遠卡住。ovn-controller 探測會輪詢到 `connected`（約 2 分鐘）才由 assert
判定失敗，讓剛重開機的 chassis 有時間重新連線。

### 檢查在哪裡執行

- Galera 與 RabbitMQ 是 controller 上的 Kolla Docker container；role 會
  `delegate_to` 各 controller 並 `docker exec`。
- Ceph CLI 只存在於 bootstrap 主機的 `cephadm shell` 內
  （`groups['ceph_bootstrap'][0]`）；所有 Ceph 指令都 delegate 到那裡。
- OVN SB 透過第一台 controller 上 `ovn_sb_db` container 內的 `ovn-sbctl` 讀取；
  liveness 探測則 delegate 到每台 compute，在 `ovn_controller` container 內跑
  `ovn-appctl`。raft 檢查 delegate 到每台 controller，在 `ovn_nb_db` 與
  `ovn_sb_db` 內跑 `ovn-appctl cluster/status`。

## Per-host hazards

`host_vars/<host>.yml` 帶有機器可讀的 `host_hazards` 清單 —— fleet 的地雷地圖，
作為資料被讀取，而不是依賴操作者記憶。每筆包含：

- `key`：穩定識別碼（例如 `bond0_boot_failure`）。
- `reason`：人類可讀的危害說明。
- `source`：文件參照（例如 `docs/troubleshooting.md#...`）。
- `blocks_unattended`（選填）：`true` 表示除非 `health_gate_ack_hazards=true`，
  否則閘門拒絕對此主機做無人值守擾動。
- `canary_tier`（選填）：rolling 操作的數字排序，越小越早擾動。
  `playbooks/reboot.yml` 透過 `managed_hosts` 的 inventory 順序使用這份資料，
  structure test 會確認 inventory 順序與 tier 一致。openstack06（純 Ceph）最先；
  openstack01（cephadm bootstrap + mgr + bond0 危害）最後。

目前的 blocking hazard：**openstack01 `bond0_boot_failure`** —— bond0 重開機後不會
自動 up，需要手動 `systemctl restart networking`，因此絕不可無人值守 reboot。

## 覆寫（以及為何危險）

| 覆寫 | 效果 | 危險 |
| --- | --- | --- |
| `health_gate_<name>_enabled=false` | 完全跳過該檢查。 | 你在不知道該子系統是否安全的情況下擾動 fleet。只在子系統確實不在本次範圍時才跳過（例如純 Ceph 維護、無 network 擾動時跳過 OVN）。 |
| `health_gate_ceph_allow_warn=true` | 接受 `HEALTH_WARN` 繼續（預設拒絕），但出現 blocking check（`health_gate_ceph_blocking_checks`）時仍拒絕。 | 只有在你已讀過該 warning 並確認其良性（例如單純的 noout flag）時才設定。`ok-to-stop` 仍會檢查，但 health 字串可能藏著它看不到的問題。 |
| `health_gate_ack_hazards=true` | 越過 blocking hazard 繼續。 | 你要負責復原，例如在 openstack01 上手動把 bond0 拉回來。切勿在無人值守／cron 情境設定。 |
| `health_gate_galera_container` / `health_gate_rabbitmq_container` | 覆寫 container 名稱。 | 名稱錯誤會讓 `docker exec` 失敗，閘門因此 fail closed（安全）。 |

覆寫永遠不會讓 fleet 更安全 —— 它只是壓掉警告。這個閘門存在，正是因為盲目 sleep
已被證明不夠。

## 等待 Galera 收斂

預設情況下，Galera 檢查一讀到不安全狀態就失敗。設定
`health_gate_galera_wait_for_sync: true` 後，會輪詢每個節點直到回報
Synced/Primary/ON 且 cluster size 完整（`health_gate_galera_sync_retries`，
預設每個節點 60 次 x `health_gate_delay_seconds`，節點依序輪詢）。`reboot.yml` 只在重開機後的
重新檢查啟用它，因為重開機的節點重新加入後，其他節點會有幾秒仍回報舊的
cluster size。

## 等待 Ceph 收斂

`health_gate_ceph_wait_for_health: true` 讓 Ceph 檢查輪詢到 health 可接受、
且 `ok-to-stop` 通過為止（`health_gate_ceph_wait_retries`，預設 60 次 x
`health_gate_delay_seconds`），而不是第一次讀到就失敗。network role 在 bond0
重啟後用它（搭配 Galera wait）作為換下一台前的收斂等待；擾動前的 gate 不開啟。

## 認證資訊

此 role 不從 repo 讀取任何 secret。Galera 查詢以 root 認證，密碼來自
`health_gate_galera_password`（預設為 Kolla 的 `database_password`），
在 runtime 由 vault 加密的 passwords 檔提供：

```bash
ansible-playbook ... -e @/path/to/kolla/passwords.yml --ask-vault-pass
```

密碼經 stdin 以 defaults file 傳給 client（不進 argv，`ps` 看不到），
task 亦設 `no_log`。MariaDB 11+ 的 Kolla 映像不含 `mysql` symlink，client 為 `mariadb`。

## 檔案結構

```
ansible/roles/health-gate/
  defaults/main.yml   # toggles、container 名稱、retries、hazard 旋鈕
  tasks/
    main.yml          # 編排（hazards、galera、rabbitmq、ceph、ovn）
    hazards.yml       # 讀取 host_hazards；拒絕 blocking hazard
    galera.yml        # 每個節點 wsrep Synced + cluster size
    rabbitmq.yml      # 每個節點 no partitions + 完整成員
    ceph.yml          # health 可接受 + 目標主機 osd/mon ok-to-stop
    ovn.yml           # chassis liveness、無殘留 chassis
    ovn_raft.yml      # 每台 controller 上的 NB/SB raft cluster/status
```
