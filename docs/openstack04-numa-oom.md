# openstack04 明明還有 60 GiB 記憶體，為什麼還是 OOM kill VM？

最近在看監控時，發現 `openstack04` 明明還有幾十 GiB 的 free memory，但 kernel 仍然在 OOM kill VM。  
一開始我也覺得很奇怪，因為從 `free -h` 來看，這台機器的記憶體看起來根本還沒用完。

本篇文章記錄這次實際的排查過程，以及最後找到的 root cause。結論先講：這不是整台主機真的沒記憶體，而是 **NUMA node 0 的本地記憶體先被用完了**。

## 目錄

- 問題現象
- 第一個線索：不是 host-wide OOM
- 第二個線索：`openstack04` 其實是 4 個 host NUMA node
- 第三個線索：VM 被綁定在單一 host NUMA node
- 為什麼 `openstack05` 沒有同樣的問題
- 實際修正與驗證
- 這次排查的結論
- Reference

## 問題現象

先看 `openstack04` 當時的記憶體使用狀況：

```bash
ssh debian@192.168.0.24 'free -h'
```

輸出如下：

```text
               total        used        free      shared  buff/cache   available
Mem:           251Gi       110Gi        56Gi        23Mi        89Gi       140Gi
Swap:          8.0Gi       4.8Gi       3.2Gi
```

從這個數字來看，確實很容易直覺認為「這台機器應該不至於 OOM」。

但實際上 kernel log 已經在 OOM kill QEMU：

```bash
ssh debian@192.168.0.24 \
  'sudo journalctl -k --since "-7 days" | egrep -i "out of memory|oom-kill|killed process|memory cgroup out of memory" | tail -n 20'
```

其中幾筆關鍵訊息如下：

```text
oom-kill:constraint=CONSTRAINT_CPUSET,nodemask=(null),cpuset=emulator,mems_allowed=0,global_oom,task=qemu-system-x86
Out of memory: Killed process 886679 (qemu-system-x86) total-vm:19254864kB, anon-rss:16477484kB

oom-kill:constraint=CONSTRAINT_MEMORY_POLICY,nodemask=0,cpuset=vcpu1,mems_allowed=0,global_oom,task=qemu-system-x86
Out of memory: Killed process 27410 (qemu-system-x86) total-vm:19241588kB, anon-rss:14235148kB
```

這裡最重要的不是 `Killed process`，而是：

- `constraint=CONSTRAINT_CPUSET`
- `constraint=CONSTRAINT_MEMORY_POLICY`
- `mems_allowed=0`
- `nodemask=0`

看到這幾個欄位時，其實方向就已經很明確了。這不是整台主機的記憶體都耗盡，而是某個 NUMA 節點的可用記憶體先沒了。

## 第一個線索：不是 host-wide OOM

如果只是單純看 `/proc/meminfo`，也會發現一件事：整台機器的記憶體其實還有餘裕。

```bash
ssh debian@192.168.0.24 \
  'grep -E "^(MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|CommitLimit|Committed_AS):" /proc/meminfo'
```

當時看到的是：

```text
MemTotal:       263722508 kB
MemFree:        58896352 kB
MemAvailable:   147518492 kB
SwapTotal:       8388604 kB
SwapFree:        3393360 kB
CommitLimit:    140249856 kB
Committed_AS:   304837192 kB
```

這裡有兩件事情值得注意：

1. `MemAvailable` 還有約 `140 GiB`
2. `Committed_AS` 已經遠大於 `CommitLimit`

`openstack04` 本身又有設定：

```bash
ssh debian@192.168.0.24 \
  'cat /proc/sys/vm/overcommit_memory /proc/sys/vm/overcommit_ratio /proc/sys/vm/zone_reclaim_mode'
```

輸出為：

```text
1
50
0
```

也就是說這台 compute host 是刻意開啟 `vm.overcommit_memory=1` 的。  
這在 OpenStack compute host 上其實不奇怪，因為 Nova 會做自己的記憶體排程，不需要 Linux 用 heuristic 先擋掉配置請求。

所以這裡的重點不是「為什麼 overcommit 了」，而是「為什麼還有很多記憶體可用，但某些 VM 還是被 kill」。

## 第二個線索：`openstack04` 其實是 4 個 host NUMA node

原本我以為 `openstack04` 應該是 `NPS1`，但實際查出來不是。

先看 `lscpu`：

```bash
ssh debian@192.168.0.24 \
  'sudo lscpu | egrep "Model name|Socket\(s\)|Core\(s\) per socket|Thread\(s\) per core|NUMA node\(s\)|NUMA node[0-9] CPU\(s\)"'
```

輸出如下：

```text
Model name:          AMD EPYC 7413 24-Core Processor
Thread(s) per core:  2
Core(s) per socket:  24
Socket(s):           1
NUMA node(s):        4
NUMA node0 CPU(s):   0-5,24-29
NUMA node1 CPU(s):   6-11,30-35
NUMA node2 CPU(s):   12-17,36-41
NUMA node3 CPU(s):   18-23,42-47
```

這台機器只有一顆 CPU，但這顆 AMD EPYC 7413 被切成了 `4` 個 host NUMA node。  
換句話說，雖然整台機器總共有約 `256 GiB` RAM，但對於要求 strict NUMA locality 的 VM 來說，真正能用的是單一 NUMA node 上的記憶體。

再看 `numactl -H`：

```bash
ssh debian@192.168.0.24 'sudo numactl -H'
```

當時的輸出重點如下：

```text
available: 4 nodes (0-3)
node 0 size: 64033 MB
node 0 free: 2982 MB
node 1 size: 64508 MB
node 1 free: 10009 MB
node 2 size: 64508 MB
node 2 free: 18820 MB
node 3 size: 64490 MB
node 3 free: 25339 MB
```

這就很清楚了：

- host overall 還有很多 free memory
- 但 **node 0 只剩下大約 3 GiB**
- node 1, 2, 3 其實都還有不少空間

所以這不是整台主機沒 RAM，而是 node 0 的 local memory 先耗盡了。

## 第三個線索：VM 被綁定在單一 host NUMA node

接下來要確認的是：這些被 kill 的 VM，到底有沒有真的被綁到某個固定的 NUMA node。

由於 `virsh` 在 `nova_libvirt` container 內，所以直接從 container 裡看 libvirt XML：

```bash
ssh debian@192.168.0.24 \
  "sudo docker exec nova_libvirt sh -lc 'for d in \$(virsh list --name); do echo == \$d ==; virsh dumpxml \$d | egrep -n \"<vcpu|<numatune|<memory mode=|<emulatorpin|<vcpupin|cpuset=|nodeset=\"; done'"
```

其中一台被放在 node 0 的 VM 片段如下：

```xml
<memory unit='KiB'>16777216</memory>
<vcpu placement='static'>8</vcpu>
<cputune>
  <vcpupin vcpu='0' cpuset='0-5,24-29'/>
  <vcpupin vcpu='1' cpuset='0-5,24-29'/>
  <vcpupin vcpu='2' cpuset='0-5,24-29'/>
  <vcpupin vcpu='3' cpuset='0-5,24-29'/>
  <vcpupin vcpu='4' cpuset='0-5,24-29'/>
  <vcpupin vcpu='5' cpuset='0-5,24-29'/>
  <vcpupin vcpu='6' cpuset='0-5,24-29'/>
  <vcpupin vcpu='7' cpuset='0-5,24-29'/>
  <emulatorpin cpuset='0-5,24-29'/>
</cputune>
<numatune>
  <memory mode='strict' nodeset='0'/>
  <memnode cellid='0' mode='strict' nodeset='0'/>
</numatune>
```

這段設定的意思很直接：

- vCPU 全部 pinned 在 host NUMA node 0 的 CPU 上
- emulator thread 也 pinned 在 node 0
- 記憶體配置是 `strict`，只能從 node 0 分配

再對照 host 上 QEMU process 的狀態：

```bash
ssh debian@192.168.0.24 \
  'for p in 27821 1316223 26487 28743; do echo "== PID $p =="; sudo cat /proc/$p/status | egrep "Name|Cpus_allowed_list|Mems_allowed_list"; done'
```

看到的是：

```text
== PID 27821 ==
Name:	qemu-system-x86
Cpus_allowed_list:	0-5,24-29
Mems_allowed_list:	0

== PID 1316223 ==
Name:	qemu-system-x86
Cpus_allowed_list:	0-5,24-29
Mems_allowed_list:	0
```

這就完全對上前面的 OOM log 了。  
kernel 並不是亂殺 VM，而是在 node 0 已經沒有足夠本地記憶體時，把只能用 node 0 記憶體的 QEMU process kill 掉。

## 為什麼 `openstack05` 沒有同樣的問題

後來我也順手看了 `openstack05` 的拓樸：

```bash
ssh debian@192.168.0.25 \
  'sudo lscpu | egrep "Model name|Socket\(s\)|Core\(s\) per socket|Thread\(s\) per core|NUMA node\(s\)|NUMA node[0-9] CPU\(s\)"'
```

輸出如下：

```text
Model name:          AMD EPYC 7413 24-Core Processor
Thread(s) per core:  2
Core(s) per socket:  24
Socket(s):           1
NUMA node(s):        1
NUMA node0 CPU(s):   0-47
```

同樣是 AMD EPYC 7413，但 `openstack05` 目前是單一 NUMA node，也就是標準的 `NPS1` 類型配置。  
這種情況下，雖然還是有 NUMA 的概念，但對 OpenStack VM placement 來說不會被切成四個大約 `64 GiB` 的小記憶體島。

也因為這樣，像 `8 vCPU / 16 GiB` 這種不算特別大的 VM，在 `openstack04` 那種「OS 看到 4 個 NUMA node」的配置上，其實很容易把單一 node 的記憶體吃滿；但在 `NPS1` 上就不太會這麼快遇到 local-memory OOM。

另外，從 `openstack05` 的 iDRAC BIOS 設定也可以直接看到：

- `NUMA Nodes Per Socket = 1`
- `L3 cache as NUMA Domain = Disabled`

這和作業系統裡看到的結果是吻合的，也就是：

- BIOS 設定是單一 NUMA node
- Linux runtime 也真的只看到 `NUMA node(s): 1`

這一點其實很重要，因為它也間接說明了另一件事：  
同樣是 EPYC 7413，平台本身並沒有一定會切成 `4` 個 NUMA node。`openstack04` 之所以會在 Linux 裡看到 `4` 個 NUMA node，關鍵不只是 `NUMA Nodes Per Socket`，而是 **`L3 cache as NUMA Domain` 這個 BIOS 選項也會影響作業系統實際看到的 NUMA 拓樸**。

換句話說，這次問題更精確的描述應該是：

- `openstack04` 在 runtime 上實際暴露出 `4` 個 host NUMA node
- `openstack05` 則是 `NUMA Nodes Per Socket = 1` 且 `L3 cache as NUMA Domain = Disabled`
- 因此 `openstack05` 的 Linux 只看到 `1` 個 NUMA node

也就是說，**這次讓 `openstack04` 變成「看起來像 NPS4」的主因，很可能就是 `L3 cache as NUMA Domain` 設定，而不是單純的 `NPS` 數值本身。**

## 實際修正與驗證

後來實際調整 `openstack04` 的 BIOS 設定後，再重新開機驗證。  
修正後的目標很單純，就是讓 `openstack04` 跟 `openstack05` 一樣，不要再把單一 socket 切成多個小的 NUMA island。

從這次比對 `openstack05` 的 BIOS 設定來看，修正的重點其實不是只有 `NPS1`，而是要讓：

- `NUMA Nodes Per Socket = 1`
- `L3 cache as NUMA Domain = Disabled`

這兩個條件同時成立，Linux runtime 才會真的回到單一 NUMA node 的狀態。

修正後再次查看 `lscpu`：

```bash
ssh debian@192.168.0.24 \
  'sudo lscpu | egrep "Model name|Socket\(s\)|Core\(s\) per socket|Thread\(s\) per core|NUMA node\(s\)|NUMA node[0-9] CPU\(s\)"'
```

輸出如下：

```text
Model name:          AMD EPYC 7413 24-Core Processor
Thread(s) per core:  2
Core(s) per socket:  24
Socket(s):           1
NUMA node(s):        1
NUMA node0 CPU(s):   0-47
```

再看 `numactl -H`：

```bash
ssh debian@192.168.0.24 'sudo numactl -H'
```

輸出如下：

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 ... 47
node 0 size: 257350 MB
node 0 free: 178519 MB
```

以及當下的記憶體使用狀況：

```bash
ssh debian@192.168.0.24 'free -h'
```

```text
               total        used        free      shared  buff/cache   available
Mem:           251Gi        65Gi       174Gi        36Mi        15Gi       185Gi
Swap:          8.0Gi          0B       8.0Gi
```

這表示這次修正至少在 NUMA topology 這件事情上已經完全生效了：

- `openstack04` 現在是單一 NUMA node
- Linux runtime 和 BIOS 預期一致
- 不再有先前那種「整台主機還有很多記憶體，但某一個 host node 先沒記憶體」的結構性問題

換句話說，前面排查出來的方向是對的。  
問題並不是 Linux 亂 OOM，也不是單純的 overcommit，而是 `openstack04` 原本的實際 NUMA 拓樸和我們以為的不一樣。

## 這次排查的結論

這次的 root cause 其實不複雜，只是如果只看整台機器的 memory graph，會很容易被誤導。

真正的問題是：

1. `openstack04` 在 Linux runtime 上實際暴露出 `4` 個 host NUMA node
2. Nova / libvirt 把多台 VM 用 strict NUMA locality 放在 host node 0
3. node 0 的本地記憶體先耗盡
4. 雖然 node 1, 2, 3 還有很多 RAM，但這些 VM 因為 `numatune memory mode='strict' nodeset='0'`，根本不能用
5. 於是 kernel 就 OOM kill QEMU

所以這個問題的正確描述不是：

> `openstack04` 明明還有 60 GiB free memory，Linux 卻亂 OOM。

而是：

> `openstack04` 的 host node 0 已經沒什麼 local memory 了，而被 strict pin 在 node 0 的 VM 沒辦法使用其他 NUMA node 的記憶體。

對於一般的 OpenStack compute workload，我個人會傾向認為「讓作業系統暴露出太多小 NUMA node」並不是很理想的設定。  
如果 workload 不是高度 NUMA-aware 或 HPC 類型，`NPS1` 會更單純，`NPS2` 也通常會比這種 `4` 個小 NUMA island 的狀態更不容易出現碎片化問題。

這次實際驗證後，也更能確定：

- `openstack04` 改回單一 NUMA node 之後，拓樸已經符合預期
- `openstack05` 的 BIOS 與 runtime 本來就是一致的
- 這類 OOM 問題如果只看 host-wide memory，很容易看錯方向

其實這類問題的重點不是「整台主機還有多少 RAM」，而是「對被 pin 住的 VM 來說，對應的 host NUMA node 還剩多少 RAM」。

## Reference

- OpenStack Nova CPU topologies  
  https://docs.openstack.org/nova/yoga/admin/cpu-topologies.html
- OpenStack Nova migration documentation  
  https://docs.openstack.org/nova/xena/admin/migration.html
- AMD EPYC BIOS & Workload Tuning Guide  
  https://www.amd.com/content/dam/amd/en/documents/epyc-technical-docs/tuning-guides/58467_amd-epyc-9005-tg-bios-and-workload.pdf
