# period_reconcile

[English](README.en.md)

部署主機 cron 任務的「週期完整性契約」（period-job integrity contract）。

## 解決的問題

Supercronic 與 `systemd Persistent=true` 不同：若容器在排程時間點是停止
狀態，錯過的那次就**不會補跑**，且沒有任何訊號（見
`docs/troubleshooting.md`）。對於以日曆週期為單位的任務——例如每月用量
報告——錯過一次代表某個已結束月份的報告默默地永遠不會產生，唯一的偵測
方式是「人類注意到它不見了」。

本套件把模型從「在某個時間點觸發」反轉為「向不變量收斂：每個**已結束**
的週期都存在一次成功執行」。一個輕量包裝器在磁碟上記錄每個任務的
**last-success watermark**，並在每小時的 tick 以及容器啟動時，補跑每個
尚未成功的已結束週期。

這與本 repo 在資料面已採用的教條一致（usage_reports 的新鮮度檢查
「拒絕而非少計費」、per-scope `last_processed` watermark）；排程層是唯一
尚未遵循此教條的地方。

## 契約

### Watermark（每任務一份）

JSON 檔，存放於該任務既有的持久化 volume（與其自身狀態同處，容器重建
不會遺失）。格式：

```json
{
  "version": 1,
  "job": "usage-reports",
  "last_success_label": "2026-05",
  "updated_at": "2026-06-01T01:00:00+00:00"
}
```

- 檔案不存在 = 「從未有任何週期成功」（全新部署）。
- 損毀或 schema 不符 = **硬錯誤**，拒絕執行（refuse rather than guess）。
  把損毀檔當成空檔可能會重跑已送出的週期，或在稍後寫入時跳過週期。
- `last_success_label` 必須是補零的 `YYYY-MM`（或 `null`）；其他格式一律視為
  損毀。label 以字典序比較，未補零的值（如 `2026-5`）會排序錯誤、默默跳過
  月份。
- `last_success_label` 只會前進、永不倒退。

### 週期計算

目前只實作**每月**（monthly）cadence。週期是一段半開區間
`[start, end)`，以設定時區（叢集使用 `Asia/Taipei`）的日曆邊界為錨點。
一個週期在 wall-clock 時間越過其 `end`（不含）之後即為「已結束」
（closed）。**只有已結束的週期會被補跑**——仍在累計的當期資料不完整，
不可報告。

模型刻意如此切分，未來新增其他 cadence（weekly、daily）時不需更動補跑
迴圈：只要提供「包含時刻 T 的週期」與「P 的下一個週期」即可。

### 補跑迴圈與 exit code

採本 repo 的 0/2/1 契約，回傳值反映所有嘗試週期中**最差**的結果：

| Exit | 意義 | 行為 |
|------|------|------|
| 0 | 成功 | 無事可做，或每個嘗試的週期都 exit 0 |
| 2 | 拒絕／被 gate 擋下 | 被包裝的任務 exit 2（如 usage_reports 新鮮度檢查：資料未就緒）。停止，下個 tick 重試。watermark **不**前進過該週期 |
| 1 | 錯誤 | 被包裝的任務 exit 1（或任何非預期碼）。停止並回報失敗，讓人類收到警示 |

- 週期以**最舊優先**逐一嘗試，遇到第一個非零 exit 即**停止**（stop-on-
  failure），絕不跳過中間的洞。
- watermark **只在 exit 0 時前進**，且每次成功後立即原子寫入（tmp +
  rename + fsync），因此部分補跑會在下個 tick 從中斷處精確續跑。
- `--max-backfill`（預設 6）限制單次最多補跑幾個週期（最舊優先），讓長
  時間停機產生有界、可預期的執行量。

### 重複觸發保護（double-fire protection）

每小時的 tick 與容器啟動時的執行可能重疊；長跑的 tick 也可能與下一個
tick 重疊。包裝器以非阻塞 `flock` 取得一把建議鎖（advisory lock）。若另
一個 reconcile 正持有鎖，本次以 **exit 0** 退出（另一個 pass 擁有這次的
工作），不會阻塞、也不會重複執行。

### 對被包裝任務的冪等性要求（必讀）

補跑會對**已結束**的週期重新執行被包裝的任務。被包裝的任務**必須**對於
重跑同一週期具備冪等性——重跑不得產生重複的副作用。

- **usage_reports 符合**：其 per-recipient 寄送 manifest（鍵為
  `period/project/email`）會讓已送出的收件者在重跑時被跳過；新鮮度檢查
  則在 CloudKitty 尚未完成該月計費時 exit 2。已結束月份的 CloudKitty
  計費資料為永久性。因此重跑某個月是安全的。
- 任何新的消費者在採用本契約前，必須先確認「重跑同一週期」是安全的。

## CLI

```
period-reconcile \
  --job usage-reports \
  --watermark /var/lib/usage-reports/reconcile-watermark.json \
  --lock /tmp/usage-reports-reconcile.lock \
  --timezone Asia/Taipei \
  --max-backfill 6 \
  -- /usr/local/bin/usage-reports generate --month {period}
```

`--` 之後是被包裝任務的指令範本。字面 token `{period}` 會在每個補跑週期
執行前被代換為該週期的 label（如 `2026-05`）。指令範本若不含
`{period}` 會被拒絕（exit 1），以免默默忽略要補跑的週期。

## 消費者採用狀態

| 消費者 | 狀態 | 說明 |
|--------|------|------|
| `usage_reports` | **已採用** | 每月報告；crontab 改為每小時 reconcile，entrypoint 於啟動時補跑。被包裝指令：`usage-reports generate --month {period}` |
| `account_automation` | 不適用（已記錄） | 每日 reconcile 現行試算表狀態，**非**以已結束週期為單位。漏跑一天只代表隔天的執行會處理；重跑某個過去日期沒有意義，且其副作用（寄信、刪資源）不具備「重跑同一天安全」的保證。已自有 flock。**採用前提**：需先定義「日」為週期、確認重跑某日冪等，效益才存在——目前判斷不值得。 |
| `keystone-totp` | 受阻（尚未納入 git 追蹤） | 該工具尚未提交進 repo，故其呼叫模型在此無法驗證、也未接線。已知的 cron（`cleanup-stale-credentials`）清理的是**當下**過期的憑證，並非以週期為單位，因此推測不需要此契約——但這應在已提交的工具上確認後再下定論。**採用前提**：先將該工具納入 git，再確認其是否有任何以「已結束週期」為單位的產物。 |

新消費者採用前必讀上方「對被包裝任務的冪等性要求」。

## 維運

### 檢查 watermark

```bash
# 在 deploy host 上（usage_reports 的 state volume）
docker compose exec usage-reports cat /var/lib/usage-reports/reconcile-watermark.json
```

### 重設 watermark

刪除 watermark 檔會讓下個 tick 視為全新部署，只補跑**最近一個**已結束
週期（不會回溯整個歷史）。若要強制重跑更舊的月份，把
`last_success_label` 手動改成更早的 label（或設為 `null`），再讓 tick
觸發；務必確認被包裝任務對那些月份仍可安全重跑。

```bash
# 觀察單次 reconcile（容器內手動觸發；reconcile.sh 是 crontab 與
# entrypoint 共用的唯一呼叫點，見 tools/usage_reports/deploy/reconcile.sh）
docker compose exec usage-reports /app/reconcile.sh
```

## 執行測試

```
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check
uv run mypy --strict src
```
