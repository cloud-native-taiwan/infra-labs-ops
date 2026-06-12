# Troubleshooting

[English](troubleshooting.en.md)

整理新加入操作者最容易遇到的問題，依 [quickstart.md](quickstart.md) 的步驟順序排列。

## 安裝相關

### `ansible-playbook: command not found`

`.venv` 未啟用。改用完整路徑或啟用 venv：

```bash
.venv/bin/ansible-playbook ...
# 或
source .venv/bin/activate
```

### `pip install ansible-core` 卡在 PEP 517 build

通常是 Python 版本太舊（< 3.10）或缺 `python3-dev`。先確認：

```bash
python3 --version
```

需要 3.10+。Debian/Ubuntu 上：`sudo apt install python3-dev`。

## Step 4: validate.sh 失敗

### `yamllint` 報 indentation / trailing-spaces

依照訊息修正。Repo 採用兩格縮排、無 trailing space。`.yamllint` 為唯一定義來源。

### `ansible-lint` 報新 rule

不要直接 `# noqa` 掉，先看是否真的有設計問題。Repo 允許的 deviation 已加在各檔頭部 `# noqa:` 註解，並附理由。

### `syntax check` 報 `ERROR! Syntax Error while loading YAML.`

通常是某個 task 的 `name` 行有問題，或縮排亂了。錯誤訊息會點出檔案與行號。

### `inventory target` 失敗：`Could not match supplied host pattern, ignoring: ...`

`ansible/hosts` 內 group 名稱與 playbook 的 `hosts:` 對不起來。先看 `ansible/hosts`，確認該 group 存在且有成員。

## SSH / sudo

### `unreachable=1` 或 `Permission denied (publickey)`

依序確認：

```bash
# 1. 對 fleet 上的 debian 使用者可以直接 SSH
ssh debian@<host-ip>

# 2. 該帳號有 passwordless sudo
ssh debian@<host-ip> 'sudo -n true && echo OK'

# 3. Ansible 用對的 user
grep ansible_user ansible/ansible.cfg ansible/hosts
```

若 `ssh` 本身就連不上，問題在你的 SSH key 還沒部署到對應主機。請維護者協助加 key，或先用 `ssh-copy-id`（前提是已有別的方式登入）。

### `sudo: a password is required`

`debian` 帳號的 sudoers 沒有 `NOPASSWD`。在該主機上：

```bash
sudo visudo
# 加入：debian ALL=(ALL) NOPASSWD:ALL
```

### Bond 開機未起來（已知問題，openstack01）

開機後 bond 介面沒有自動 up。手動修復：

```bash
ssh debian@openstack01 'sudo systemctl restart networking'
```

根因是 Mellanox DKMS 模組與 bonding 模組的載入時序，或 systemd-networkd 與 Mellanox firmware 初始化的競爭。重啟 networking 是已知 workaround。

## Ansible Vault

### `ERROR! Attempting to decrypt but no vault secrets found`

`kolla/passwords.yml` 是 Vault 加密的，但本地沒給 vault 密碼。建立 `kolla/ansible_vault_pass`：

```bash
echo '<向維護者索取>' > kolla/ansible_vault_pass
chmod 600 kolla/ansible_vault_pass
```

Kolla-Ansible 透過 `--configdir kolla` 讀取此檔。

### `Failed to find any cipher mapping for vault id ...`

`kolla/ansible_vault_pass` 內容不對，或檔案末端有多餘的換行/空白。建議用 `printf` 而非 `echo`：

```bash
printf '%s' '<vault password>' > kolla/ansible_vault_pass
```

## Playbook 行為相關

### 為什麼 network apply 中間停了 15 秒？

刻意設計。`roles/network` 在 `bond0` 變更時逐台 rolling，每台間隔 15 秒，避免同時擾動所有節點上的 Ceph／OVN／VM 流量。等就好。

### `--check --diff` 對 command 任務看不到變化

預期行為。`ansible.builtin.command` 在 check mode 下無法預知結果，所以顯示為 skipped 或不變。模板類 task（GRUB、sysctl、network、mail）在 check mode 仍會顯示完整 diff。

### Ceph apply playbook 不動作

```
TASK [ceph-config : ...] ******
skipping: ...
```

`ceph-apply.yml` 設計成預設不變更。必須明確帶旗標：

```bash
ansible-playbook playbooks/ceph-apply.yml --limit ceph_bootstrap -e ceph_iac_apply=true
```

## Cephadm 套件

### `apt: Unable to locate package cephadm`

`roles/ceph-bootstrap` 在 Debian 13 (`trixie`) 主機上刻意使用 Ceph upstream 的 `bookworm` apt repo（因為 upstream 尚未發布 `trixie` suite）。這是預期設計，並非錯誤。若在新主機 bootstrap 時還是抓不到，請確認該主機已執行 `bootstrap.yml`（會加入 apt source）。

## Tools 部署

### 新增 cron 條目時的 `command not found`

若你在 `tools/<name>/deploy/crontab` 加新項目時用相對指令（例如 `account-automation`）而非絕對路徑，Supercronic 會找不到執行檔。Supercronic 不繼承 container shell 的 PATH。**現有 crontab 全部用絕對路徑**（如 `/usr/local/bin/account-automation`），請保持此慣例。

### Supercronic 略過排程（容器停機時）

容器在排程時間點是停止狀態 → Supercronic 不會補跑（與 `systemd Persistent=true` 不同）。檢查 `docker compose ps` 與 host 重開機紀錄。

**以週期為單位的任務已有解法：** `usage_reports` 已改用
`tools/period_reconcile/` 的「週期完整性契約」——不再於固定時間單次觸發，
而是每小時 reconcile 並在容器啟動時補跑，向「每個已結束月份都存在一次成功
報告」收斂。若懷疑某個月份漏跑，檢查 watermark：

```bash
docker compose exec usage-reports cat /var/lib/usage-reports/reconcile-watermark.json
```

`last_success_label` 即最近一次成功的月份（`YYYY-MM`）。詳見
`tools/period_reconcile/README.md`（含 watermark 格式、補跑語意、重設方式）。

`account_automation`（每日 reconcile 現行試算表狀態）並非以「已結束週期」
為單位，重跑舊日期沒有意義，因此尚未納入此契約。`keystone-totp` 因尚未納入
git 追蹤，在此無法評估。其採用狀態見 `tools/period_reconcile/README.md`。

## 找不到原因？

1. 先看完整 stderr，貼到 Slack/TG 群組。
2. 翻 [docs/README.md](README.md) 的 runbook 與報告區，看是否曾經發生過。
3. 直接找維護者。
