# control-plane-alert-collector

部署一個 per-host 的 systemd timer，定期執行 `control_plane_alert_collector.py`，
把「指標難以準確表達」的兩個控制平面地雷寫成 Prometheus textfile gauge，供
node_exporter 的 `--collector.textfile` 抓取：

- **RabbitMQ 分區 / 成員數**：本 fleet 刻意使用 `partition_handling=ignore`
  （Bug #2126768），少數側節點不會自我暫停，單一節點的 `cluster_status` 會說謊。
  因此每個 controller 都跑這個 collector，各自回報自己看到的
  `running_nodes` 與 `partitions`，再由 PromQL 跨節點彙整（U4）。
- **OVN chassis 失聯 / 殘留**：比對 SB-DB chassis 與預期集合（missing/stale），
  並在每個 chassis 主機本地用 `ovn-appctl` 探測 ovn-controller 的 SB 連線狀態
  （殘留的 chassis row 會掩蓋已死的 ovn-controller，所以連線探測必須在本機跑，
  也因此本 role 部署在 `compute` 群組而非僅 controller）。

邏輯刻意對齊 `roles/health-gate/tasks/{rabbitmq,ovn}.yml`；任何一邊改動時，請對照
`docs/runbooks/control-plane-alerts.md` 的對照表（R10）同步更新。

**Fail-safe**：任一 CLI 失敗或輸出非預期，只會寫出 `*_check_failed 1`，永不寫出
「健康」數值；整個檔案以 temp+rename 原子寫入。Collector 若整個死掉，檔案不再更新，
由 U4 的 collector-staleness 告警偵測。

部署：`ansible-playbook playbooks/setup-control-plane-alert-collector.yml`
（先啟用 `kolla/globals.yml` 的 textfile collector 並重新部署 Kolla）。
離線自我測試：`python3 files/control_plane_alert_collector.py --check`。
