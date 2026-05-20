# Quickstart：第一次操作此 repo

[English](quickstart.en.md)

目標：從零到「我在 fleet 上跑了第一個 dry-run」，預估 10–20 分鐘。

> 名詞不熟？對照 [glossary.md](glossary.md)。  
> 主機與群組關係？對照 [fleet-topology.md](fleet-topology.md)。  
> 任何步驟卡住？對照 [troubleshooting.md](troubleshooting.md)。

## 你需要的權限與條件

- 控制機（你的筆電或跳板機）可上網
- 可使用 `debian` 帳號 SSH 進入目標主機，且該帳號具備 passwordless sudo
- `kolla/passwords.yml` 對應的 vault 密碼（向維護者索取）
- `ansible/private/` 機敏檔案（向維護者索取或自行生成）

如果只是想驗證 repo（不碰主機），SSH 與 vault 密碼可以延後處理；步驟 1–4 已足夠。

## Step 1：拿到程式碼，建立 venv

```bash
git clone <repo-url>
cd infra-labs-ops
python3 -m venv .venv
.venv/bin/pip install ansible-core ansible-lint yamllint jinja2 pyyaml
```

> 為什麼用 `.venv/`：repo 預設的 `ansible/scripts/validate.sh` 會找 `./.venv/bin/python`。其他位置可運作但需要自行調整路徑。

## Step 2：放入機敏檔案

於 repo 根目錄建立以下檔案。**路徑已從 git 排除**：

```text
ansible/private/
  authorized_keys             # roles/base 會寫入 /home/debian/.ssh/authorized_keys
  passwd.client               # roles/mail 會寫入 /etc/exim4/passwd.client
```

`authorized_keys` 內容為一行一把 SSH public key（要 push 到 fleet 上 `debian` 帳號的那些）。  
`passwd.client` 為 exim4 smarthost 認證設定，格式 `mail.example.com:username:password`。

僅在你打算同時部署 tools 才需要：

```text
ansible/private/tools/account_automation/
  .env                  # 從 tools/account_automation/.env.example 複製並填值
  service-account.json  # Google service account 金鑰
  clouds.yaml           # OpenStack 認證
```

## Step 3：取得 Kolla vault 密碼

```bash
# 把 vault 密碼存到 git 不會收的檔案
echo '<向維護者索取>' > kolla/ansible_vault_pass
chmod 600 kolla/ansible_vault_pass
```

僅當你要操作 Kolla-Ansible 才需要；單純跑 `ansible/playbooks/*` 不需要。

## Step 4：跑本地靜態驗證

```bash
./ansible/scripts/validate.sh
```

正常結果：`yamllint`、`ansible-lint`、playbook syntax、inventory target、template render 全綠。任何紅字請對照 [troubleshooting.md](troubleshooting.md#step-4-validatesh-失敗)。

> 這一步**不需要**碰任何主機。失敗代表 repo 本身或本地環境有問題。

## Step 5：對最低爆炸半徑的主機做 dry-run

`openstack06` 是 Ceph-only 節點（沒跑 OpenStack 控制面），所以 OpenStack 控制面不會受影響。但它**仍是 production**：跑著 live Ceph cluster 的 OSD，下方 step 6 的 apply 會實際變更 sysctl／GRUB／network／mail 設定。先看完 step 5 的 diff，與維護者確認後再進 step 6，必要時挑維護時段。

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff --limit openstack06
```

預期輸出：

- 一連串 task，多數為 `ok`，少數可能 `changed`（在 check mode 下表示「我會改」）。
- `--diff` 會印出 GRUB、sysctl、network、mail 模板會被改寫的內容。
- 最後 PLAY RECAP 顯示 `failed=0`、`unreachable=0`。

如果 `unreachable=1`，請先解決 SSH／sudo 問題再回來（見 [troubleshooting.md](troubleshooting.md#unreachable1-或-permission-denied-publickey)）。

## Step 6：你看完 diff 也覺得 OK，正式套用

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --limit openstack06
```

對 `bond0` 有變更時，network role 會逐台 rolling、每台間隔 15 秒，過程中該主機網路會短暫斷線；對 `ceph_cluster` 成員（包含 openstack06）這代表 OSD 會被短暫切離 cluster，請先確認 `ceph -s` 為 `HEALTH_OK`，且沒有 backfill/recovery 進行中。

## 接下來要看什麼？

- 想了解每個 playbook 做什麼：[`ansible/README.md`](../ansible/README.md)
- 想調整 Ceph day-2 設定：[`ansible/roles/ceph-config/README.md`](../ansible/roles/ceph-config/README.md)
- 想調整主機效能：[`ansible/roles/tuning/README.md`](../ansible/roles/tuning/README.md)
- 想部署或維運 tools：[`tools/account_automation/README.md`](../tools/account_automation/README.md)
- 想新增一台主機：[Root README -- 新主機首次設定](../README.md#新主機首次設定)
- 找其他文件：[`README.md`](README.md)
