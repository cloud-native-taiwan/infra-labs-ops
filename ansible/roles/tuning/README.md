# Tuning Role

[English](README.en.md)

此 role 負責主機層級的效能調校，針對 AMD NUMA 架構、NVMe 儲存、Ceph 超融合部署、OVN 網路進行最佳化設定。所有設定皆可即時生效，無需重新開機，並透過 udev rule、tmpfiles.d、if-up.d script 持久化。

## 套用方式

此 role 包含於 `bootstrap.yml`，新主機在 bootstrap 時會自動套用。

針對既有主機，可單獨套用 tuning（每次一台，rolling apply）：

```bash
cd ansible
ansible-playbook playbooks/apply-tuning.yml
```

驗證所有設定是否正確（唯讀，不做任何變更）：

```bash
cd ansible
ansible-playbook playbooks/verify-tuning.yml
```

## Sysctl 設定

以下設定定義於 `group_vars/all.yml` 的 `sysctl_config` 中，由 `roles/base` 部署至 `/etc/sysctl.d/99-infra-labs.conf`。

### vm.zone_reclaim_mode = 0

**問題：** AMD CPU 為多 NUMA 節點架構。預設行為下，當本地 NUMA 節點記憶體不足時，kernel 會優先回收本地的 page cache，而非從遠端 NUMA 節點分配記憶體。

**影響：** Ceph OSD 的 BlueStore cache 和 QEMU 的 disk cache 會被反覆清除再重新從 NVMe 讀取，導致隨機的延遲尖峰（tail latency），原本應 <1ms 的操作可能飆升至 10-50ms。

**設定值 0 的效果：** 允許跨 NUMA 節點分配記憶體。遠端節點的存取延遲約 100ns（本地約 80ns），遠低於清除 cache 後重新讀取的成本。

**適用場景：** 所有執行 Ceph 或資料庫的多 NUMA 節點系統。Red Hat、SUSE、Ceph 官方文件皆建議此設定。

### vm.max_map_count = 524288

**問題：** Ceph BlueStore 大量使用 `mmap()` 存取 RocksDB metadata。每個 OSD process 會使用數千個 memory mapping。

**預設值：** 65530。

**風險：** 當 4+ 個 OSD 加上 QEMU process 同時運行時，可能耗盡 mapping 上限。此時 `mmap()` 會失敗，Ceph OSD crash 並顯示令人困惑的 "Cannot allocate memory" 錯誤（實際上 RAM 仍有餘量）。

**設定值 524288：** 提供充足的 headroom，避免 mapping 耗盡。這是安全閥設定，非效能調校。

### vm.overcommit_memory = 1

**問題：** Nova scheduler 計算記憶體分配比率，QEMU process 啟動時會預先申請大量記憶體。

**預設值 0（啟發式模式）：** Kernel 使用內部啟發式判斷是否有「足夠」記憶體，可能在實際有可用 RAM 時拒絕分配。

**設定值 1：** 始終允許分配請求，信任 Nova scheduler 已完成計算。真正的記憶體耗盡由 OOM killer 處理。

**例外：** `openstack06` 為純 Ceph 節點（無 Nova/QEMU），維持 `vm.overcommit_memory=0`，避免 Ceph daemon 因記憶體壓力被 OOM kill。此覆寫設定於 `host_vars/openstack06.yml` 的 `host_sysctl_config` 中。

### net.netfilter.nf_conntrack_max = 524288

**問題：** OVN 使用 conntrack 實作 security group 規則與 NAT。每個 VM 的網路連線都會建立一筆 conntrack entry。

**預設值：** 通常為 65536。

**風險：** 多個 VM 的 web server 可能產生數千筆 entry。當 conntrack table 滿載時，新連線會**被靜默丟棄**，VM 端只會看到 timeout，無任何錯誤訊息。這是 OpenStack 網路中最難除錯的問題之一。

**相關 timeout 設定：**
- `nf_conntrack_tcp_timeout_established: 1800`（30 分鐘，預設 5 天太長）
- `nf_conntrack_tcp_timeout_time_wait: 30`（30 秒，加速清除已結束的連線）

### fs.file-max = 2097152

**問題：** 單一主機運行 30+ OpenStack container、Ceph OSD、QEMU process，peak load 時可能接近預設的 file descriptor 上限（約 100 萬）。

### fs.inotify.max_user_instances = 8192 / max_user_watches = 1048576

**問題：** 容器化服務使用 inotify 監控設定檔變更。大量 container 需要大量 inotify instance。預設值（128 instances、8192 watches）對於運行 30+ OpenStack container 的節點過低。

## NVMe I/O 調校

設定檔：`tasks/nvme.yml`、`files/99-nvme-ceph.rules`

### I/O Scheduler: none

**問題：** Linux 預設的 I/O scheduler（如 `mq-deadline`）會重新排序與合併 I/O 請求，為傳統硬碟或 SATA SSD 最佳化。

**為何設為 none：** NVMe 裝置有自己的多佇列控制器，內建高效的調度邏輯。Kernel 的 scheduler 只會增加不必要的 CPU 開銷與延遲。設為 `none` 直接將請求傳遞給硬體。

### Read-ahead: 0

**預設值：** 128 KB。

**問題：** Read-ahead 讓 kernel 預讀超出請求範圍的資料，預期應用程式會循序讀取。Ceph RADOS I/O 以隨機存取為主（4K-4M 的 object 分散於 OSD 上）。預讀不會被使用的資料浪費 NVMe 頻寬，並污染 page cache。

**持久化方式：** 即時設定透過寫入 `/sys/block/nvme*/queue/` 生效，重開機透過 udev rule（`/etc/udev/rules.d/99-nvme-ceph.rules`）持久化。

**注意：** 若主機無 NVMe 裝置，相關 task 會自動跳過。

## Transparent Hugepages (THP)

設定檔：`tasks/thp.yml`、`files/99-thp.conf`

### enabled = madvise

**背景：** 普通記憶體 page 為 4 KB，Hugepage 為 2 MB。使用 hugepage 減少 page table entry，降低 TLB miss，提升大型工作負載的記憶體存取效能。

**預設值 `always` 的問題：** Kernel 自動嘗試為所有分配使用 2 MB hugepage。當找不到連續 2 MB 區域時，會觸發**同步 compaction**，移動大量 page 以建立連續空間。在繁忙的超融合節點上，這會造成隨機的毫秒級停頓。

**設定值 `madvise`：** 僅有明確請求 hugepage 的 process（透過 `madvise(MADV_HUGEPAGE)`）才會使用。Ceph 和 QEMU 都知道在適當時機請求。其餘 process 使用普通 4 KB page，永不觸發 compaction。

### defrag = defer+madvise

**效果：** 即使請求 hugepage 的 process，也將 compaction 延遲至背景 kernel thread 執行，而非阻塞 process。兩全其美：有 hugepage 可用時使用，沒有時不停頓。

**持久化方式：** 即時設定透過寫入 `/sys/kernel/mm/transparent_hugepage/` 生效，重開機透過 tmpfiles.d（`/etc/tmpfiles.d/99-thp.conf`）持久化。

## NIC Ring Buffer 調校

設定檔：`tasks/ring_buffers.yml`、`templates/ring-buffers.j2`

### 最大化 RX/TX Ring Buffer

**問題：** 多數 25 GbE NIC 的預設 ring buffer 設定較保守（通常 256-512）。在突發流量下，較小的 ring buffer 可能導致封包被丟棄。

**做法：** 透過 `ethtool -g` 查詢各 NIC 支援的最大 RX/TX ring buffer 值，再以 `ethtool -G` 設定至最大值。在 mlx5_core driver（常見 25 GbE）上，變更 ring buffer 不會導致 NIC reset。

**持久化方式：** 部署 `/etc/network/if-up.d/ring-buffers` script，在 bond slave interface 啟動時自動設定最大 ring buffer。使用 `interface1` 和 `interface2` 變數（定義於各主機的 `host_vars/`）。

**錯誤處理：** 若 driver 不支援 ring buffer 變更，task 會顯示警告而非靜默失敗。

## Ceph OSD 記憶體設定

Ceph OSD memory 管理已移至 [`ceph-config`](../ceph-config/README.md) role。

## 檔案結構

```
ansible/roles/tuning/
  tasks/
    main.yml            # 引入各子 task
    nvme.yml            # NVMe I/O scheduler + readahead
    thp.yml             # Transparent Hugepages
    ring_buffers.yml    # NIC ring buffer 最大化
  files/
    99-nvme-ceph.rules  # NVMe udev rule（重開機持久化）
    99-thp.conf         # THP tmpfiles.d（重開機持久化）
  templates/
    ring-buffers.j2     # if-up.d script template（重開機持久化）
```
