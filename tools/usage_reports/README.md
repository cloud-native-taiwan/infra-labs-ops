# usage_reports

[English](README.en.md)

CNTUG Infra Labs OpenStack 專案的每月成本報告工具。

本工具向 CloudKitty 查詢上一個日曆月的計費用量，透過 Nova/Cinder
補齊資源的中繼資料，從 Keystone 查出專案成員，再透過 Resend 寄出
每個專案的 HTML 成本報告。

## 功能

- 讀取 CloudKitty v2 Summary 資料，依 collector 儲存的鍵分組：
  `tenant_id`（專案）以及運算資源的每台執行個體 `uuid`。儲存資源沒有
  `uuid`，因此以第二次查詢取得專案層級的彙總後合併。這些鍵必須與
  `kolla/config/cloudkitty/metrics.yml` 一致；若改查 `project_id`/`id`
  會讓每個專案都得到空的 summary。
- 透過 Nova 與 Cinder 將資源 UUID 解析為可讀的名稱與規格。
- 從 Keystone 的角色指派中找出專案成員；略過沒有 email 的使用者。
- 透過 Resend 為每位收件者寄出一封 HTML 信件，內容為雙語（中文／英文）
  並附上各資源的成本明細。成本以 USD 標示。
- 將寄送結果記錄於 JSON manifest，使重跑具備冪等性。

## 不做的事

- 真實計費、開立發票或收款。
- 配額強制執行或自動清理資源。
- 網路用量計費。
- 即時儀表板（由 Horizon／Skyline 負責）。

## 設定

所有設定皆透過 `INFRA_LABS_*` 環境變數提供。完整清單見
`.env.example`。

CloudKitty 存取使用 `clouds.yaml` 中由 `INFRA_LABS_OPENSTACK_CLOUD`
指定的 profile。該認證必須具備 `rating:rating:get_all` 權限（通常為
`admin` 角色）。

在 `clouds.yaml` 設定 `api_timeout: 30` 以限制 Keystone／Nova／Cinder
呼叫的最長等待時間。CloudKitty 的 HTTP 呼叫則由程式碼中的 30 秒逾時
限制（見 `cloudkitty_service.HTTP_TIMEOUT_SECONDS`）。

## 本機執行

```
cd tools/usage_reports
uv venv
uv pip install -e ".[dev]"
cp .env.example .env  # 填入實際數值
uv run usage-reports generate --dry-run --month 2026-05
```

`--dry-run` 會略過 Resend 呼叫，並將報告摘要印到 stdout。
`--month YYYY-MM` 指定報告期間；省略時使用設定時區下的上一個日曆月。
`--force` 會略過新鮮度檢查與寄送冪等 manifest（用於重寄或非排程測試）。

### 測試單一使用者

`--only-project <project_id>` 與 `--only-email <address>` 可將執行範圍限縮到
單一專案及／或單一收件者，讓你預覽或只寄出某一個人的報告，而不會動到其他
專案成員。`--only-project` 接受專案的 **UUID** 而非名稱——可用
`openstack project show <name> -c id -f value` 取得。它們可與上述旗標組合：

```
# 只算 Alice 的報告，不寄任何信：
uv run usage-reports generate --dry-run --force --only-email alice@example.com

# 實際寄出某一專案中 Alice 的報告：
uv run usage-reports generate --force \
  --only-project 1a2b... --only-email alice@example.com
```

這類測試建議加上 `--force`：它會略過新鮮度檢查與 manifest，使先前的寄送
紀錄不會抑制這次重寄。

限縮範圍的執行（帶 `--only-project` 及／或 `--only-email`）**不會將寄送
紀錄寫入 manifest**。這是一道安全防護：真實的限縮測試寄送否則會把該收件者
標記為已寄送，導致下一次排程執行默默略過他們——讓他們只收到測試所產生
（可能不完整）的報告。若你是用限縮執行來*合法地*補寄給某位漏掉的收件者
並希望記錄下來，請加上 `--record-deliveries`。

當 CloudKitty 尚未完成該月計費時，排程執行也會以 **2** 結束（新鮮度檢查：
`CloudKitty has not finished processing ...`）。若此情況跨多次執行持續發生，
代表 CloudKitty 計費已停滯——見 `docs/runbooks/cloudkitty-metering-stall.md`。

新鮮度檢查會忽略「專案已不存在」的落後 scope：當專案被刪除後，CloudKitty 的
fetcher 不再探索其 scope，使 `last_processed` 永遠停在期間結束之前，否則會
無限期卡住報告。檢查會針對每個落後的 scope 向 Keystone 確認其專案，**僅在
明確的 404 時**才略過它（以 WARNING 記錄：`Ignoring lagging scope ... project
no longer exists`）。Keystone 的暫時性錯誤會被視為「仍存在」並繼續阻擋，因此
短暫的服務中斷絕不會導致仍在使用的專案被少計費。

打錯的 `--only-project`（在該期間找不到任何可計費專案，包括完全沒有用量的
期間）會以 **2** 結束，而非默默成功。最常見的原因是傳了專案*名稱*而非 UUID，
或該專案當月確實沒有計費用量。若 `--only-email` 比對不到某專案的任何成員，
會記錄一則警告並略過該專案；這屬於 **0**，因為僅以 email 跨多個專案限縮時，
出現不相符的專案是正常的。

## 部署

本工具以 Docker container 形式執行於 deploy host。排程不再是每月單次觸發
（容器停機時 supercronic 會默默跳過），而是採 `tools/period_reconcile/` 的
週期完整性契約：supercronic 每小時（`17 * * * *`）與容器啟動時各跑一次
`/app/reconcile.sh`，補跑每個尚未成功的已結束月份（watermark 記錄進度、
flock 防重複觸發）。見 `deploy/reconcile.sh`、`deploy/crontab`、
`deploy/entrypoint.sh` 與 `ansible/playbooks/deploy-usage-reports.yml`。

## 費率表

定價透過 CloudKitty 的 hashmap rating 模組管理。見
`docs/runbooks/cloudkitty-rate-card.md` 與 `scripts/setup_hashmap.sh`。

## 執行測試

```
uv run pytest
uv run ruff check
uv run mypy --strict src
```
