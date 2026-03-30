# Tools

[English](README.en.md)

`tools/` 目錄存放部署於 deploy host 上的營運工具。每個工具是獨立的應用程式，擁有自己的建置系統、Docker image 和測試套件。

## 慣例

每個 `tools/<name>/` 必須包含：

| 項目 | 說明 |
|------|------|
| `pyproject.toml`（或同等建置檔） | 定義相依套件與建置設定 |
| `Dockerfile` | 建置可部署的 container image |
| `deploy/` | 排程設定、執行期設定 |
| `tests/` | 可透過標準指令執行的測試 |
| `.env.example` | 所需環境變數的範本 |
| `README.md` | 工具說明文件 |

## 機敏檔案

每個工具的機敏檔案存放於 `ansible/private/tools/<name>/`，**不**存放在工具目錄中。此目錄已透過 `.gitignore` 排除於版本控制。

典型的機敏檔案包含：
- `.env` — 環境變數（API key 等）
- `clouds.yaml` — OpenStack 認證資訊
- `service-account.json` — Google 服務帳戶金鑰

## 部署

每個工具對應一個 Ansible playbook：`ansible/playbooks/deploy-<name>.yml`

這些 playbook 的目標為 `deploy_host` inventory group（192.168.0.1），會將原始碼與機敏檔案同步至遠端主機後執行 `docker compose`。

```bash
cd ansible
ansible-playbook playbooks/deploy-account-automation.yml
```

## 驗證

`ansible/scripts/validate.sh` 會自動偵測 `tools/*/` 下的工具，為每個工具建立獨立的 `.venv`，並執行 pytest 與 ruff check。

## 目前的工具

| 工具 | 說明 |
|------|------|
| [`account_automation`](account_automation/) | OpenStack 帳號生命週期自動化（建立、延期、到期、刪除） |
