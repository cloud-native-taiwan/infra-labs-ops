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
| APPROVED -> ACTIVE | 建立 OpenStack 使用者與專案（專案加上 `managed-by:account-automation` 標籤，見[所有權標籤](#所有權標籤)）、設定配額、寄送含密碼的歡迎信 |
| ACTIVE -> EXPIRING | 寄送到期預警信（預設到期前 14 天） |
| EXPIRING -> EXPIRED | 預警寄出後超過寬限期即標記為過期（預設 7 天），並於 Keystone 停用該使用者 |
| (ACTIVE / EXPIRING / EXPIRED) -> RENEWAL | **手動操作** -- 管理員審核使用者回信後，在試算表中將狀態設為 `RENEWAL` 以核准續期 |
| RENEWAL -> ACTIVE | 重新計算到期日、於 Keystone 重新啟用使用者、清除 `ExpiryEmailSentAt` 以重置預警週期 |
| EXPIRED -> PENDING_DELETE | **手動操作** -- 管理員須在試算表中手動設定 |
| PENDING_DELETE | 預覽即將刪除的資源（使用者、專案、Group、VM、Volume、網路、路由器、浮動 IP、安全群組、快照、負載平衡器、映像檔、Ceph RadosGW 物件儲存 Bucket），寄送預覽信給管理員 |
| READY_TO_DELETE | **手動操作** -- 管理員確認預覽後手動設定。下次執行時依序清除專案內所有資源（VM、磁碟區、網路、路由器、浮動 IP、安全群組、快照、負載平衡器、映像、Ceph RadosGW 物件儲存 Bucket 與其物件，再刪除 RGW implicit-tenant 使用者）。RGW 使用者僅在所有 Bucket 均成功刪除後才會移除；若有任何 Bucket 刪除失敗，則保留 RGW 使用者以避免資料孤立。接著刪除 OpenStack Group（移除所有成員）與專案。使用者僅在無其他專案角色時才會刪除；若該使用者在其他專案仍有角色指派，則僅移除目標專案上的角色，保留使用者帳號 |

腳本不會自動刪除資源。管理員必須先將狀態設為 `PENDING_DELETE`（觸發預覽通知），再手動設為 `READY_TO_DELETE` 才會執行刪除。

`READY_TO_DELETE` 為強制閘門，非僅約定：除非確實已寄出刪除預覽（`DeletePreviewSentAt` 已填），且該預覽已滿 4 個日曆天，否則刪除會被**拒絕**。`DeletePreviewSentAt` 僅記錄寄出的日期（不含時間），故以整天計算天數；4 天的下限即使在最壞情況（預覽於當日稍晚寄出、於 02:00 cron 清除）也能保證超過 72 個實際小時。若因手誤或貼錯列而誤設 `READY_TO_DELETE`，該列會被記錄警告並保持原狀，不會於下次執行時被清除。預覽尚未滿期者亦保持原狀，待其滿期後自動繼續刪除流程。

### 續期 (Renewal)

使用者收到到期預警信後，直接回覆本信即可向管理員申請續期。管理員審核通過後，在試算表 `Status` 欄填入 `RENEWAL`，腳本下次執行時會自動續期：

- **新到期日**為「原申請時長（`使用時間`）自今天起算」與「`ExpiryDate` 欄現有日期」兩者中**較晚者** -- 因此只能延長，不能縮短。若需縮短，請改 `使用時間` 欄；若需自訂較晚的到期日，直接在 `ExpiryDate` 填入即可。
- 續期會於 Keystone **重新啟用**使用者，並清除 `ExpiryEmailSentAt` 以重置下一輪預警。
- 帳號在進入 `EXPIRED` 時即於 Keystone **停用**，故可供備份的時段為到期前的預警期（14 天）加寬限期（7 天）；`EXPIRED` 之後帳號被鎖定，但資料保留至刪除階段才清除。續期只能還原尚未進入 `DELETED` 的帳號。
- 若該 Keystone 使用者已不存在（帳號已刪除），續期會**失敗並記錄錯誤**，而非錯誤地將狀態改回 `ACTIVE`。

### 所有權標籤

實驗室帳號與 `admin`、`service` 專案及各服務使用者位於同一個 Keystone domain，因此腳本拒絕操作任何非由它建立的物件：

- 使用者、專案與 Group 一律以完整名稱在 `INFRA_LABS_OPENSTACK_DOMAIN_ID` **之內**查詢（不使用 openstacksdk 的 `find_*()`，因其會先以 ID 直接 GET 並忽略 domain）。若只找到大小寫不同的同名物件（申請 `Alice` 時已有 `alice`），也會拒絕，因 Keystone 名稱可能不分大小寫。
- 腳本建立的每個專案都會加上 `managed-by:account-automation` 標籤（`services/keystone_accounts.py` 的 `MANAGED_PROJECT_TAG`）。只有同名專案帶有此標籤時，帳號 `<name>` 才屬於腳本管理；同名使用者與 Group 也只有在該專案存在時才會被操作。
- 若同名專案存在但沒有標籤，或同名使用者/Group 存在但沒有同名專案，則開通、停用、續期、預覽與刪除都會**拒絕執行**：記錄錯誤、該列記為失敗（結束碼 1），不會沿用、修改或刪除任何物件。`delete` / `preview` CLI 會印出拒絕原因並以結束碼 1 結束。唯一例外：預覽或刪除時若同名專案與 Group 皆不存在，視為無事可做而成功結束，不會動到同名使用者（會記錄警告）（因此使用者因仍有其他專案角色而被保留後，重試刪除也能完成）。
- `APPROVED` 資料列的使用者名稱若為 32 個十六進位字元（形似 Keystone ID）或列於 `RESERVED_USERNAMES`（`validators.py`：`admin`、`service`、Kolla 服務使用者，不分大小寫），會被視為無效資料列。已過 `APPROVED` 的資料列不再重新檢查，既有帳號仍可正常到期與刪除（由標籤保護）。

標籤機制上線前建立的專案必須回填一次，否則之後對它們的所有操作（到期停用、續期重新啟用、預覽、刪除）都會被拒絕，該列每次執行都會失敗。請在部署此版本後立即執行 `backfill-tags`。`backfill-tags` 讀取試算表，對狀態為 `ACTIVE`/`EXPIRING`/`EXPIRED`/`RENEWAL`/`PENDING_DELETE`/`READY_TO_DELETE` 的使用者名稱，僅在名稱通過驗證、domain 內存在同名專案與同名使用者、使用者 email 與該名稱在試算表中的 email 相符、且該使用者是此專案唯一的角色指派對象時，才列為候選。由於此特徵仍無法排除舊版沿用的手動建立專案，`--apply` 只會為明確指定的使用者名稱加上標籤：先執行試執行、逐一確認 `would-tag` 帳號，再傳入這些名稱。被略過的帳號（或舊版執行時已建立專案的 `APPROVED` 資料列），確認確實為實驗室帳號後可手動加上標籤：

```bash
openstack project set --tag managed-by:account-automation --domain <domain-id> <username>
```

若帳號有對應的 Keystone Group（Group 名稱 = 專案名稱），刪除時會先移除所有 Group 成員再刪除 Group。預覽信與 CLI 預覽會顯示 Group 成員清單。

使用者通知信件設定 `Reply-To: infra@cloudnative.tw`，使用者回信會直接寄到管理員 mailing list。歡迎信含初始密碼，故**僅**寄給使用者本人；管理員改為收到一封不含任何憑證的「帳號已開通」通知信，使 `infra@` 共用信箱不會累積明文密碼。到期預警信（不含憑證）仍會 CC 至 `infra@cloudnative.tw`。刪除預覽信（管理員專屬）僅設定 `Reply-To`，不額外 CC（管理員信箱通常已在收件清單）。歡迎信會提醒使用者於首次登入後立即變更密碼。所有信件 footer 含 Horizon、Skyline、文件站、Telegram Channel、Grafana 監控、Upptime 狀態頁連結。

## 安裝

需要 Python 3.12 以上版本。

```bash
python -m venv .venv
source .venv/bin/activate
pip install --no-deps -e ../infra_labs_common  # 共用函式庫，不在 PyPI 上
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
| `INFRA_LABS_RGW_ADMIN_URL` | *(空)* | Ceph RadosGW Admin API 位址（例如 `https://rgw.example.com`）。設定後啟用物件儲存 Bucket 清單與刪除功能。**必須使用 HTTPS** -- 若位址非 HTTPS，客戶端會直接拒絕（fail-closed），不會以明文簽署管理憑證（loopback 位址例外，供本機測試）。設定此項時，`ACCESS_KEY` 與 `SECRET_KEY` 為必填。 |
| `INFRA_LABS_RGW_ADMIN_ACCESS_KEY` | *(空)* | RGW Admin API 的 S3 存取金鑰。對應使用者須具備 `buckets=*;users=*` capabilities。 |
| `INFRA_LABS_RGW_ADMIN_SECRET_KEY` | *(空)* | RGW Admin API 的 S3 私密金鑰。 |
| `INFRA_LABS_RGW_ADMIN_REGION` | *(空)* | Sig V4 Credential Scope 中的 AWS region 名稱，須與 RGW 的 `rgw_zonegroup` 或 zone region 設定一致（例如 `cloudnative`）。若 RGW 接受空 region 則留空。 |
| `INFRA_LABS_TEXTFILE_COLLECTOR_DIR` | `/var/lib/node_exporter/textfile_collector` | 寫入成功指標的 node_exporter textfile 目錄（見[結束碼與監控](#結束碼與監控)）。目錄不存在時略過指標，本機執行無須任何設定。 |

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

# 一次性：為標籤機制上線前建立的專案補上標籤（試執行；加 --apply 才會實際加上）
account-automation backfill-tags
account-automation backfill-tags --apply <username> [<username> ...]
```

`run` 子命令對 `/tmp/account-automation.lock` 取得 `flock` 以防止同時執行多個實例；發現鎖已被持有的執行不做任何事，並以結束碼 75 結束。持有者結束時核心會自動釋放鎖，因此不會殘留過期的鎖（鎖被持有代表有執行中或卡住的實例）。`delete` 與 `preview` 子命令不受檔案鎖限制，僅需 OpenStack 認證即可執行。

### 結束碼與監控

| `run` 結束碼 | 意義 | 是否寫入成功指標 |
|---|---|---|
| `0` | 所有需處理的資料列皆成功（或沒有需處理的列） | 是（dry run 除外） |
| `1` | 至少一列處理失敗，或執行中止（設定、Google Sheets 或其他致命錯誤） | 否 |
| `65` | 沒有處理失敗的列，但有一或多列需處理的資料列因無效而略過 | 否 |
| `75` | 另一個執行持有鎖，本次未做任何事 | 否 |

每次執行最後會記錄 `Processing summary: succeeded=N failed=N invalid=N`。失敗原因以 `<例外類型>: <訊息>` 寫在日誌訊息本文中（致命錯誤的 traceback 也以訊息本文記錄），因此會經過機密遮蔽。

**無效資料列**：只檢查 `Status` 有對應處理器的資料列（`approved`、`active`、`expiring`、`renewal`、`pending_delete`、`ready_to_delete`）。具上述狀態但無法解析（例如配額非數字、日期格式錯誤）或未通過驗證（使用者名稱、Email、使用時間、配額）的資料列會被略過、絕不執行任何動作，以 WARNING 記錄列號與原因，並計入 `invalid`；其餘資料列照常處理。沒有狀態、`expired` 或 `deleted` 的資料列不檢查，因此其中的舊資料不會讓執行失敗。無法辨識的 `Status` 值會以 WARNING 記錄列號並略過，但不計為無效。

**成功指標**：完全成功的執行（結束碼 0，且非 dry run，包含 `INFRA_LABS_DRY_RUN=true`）結束後，工具會以原子方式將 `task_account_automation.prom`（權限 `0644`）寫入 `INFRA_LABS_TEXTFILE_COLLECTOR_DIR`：

```
# HELP deploy_host_task_last_success_timestamp_seconds Unix time of the last successful run of a deploy-host task.
# TYPE deploy_host_task_last_success_timestamp_seconds gauge
deploy_host_task_last_success_timestamp_seconds{task="account_automation"} <unix seconds>
```

deploy host 上的 node_exporter 會匯出此指標，指標過舊時 `DeployHostTaskStale` 告警即會觸發，因此處理失敗、無效資料列、鎖衝突、當機與卡住都會以同一方式浮現。寫入檔案失敗會記錄錯誤，但不改變結束碼。容器 healthcheck 仍只檢查 supercronic 是否在執行：工作健康狀態由此指標追蹤，Docker health 狀態並未接到任何告警。

**逾時**：OpenStack API 請求逾時 120 秒（`clouds.yaml` 有設定 `api_timeout` 時以其為準）；Google Sheets 請求 60 秒；RGW admin 請求連線 10 秒、讀取 60 秒（bucket 清除為 900 秒）；Resend 使用其內建的 30 秒逾時。逾時的請求與其他暫時性錯誤一樣會重試。

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
1. 將工具原始碼同步至 deploy host 的 `/opt/infra-labs-tools/account_automation/`。同步會排除 `.env` 與 `secrets/`，`--delete` 不會動到它們。
2. 將 `.env`、`service-account.json`、`clouds.yaml` 複製至遠端主機（模式 `0600`）。機敏檔案與 `secrets/` 目錄（`0700`）屬於容器 uid `10001`。
3. 在遠端主機執行 `deploy/verify.sh` 作為部署前檢查
4. 從 GHCR 拉取預先建置的 image，並透過 `docker compose up -d --pull always` 啟動容器（以 `-e tools_image_tag=sha-<commit>` 指定 tag）。步驟 2 變更了 `.env` 或機敏檔案，或傳入 `-e tools_force_recreate=true` 時，會加上 `--force-recreate`。
5. 驗證容器是否正常運行

容器以唯讀方式掛載整個 `secrets/` 目錄，替換後的機敏檔案會立即可見。輪替機敏檔案時，更新 `ansible/private/tools/account_automation/` 下的檔案後重新執行 playbook（見 `tools/README.md` 的「機敏檔案」）。

Image 由 `.github/workflows/build-tools.yml` 建置並推送至 GHCR，因此 deploy host 改為拉取 image 而非在本機建置。

### 前置需求

- `ansible/hosts` 中必須已定義 `deploy_host` group
- 需已安裝 `ansible.posix` 與 `community.docker` Ansible collection

## Docker 部署

### 前置需求

- Docker
- Docker Compose
- 主機上的 `/var/lib/node_exporter/textfile_collector`，擁有者為 uid `10001`（部署 playbook 會建立）。Compose 會 bind-mount 此目錄以寫入成功指標；若目錄不存在，Docker 會以 root 身分建立，指標寫入會失敗（記錄為錯誤）。

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
├── main.py              # 進入點、子命令（run/delete/preview/backfill-tags）、檔案鎖、結束碼
├── config.py            # 環境變數載入（支援 require_all 模式）
├── metrics.py           # 供 node_exporter textfile collector 讀取的最後成功時間指標
├── models.py            # 凍結資料類別、Status 列舉、ResourceItem、DeletePreview
├── duration.py          # 中文時間字串轉日期運算
├── validators.py        # 試算表資料列驗證（保留名稱與形似 ID 的使用者名稱）
├── backfill.py          # backfill-tags：為腳本已開通的既有專案補上標籤
├── orchestrator.py      # 讀取 -> 狀態篩選 -> 驗證 -> 分派 -> 逐列寫回
├── repositories/
│   ├── base.py          # SheetRepository 協定
│   ├── google_sheets.py # 透過 gspread 存取 Google Sheets
│   ├── _sheet_mapping.py # 欄位解析與序列化
│   └── csv_repository.py
├── services/
│   ├── keystone_accounts.py  # 限定 domain 的名稱查詢、所有權標籤、拒絕非腳本管理的帳號
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

每列資料獨立處理，單列失敗不影響其他列。試算表更新在每列處理成功後立即寫回；單列寫回失敗會記錄錯誤並標記為失敗，於下次執行重試（OpenStack 操作具冪等性），不會中斷其餘列的處理。無效資料列會被略過並回報，不影響有效資料列。所有外部 API 呼叫皆設有逾時，並具備指數退避重試機制。
