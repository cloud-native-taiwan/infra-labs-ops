# infra_labs_common

[English](README.en.md)

共用函式庫：`account_automation`、`usage_reports`、`period_reconcile`
三個營運工具共用的基礎元件。抽出的動機是三個工具各自維護了
byte-identical 的複本（secret redaction、retry 分類、環境變數讀取、
原子寫入），複本之間已多次需要人工同步。

## 模組

| 模組 | 說明 |
|------|------|
| `redaction` | `SecretRedactingFilter`：在 %-interpolation **之後** redact（`record.msg = redacted getMessage()`、`record.args = None`），避免 format string 先被改寫導致 `getMessage()` 丟 TypeError 而整行 log 被丟棄 |
| `retry` | transient 錯誤分類（連線失敗、timeout、HTTP 5xx）與 `STANDARD_RETRY` tenacity decorator（3 次、指數退避加 jitter） |
| `env_config` | `INFRA_LABS_` 前綴環境變數讀取（空字串視為未設定） |
| `periods` | `month_bounds`：時區內日曆月份的半開區間 `[start, end)` |
| `persistence` | `atomic_write_json`：tmp 檔 + fsync + rename + 目錄 fsync 的原子 JSON 寫入 |
| `openstack` | 系統管理 port 的 `device_owner` 常數（刪除路徑必須跳過的 port） |

各工具保留自己的 `LOG_FORMAT`、`configure_logging`、`AppConfig` 與
`load_config`；本套件只提供底層原語。`usage_reports` 的 retry 政策
（denylist、非永久錯誤都重試）與本套件的 opt-in transient 分類語義不同，
**刻意不**統一（見 `tools/usage_reports/src/usage_reports/retry.py`）。

## 封裝與 vendoring

本套件**不**發佈至 PyPI，也**不**出現在任何工具的 `pyproject.toml`
相依或 `requirements.lock` 中（hash-pinned lockfile 由
`uv pip compile pyproject.toml` 產生，宣告了反而會嘗試從 PyPI 解析，
在公開 repo 上有 dependency-confusion 風險）：

- **Image 建置**：`.github/workflows/build-tools.yml` 以 rsync 將本套件
  vendor 進各消費工具的 build context（`vendor/infra_labs_common/`），
  Dockerfile 以 `pip install --no-deps` 安裝。
- **本機開發**：`ansible/scripts/validate.sh` 以 path
  （`pip install --no-deps -e`）安裝進各工具的 `.venv`。

`dependencies = []` 是刻意的：唯一需要第三方套件的模組是 `retry`
（tenacity / requests / keystoneauth1），由消費工具的 lockfile 已 pin 的
版本提供；stdlib-only 的消費端（`period_reconcile`）不 import `retry`，
因此不需要任何第三方套件。

## 測試

```bash
cd tools/infra_labs_common
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

`ansible/scripts/validate.sh` 會自動涵蓋本套件（pytest coverage gate 95%
加 ruff check）。
