# CNTUG Infra Labs 帳號自動化

[English](README.en.md)

自動化管理 CNTUG Infra Labs 的 OpenStack 使用者/專案生命週期。從 Google Sheet 讀取註冊資料，建立 OpenStack 資源，透過 Resend 寄送通知信，並管理帳號到期流程。

設計為每日 cronjob 執行，具備冪等性，可安全重複執行。

## 狀態生命週期

```
APPROVED ──> ACTIVE ──> EXPIRING ──> EXPIRED ──> (管理員手動設定) PENDING_DELETE ──> DELETED
```

| 狀態轉換 | 動作 |
|---|---|
| APPROVED -> ACTIVE | 建立 OpenStack 使用者與專案、設定配額、寄送含密碼的歡迎信 |
| ACTIVE -> EXPIRING | 寄送到期預警信（預設到期前 14 天） |
| EXPIRING -> EXPIRED | 預警寄出後超過寬限期即標記為過期（預設 7 天） |
| EXPIRED -> PENDING_DELETE | **手動操作** -- 管理員須在試算表中手動設定 |
| PENDING_DELETE -> DELETED | 刪除 OpenStack 使用者與專案 |

腳本不會自動刪除資源。管理員必須明確將狀態設為 `PENDING_DELETE` 才會授權刪除。

## 安裝

需要 Python 3.12 以上版本。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

開發環境：

```bash
pip install -e ".[dev]"
```

## 設定

將 `.env.example` 複製為 `.env` 並填入對應的值：

```bash
cp .env.example .env
```

### 必填變數

| 變數 | 說明 |
|---|---|
| `INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON` | 服務帳號 JSON 檔案路徑，或直接填入 JSON 字串 |
| `INFRA_LABS_SPREADSHEET_ID` | Google Sheets 試算表 ID |
| `INFRA_LABS_OPENSTACK_DOMAIN_ID` | OpenStack domain ID（用於建立使用者與專案） |
| `INFRA_LABS_RESEND_API_KEY` | Resend API 金鑰 |
| `INFRA_LABS_RESEND_FROM_EMAIL` | 寄件者信箱 |

### 選填變數

| 變數 | 預設值 | 說明 |
|---|---|---|
| `INFRA_LABS_WORKSHEET_NAME` | `Sheet1` | 工作表名稱 |
| `INFRA_LABS_OPENSTACK_CLOUD` | `default` | OpenStack cloud 名稱（對應 `clouds.yaml`） |
| `INFRA_LABS_OPENSTACK_MEMBER_ROLE` | `member` | 指派給使用者的專案角色 |
| `INFRA_LABS_OPENSTACK_LB_ROLE` | `load-balancer_member` | Load Balancer 存取角色 |
| `INFRA_LABS_EXPIRY_WARNING_DAYS` | `14` | 到期前幾天寄送預警信 |
| `INFRA_LABS_GRACE_PERIOD_DAYS` | `7` | 預警寄出後幾天標記為過期 |
| `INFRA_LABS_DRY_RUN` | `false` | 僅記錄動作，不實際執行 |
| `INFRA_LABS_LOG_LEVEL` | `INFO` | 日誌等級 |

## 使用方式

```bash
# 正常執行
account-automation

# 試執行（僅記錄，不執行任何動作）
account-automation --dry-run
```

透過檔案鎖 `/tmp/account-automation.lock` 防止同時執行多個實例。

### Cron 範例

以下範例僅適用於非 Docker 部署環境。若使用 Docker，請改參考下方的「Docker 部署」章節。

```cron
0 2 * * * /path/to/.venv/bin/account-automation >> /var/log/account-automation.log 2>&1
```

## Docker 部署

### 前置需求

- Docker
- Docker Compose

### 快速開始

1. 複製環境變數範本並填入對應值：

   ```bash
   cp .env.example .env
   ```

2. 將 `service-account.json` 與 `clouds.yaml` 放入 `secrets/` 目錄。
3. 執行設定檢查：

   ```bash
   bash deploy/verify.sh
   ```

4. 建置並啟動服務：

   ```bash
   docker compose up -d --build
   ```

5. 檢查執行日誌，確認服務正常：

   ```bash
   docker compose logs -f
   ```

### 測試高頻排程

可使用較頻繁的測試排程驗證容器執行：

```bash
docker compose run --rm -v ./deploy/crontab.test:/app/crontab:ro account-automation
```

### 更新

拉取最新程式碼後重新建置並啟動：

```bash
git pull && docker compose up -d --build
```

### 手動觸發

可在已啟動的容器內手動執行一次：

```bash
docker compose exec account-automation account-automation
```

### 已知限制

若容器在排程時間點處於停止狀態，該次執行會被跳過。Supercronic 不會補跑錯過的排程，這點與 `systemd` 的 `Persistent=true` 不同。

## Google Sheet 格式

試算表須包含標題列，欄位順序不限：

| 欄位 | 範例 |
|---|---|
| `時間戳記` | `2026/3/25 下午 1:00:05` |
| `姓名` | `王大明` |
| `使用者名稱` | `daming` |
| `Email` | `daming@gmail.com` |
| `使用用途` | `研究專案` |
| `使用時間` | `兩週`、`一個月`、`三個月` 或 `六個月` |
| `vCPU 數量` | `2` |
| `記憶體 (GB)` | `4` |
| `儲存空間 (GB)` | `40` |
| `其餘設備` | `Load Balancer, GPU` |
| `Status` | `approved`、`active` 等（由腳本管理） |
| `ExpiryDate` | `2026-06-25`（由腳本管理） |
| `ExpiryEmailSentAt` | `2026-06-11`（由腳本管理） |

範例資料請參考 `example.csv`。

## 開發

```bash
# 執行測試
pytest

# Lint 檢查
ruff check src/ tests/

# 型別檢查
mypy src/
```

## 架構

```
src/account_automation/
├── main.py              # 進入點、檔案鎖、CLI 參數
├── config.py            # 環境變數載入
├── models.py            # 凍結資料類別、Status 列舉
├── duration.py          # 中文時間字串轉日期運算
├── validators.py        # 試算表資料列驗證
├── orchestrator.py      # 讀取 -> 驗證 -> 分派 -> 逐列寫回
├── repositories/
│   ├── base.py          # SheetRepository 協定
│   ├── google_sheets.py # 透過 gspread 存取 Google Sheets
│   └── csv_repository.py
├── services/
│   ├── openstack_service.py  # 使用者/專案/配額管理
│   └── email_service.py      # 透過 Resend 寄送歡迎信與到期預警信
└── processors/
    ├── registry.py       # 狀態 -> 處理器分派
    ├── approved.py       # APPROVED -> ACTIVE
    ├── active.py         # ACTIVE -> EXPIRING
    ├── expiring.py       # EXPIRING -> EXPIRED
    └── pending_delete.py # PENDING_DELETE -> DELETED
```

每列資料獨立處理，單列失敗不影響其他列。試算表更新在每列處理成功後立即寫回。所有外部 API 呼叫皆具備指數退避重試機制。
