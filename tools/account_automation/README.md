# CNTUG Infra Labs 帳號自動化

[English](README.en.md)

自動化管理 CNTUG Infra Labs 的 OpenStack 使用者/專案生命週期。從 Google Sheet 讀取註冊資料，建立 OpenStack 資源，透過 Resend 寄送通知信，並管理帳號到期流程。

設計為每日 cronjob 執行，具備冪等性，可安全重複執行。

## 狀態生命週期

```
APPROVED ──> ACTIVE ──> EXPIRING ──> EXPIRED ──> (管理員手動設定) PENDING_DELETE
                                                                    │
                                                       [預覽 + 通知管理員]
                                                                    │
                                                     (管理員手動設定) READY_TO_DELETE
                                                                    │
                                                                DELETED
```

| 狀態轉換 | 動作 |
|---|---|
| APPROVED -> ACTIVE | 建立 OpenStack 使用者與專案、設定配額、寄送含密碼的歡迎信 |
| ACTIVE -> EXPIRING | 寄送到期預警信（預設到期前 14 天） |
| EXPIRING -> EXPIRED | 預警寄出後超過寬限期即標記為過期（預設 7 天），並於 Keystone 停用該使用者 |
| (ACTIVE / EXPIRING / EXPIRED) -> RENEWAL | **手動操作** -- 管理員審核使用者回信後，在試算表中將狀態設為 `RENEWAL` 以核准續期 |
| RENEWAL -> ACTIVE | 重新計算到期日、於 Keystone 重新啟用使用者、清除 `ExpiryEmailSentAt` 以重置預警週期 |
| EXPIRED -> PENDING_DELETE | **手動操作** -- 管理員須在試算表中手動設定 |
| PENDING_DELETE | 預覽即將刪除的資源（使用者、專案、Group、VM、Volume、網路、路由器、浮動 IP、安全群組、快照、負載平衡器、映像檔、Ceph RadosGW 物件儲存 Bucket），寄送預覽信給管理員 |
| READY_TO_DELETE | **手動操作** -- 管理員確認預覽後手動設定。下次執行時依序清除專案內所有資源（VM、磁碟區、網路、路由器、浮動 IP、安全群組、快照、負載平衡器、映像、Ceph RadosGW 物件儲存 Bucket 與其物件，再刪除 RGW implicit-tenant 使用者）。RGW 使用者僅在所有 Bucket 均成功刪除後才會移除；若有任何 Bucket 刪除失敗，則保留 RGW 使用者以避免資料孤立。接著刪除 OpenStack Group（移除所有成員）與專案。使用者僅在無其他專案角色時才會刪除；若該使用者在其他專案仍有角色指派，則僅移除目標專案上的角色，保留使用者帳號 |

腳本不會自動刪除資源。管理員必須先將狀態設為 `PENDING_DELETE`（觸發預覽通知），再手動設為 `READY_TO_DELETE` 才會執行刪除。

### 續期 (Renewal)

使用者收到到期預警信後，直接回覆本信即可向管理員申請續期。管理員審核通過後，在試算表 `Status` 欄填入 `RENEWAL`，腳本下次執行時會自動續期：

- **新到期日**為「原申請時長（`使用時間`）自今天起算」與「`ExpiryDate` 欄現有日期」兩者中**較晚者** -- 因此只能延長，不能縮短。若需縮短，請改 `使用時間` 欄；若需自訂較晚的到期日，直接在 `ExpiryDate` 填入即可。
- 續期會於 Keystone **重新啟用**使用者，並清除 `ExpiryEmailSentAt` 以重置下一輪預警。
- 帳號在進入 `EXPIRED` 時即於 Keystone **停用**，故可供備份的時段為到期前的預警期（14 天）加寬限期（7 天）；`EXPIRED` 之後帳號被鎖定，但資料保留至刪除階段才清除。續期只能還原尚未進入 `DELETED` 的帳號。
- 若該 Keystone 使用者已不存在（帳號已刪除），續期會**失敗並記錄錯誤**，而非錯誤地將狀態改回 `ACTIVE`。

若帳號有對應的 Keystone Group（Group 名稱 = 專案名稱），刪除時會先移除所有 Group 成員再刪除 Group。預覽信與 CLI 預覽會顯示 Group 成員清單。

使用者通知信件（歡迎信、到期預警信）會 CC 至 `infra@cloudnative.tw`，並設定 `Reply-To: infra@cloudnative.tw`，使用者回信會直接寄到管理員 mailing list。刪除預覽信（管理員專屬）僅設定 `Reply-To`，不額外 CC（管理員信箱通常已在收件清單）。所有信件 footer 含 Horizon、Skyline、文件站、Telegram Channel、Grafana 監控、Upptime 狀態頁連結。

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
| `INFRA_LABS_OPENSTACK_CLOUD` | `openstack` | OpenStack cloud 名稱（對應 `clouds.yaml`） |
| `INFRA_LABS_OPENSTACK_MEMBER_ROLE` | `member` | 指派給使用者的專案角色 |
| `INFRA_LABS_OPENSTACK_LB_ROLE` | `load-balancer_member` | Load Balancer 存取角色 |
| `INFRA_LABS_EXPIRY_WARNING_DAYS` | `14` | 到期前幾天寄送預警信 |
| `INFRA_LABS_GRACE_PERIOD_DAYS` | `7` | 預警寄出後幾天標記為過期 |
| `INFRA_LABS_ADMIN_EMAIL` | *(空)* | 管理員信箱（接收刪除預覽通知）。支援逗號分隔多位管理員。若未設定，預覽通知不會寄出。 |
| `INFRA_LABS_DRY_RUN` | `false` | 僅記錄動作，不實際執行 |
| `INFRA_LABS_LOG_LEVEL` | `INFO` | 日誌等級 |
| `INFRA_LABS_RGW_ADMIN_URL` | *(空)* | Ceph RadosGW Admin API 位址（例如 `https://rgw.example.com`）。設定後啟用物件儲存 Bucket 清單與刪除功能。設定此項時，`ACCESS_KEY` 與 `SECRET_KEY` 為必填。 |
| `INFRA_LABS_RGW_ADMIN_ACCESS_KEY` | *(空)* | RGW Admin API 的 S3 存取金鑰。對應使用者須具備 `buckets=*;users=*` capabilities。 |
| `INFRA_LABS_RGW_ADMIN_SECRET_KEY` | *(空)* | RGW Admin API 的 S3 私密金鑰。 |
| `INFRA_LABS_RGW_ADMIN_REGION` | *(空)* | Sig V4 Credential Scope 中的 AWS region 名稱，須與 RGW 的 `rgw_zonegroup` 或 zone region 設定一致（例如 `cloudnative`）。若 RGW 接受空 region 則留空。 |

## 使用方式

```bash
# 正常執行（排程用，等同 account-automation run）
account-automation

# 試執行（僅記錄，不執行任何動作）
account-automation run --dry-run

# 預覽指定使用者即將刪除的資源（唯讀）
account-automation preview <username>

# 手動刪除指定使用者（需確認，或使用 --force 跳過確認）
account-automation delete <username>
account-automation delete <username> --force --dry-run
```

`run` 子命令透過檔案鎖 `/tmp/account-automation.lock` 防止同時執行多個實例。`delete` 與 `preview` 子命令不受檔案鎖限制，僅需 OpenStack 認證即可執行。

在非互動環境（如 Docker、cron）中執行 `delete` 需加上 `--force`，否則會因偵測到非 TTY 而拒絕執行。

### Cron 範例

以下範例僅適用於非 Docker 部署環境。若使用 Docker，請改參考下方的「Docker 部署」章節。

```cron
0 2 * * * /path/to/.venv/bin/account-automation >> /var/log/account-automation.log 2>&1
```

## Ansible 部署

正式部署使用 `ansible/playbooks/deploy-account-automation.yml`。此 playbook 會將原始碼與機敏檔案從本機同步至 deploy host，並透過 `docker compose` 啟動容器。

### 機敏檔案設定

執行 playbook 前，請在本機建立機敏檔案目錄並放入三個檔案。此路徑已從 git 排除：

```
ansible/private/tools/account_automation/
  .env                  # 從 tools/account_automation/.env.example 複製並填入對應值
  service-account.json  # Google 服務帳戶金鑰
  clouds.yaml           # OpenStack 認證資訊（從 tools/account_automation/secrets/clouds.yaml.example 複製）
```

`clouds.yaml` 中的 cloud 名稱必須與 `.env` 中的 `INFRA_LABS_OPENSTACK_CLOUD` 一致（預設值：`openstack`）。

### 執行 playbook

```bash
cd ansible
ansible-playbook playbooks/deploy-account-automation.yml
```

此 playbook 執行流程：
1. 將工具原始碼同步至 deploy host 的 `/opt/infra-labs-tools/account_automation/`
2. 將 `.env`、`service-account.json`、`clouds.yaml` 複製至遠端主機（模式 `0600`）
3. 在遠端主機執行 `deploy/verify.sh` 作為部署前檢查
4. 從 GHCR 拉取預先建置的 image，並透過 `docker compose up -d --pull always` 啟動容器（可用 `-e tools_image_tag=sha-<commit>` 指定 tag 以鎖定版本）
5. 驗證容器是否正常運行

Image 由 `.github/workflows/build-tools.yml` 建置並推送至 GHCR，因此 deploy host 改為拉取 image 而非在本機建置。

### 前置需求

- `ansible/hosts` 中必須已定義 `deploy_host` group
- 需已安裝 `ansible.posix` 與 `community.docker` Ansible collection

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

4. 啟動服務（會從 GHCR 拉取預先建置的 image）：

   ```bash
   docker compose up -d --pull always
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

拉取最新 image 後重新啟動：

```bash
docker compose up -d --pull always
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
| `vCPU 數量` | `2`（留空代表沿用 OpenStack 專案預設配額） |
| `記憶體 (GB)` | `4`（留空代表沿用 OpenStack 專案預設配額） |
| `儲存空間 (GB)` | `40`（留空代表沿用 OpenStack 專案預設配額） |
| `其餘設備` | `Load Balancer, GPU` |
| `Status` | `approved`、`active` 等（由腳本管理） |
| `ExpiryDate` | `2026-06-25`（由腳本管理） |
| `ExpiryEmailSentAt` | `2026-06-11`（由腳本管理） |
| `DeletePreviewSentAt` | `2026-07-01`（由腳本管理，刪除預覽信寄出日期） |

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
├── main.py              # 進入點、子命令（run/delete/preview）、檔案鎖
├── config.py            # 環境變數載入（支援 require_all 模式）
├── models.py            # 凍結資料類別、Status 列舉、ResourceItem、DeletePreview
├── duration.py          # 中文時間字串轉日期運算
├── validators.py        # 試算表資料列驗證
├── orchestrator.py      # 讀取 -> 驗證 -> 分派 -> 逐列寫回
├── repositories/
│   ├── base.py          # SheetRepository 協定
│   ├── google_sheets.py # 透過 gspread 存取 Google Sheets
│   ├── _sheet_mapping.py # 欄位解析與序列化
│   └── csv_repository.py
├── services/
│   ├── openstack_service.py  # 使用者/專案/Group/配額管理、資源清單、刪除預覽（含 RGW Bucket 與 implicit-tenant 使用者）、跨專案安全刪除
│   ├── rgw_admin.py          # Ceph RadosGW Admin REST API 客戶端（AWS Sig V4 認證）；可跨任意 implicit-tenant 專案列舉與刪除 Bucket 與使用者
│   └── email_service.py      # 透過 Resend 寄送歡迎信、到期預警信與刪除預覽信
└── processors/
    ├── registry.py        # 狀態 -> 處理器分派
    ├── approved.py        # APPROVED -> ACTIVE
    ├── active.py          # ACTIVE -> EXPIRING
    ├── expiring.py        # EXPIRING -> EXPIRED（並停用 Keystone 使用者）
    ├── renewal.py         # RENEWAL -> ACTIVE（重算到期日、重新啟用使用者）
    ├── pending_delete.py  # PENDING_DELETE: 預覽 + 通知管理員
    └── ready_to_delete.py # READY_TO_DELETE -> DELETED
```

每列資料獨立處理，單列失敗不影響其他列。試算表更新在每列處理成功後立即寫回；單列寫回失敗會記錄錯誤並標記為失敗，於下次執行重試（OpenStack 操作具冪等性），不會中斷其餘列的處理。所有外部 API 呼叫皆具備指數退避重試機制。
