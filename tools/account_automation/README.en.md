# CNTUG Infra Labs Account Automation

[中文](README.md)

Automates OpenStack user/project lifecycle management for CNTUG Infra Labs. Reads registration data from a Google Sheet, provisions OpenStack resources, sends email notifications via Resend, and manages account expiry.

Designed to run as a daily cronjob. Idempotent and safe to re-run.

## Status Lifecycle

```
APPROVED ──> ACTIVE ──> EXPIRING ──> EXPIRED ──> (admin sets) PENDING_DELETE
                                                                  │
                                                     [preview + notify admin]
                                                                  │
                                                   (admin sets) READY_TO_DELETE
                                                                  │
                                                               DELETED
```

| Transition | What happens |
|---|---|
| APPROVED -> ACTIVE | Creates OpenStack user + project, sets quotas, sends welcome email with password |
| ACTIVE -> EXPIRING | Sends expiry warning email (14 days before expiry by default) |
| EXPIRING -> EXPIRED | Marks expired after grace period (7 days after warning by default) and disables the user in Keystone |
| (ACTIVE / EXPIRING / EXPIRED) -> RENEWAL | **Manual** -- after reviewing the user's email reply, the admin sets the status to `RENEWAL` to approve renewal |
| RENEWAL -> ACTIVE | Recomputes the expiry date, re-enables the user in Keystone, and clears `ExpiryEmailSentAt` to reset the warning cycle |
| EXPIRED -> PENDING_DELETE | **Manual** -- admin must set this in the sheet |
| PENDING_DELETE | Previews resources to be deleted (user, project, group, VMs, volumes, networks, routers, floating IPs, security groups, snapshots, load balancers, images, Ceph RadosGW object storage buckets) and emails the admin |
| READY_TO_DELETE | **Manual** -- admin confirms after reviewing the preview. Next run purges all project resources (VMs, volumes, networks, routers, floating IPs, security groups, snapshots, load balancers, images, Ceph RadosGW object storage buckets and their objects, then the RGW implicit-tenant user) then deletes the OpenStack group (removes all members) and project. The RGW user is only deleted after all buckets are successfully removed; if any bucket deletion fails, the RGW user is retained to prevent orphaning data. The OpenStack user is only deleted if they have no role assignments on other projects; otherwise only the target project's roles are removed and the user account is retained |

The script never auto-deletes. An admin must set `PENDING_DELETE` (triggers preview notification), then manually set `READY_TO_DELETE` to authorize deletion.

### Renewal

After receiving the expiry warning, a user simply replies to the email to ask the admin for renewal. Once the admin approves, they set the `Status` cell to `RENEWAL`; the next run renews the account automatically:

- The **new expiry** is the **later** of "the original duration (`使用時間`) recomputed from today" and "the date already in `ExpiryDate`" -- so renewal can only extend, never shorten. To shorten, change the duration field; to set a specific later date, type it into `ExpiryDate`.
- Renewal **re-enables** the user in Keystone and clears `ExpiryEmailSentAt` so the next warning cycle re-arms.
- The user is **disabled** in Keystone when the account enters `EXPIRED`, so the backup window is the warning period (14 days) plus the grace period (7 days) before `EXPIRED`; after `EXPIRED` the account is locked but data persists until the deletion stage. Renewal can only restore accounts that have not yet reached `DELETED`.
- If the Keystone user no longer exists (the account was already deleted), renewal **fails and is logged** instead of flipping the row back to `ACTIVE` for a ghost account.

If the account has an associated Keystone group (group name = project name), deletion removes all group members first, then deletes the group. The preview email and CLI preview show group membership.

User-facing emails (welcome, expiry warning) are CC'd to `infra@cloudnative.tw` and have `Reply-To: infra@cloudnative.tw` so user replies route to the admin mailing list. The delete-preview email (admin-only) sets `Reply-To` but is not CC'd, since the admin alias is typically already on the recipient list. All emails include footer links to Horizon, Skyline, the docs site, the Telegram channel, Grafana stats, and the Upptime status page.

## Setup

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

### Required variables

| Variable | Description |
|---|---|
| `INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON` | Path to a service account JSON file, or inline JSON string |
| `INFRA_LABS_SPREADSHEET_ID` | Google Sheets spreadsheet ID |
| `INFRA_LABS_OPENSTACK_DOMAIN_ID` | OpenStack domain ID for user/project creation |
| `INFRA_LABS_RESEND_API_KEY` | Resend API key for sending emails |
| `INFRA_LABS_RESEND_FROM_EMAIL` | Sender email address |

### Optional variables

| Variable | Default | Description |
|---|---|---|
| `INFRA_LABS_WORKSHEET_NAME` | `Sheet1` | Worksheet tab name |
| `INFRA_LABS_OPENSTACK_CLOUD` | `openstack` | OpenStack cloud name (from `clouds.yaml`) |
| `INFRA_LABS_OPENSTACK_MEMBER_ROLE` | `member` | Role assigned to users on their project |
| `INFRA_LABS_OPENSTACK_LB_ROLE` | `load-balancer_member` | Role for Load Balancer access |
| `INFRA_LABS_EXPIRY_WARNING_DAYS` | `14` | Days before expiry to send warning |
| `INFRA_LABS_GRACE_PERIOD_DAYS` | `7` | Days after warning before marking expired |
| `INFRA_LABS_ADMIN_EMAIL` | *(empty)* | Admin email for deletion preview notifications. Supports comma-separated list for multiple admins. If unset, preview emails are skipped. |
| `INFRA_LABS_DRY_RUN` | `false` | Log actions without executing them |
| `INFRA_LABS_LOG_LEVEL` | `INFO` | Logging level |
| `INFRA_LABS_RGW_ADMIN_URL` | *(empty)* | Ceph RadosGW admin API base URL (e.g. `https://rgw.example.com`). Enables object storage bucket inventory and deletion. **Must be HTTPS** -- the client fails closed on a non-HTTPS URL rather than signing admin credentials over cleartext (loopback hosts are exempt for local testing). When set, `ACCESS_KEY` and `SECRET_KEY` are required. |
| `INFRA_LABS_RGW_ADMIN_ACCESS_KEY` | *(empty)* | S3 access key for the RGW admin API. The key's admin user must have `buckets=*;users=*` capabilities. |
| `INFRA_LABS_RGW_ADMIN_SECRET_KEY` | *(empty)* | S3 secret key for the RGW admin API. |
| `INFRA_LABS_RGW_ADMIN_REGION` | *(empty)* | AWS region name used in Sig V4 credential scope. Must match RGW's `rgw_zonegroup` or zone region name (e.g. `cloudnative`). Leave empty if RGW accepts an empty region. |

## Usage

```bash
# Normal run (cron use, equivalent to account-automation run)
account-automation

# Dry run (logs actions without side effects)
account-automation run --dry-run

# Preview resources that would be deleted for a user (read-only)
account-automation preview <username>

# Manually delete a user (requires confirmation, or use --force)
account-automation delete <username>
account-automation delete <username> --force --dry-run
```

The `run` subcommand uses a file lock at `/tmp/account-automation.lock` to prevent concurrent runs. The `delete` and `preview` subcommands are not blocked by the lock and only require OpenStack credentials.

In non-interactive environments (Docker, cron), `delete` requires `--force` or it will refuse to run when stdin is not a TTY.

### Cron example (non-Docker)

This is for bare-metal / non-Docker setups. If you are running with Docker, see the Docker Deployment section below.

```cron
0 2 * * * /path/to/.venv/bin/account-automation >> /var/log/account-automation.log 2>&1
```

## Ansible Deployment

Production deployment uses `ansible/playbooks/deploy-account-automation.yml`. The playbook syncs source code and secrets from your local machine to the deploy host, then starts the container via `docker compose`.

### Secrets setup

Before running the playbook, create the secrets directory on your local machine and populate three files. This path is excluded from git:

```
ansible/private/tools/account_automation/
  .env                  # copy from tools/account_automation/.env.example and fill in values
  service-account.json  # Google service account key
  clouds.yaml           # OpenStack credentials (copy from tools/account_automation/secrets/clouds.yaml.example)
```

The `clouds.yaml` cloud name must match `INFRA_LABS_OPENSTACK_CLOUD` in your `.env` (default: `openstack`).

### Running the playbook

```bash
cd ansible
ansible-playbook playbooks/deploy-account-automation.yml
```

The playbook:
1. Syncs tool source to `/opt/infra-labs-tools/account_automation/` on `deploy_host`
2. Copies `.env`, `service-account.json`, and `clouds.yaml` to the remote host (mode `0600`)
3. Runs `deploy/verify.sh` on the remote host as a pre-deploy check
4. Pulls the prebuilt image from GHCR and starts the container via `docker compose up -d --pull always` (override the tag with `-e tools_image_tag=sha-<commit>` to pin)
5. Verifies the container is running

The image is built and pushed to GHCR by `.github/workflows/build-tools.yml`, so the deploy host pulls it instead of building locally.

### Prerequisites

- `deploy_host` group must be defined in `ansible/hosts`
- `ansible.posix` and `community.docker` Ansible collections must be installed

## Docker Deployment

### Prerequisites

- Docker
- Docker Compose

### Quick start

1. Copy the example environment file and fill in the required values:

   ```bash
   cp .env.example .env
   ```

2. Place `service-account.json` and `clouds.yaml` in `secrets/`.
3. Verify the configuration:

   ```bash
   bash deploy/verify.sh
   ```

4. Start the container (pulls the prebuilt image from GHCR):

   ```bash
   docker compose up -d --pull always
   ```

5. Check the logs to verify startup:

   ```bash
   docker compose logs -f
   ```

### Testing with frequent schedule

Use the test crontab to run the container with a more frequent schedule:

```bash
docker compose run --rm -v ./deploy/crontab.test:/app/crontab:ro account-automation
```

### Updating

Pull the latest image and restart the container:

```bash
docker compose up -d --pull always
```

### Manual trigger

Run the job immediately inside the running container:

```bash
docker compose exec account-automation account-automation
```

### Known limitation

If the container is down at the scheduled time, that run is skipped. Supercronic does not retry missed runs, unlike `systemd` with `Persistent=true`.

## Google Sheet Format

The sheet must have a header row with these columns (order does not matter):

| Column | Example |
|---|---|
| `時間戳記` | `2026/3/25 下午 1:00:05` |
| `姓名` | `John Doe` |
| `使用者名稱` | `johndoe` |
| `Email` | `johndoe@gmail.com` |
| `使用用途` | `Research project` |
| `使用時間` | `兩週`, `一個月`, `三個月`, or `六個月` |
| `vCPU 數量` | `2` (leave blank to inherit the OpenStack project default quota) |
| `記憶體 (GB)` | `4` (leave blank to inherit the OpenStack project default quota) |
| `儲存空間 (GB)` | `40` (leave blank to inherit the OpenStack project default quota) |
| `其餘設備` | `Load Balancer, GPU` |
| `Status` | `approved`, `active`, etc. (managed by the script) |
| `ExpiryDate` | `2026-06-25` (managed by the script) |
| `ExpiryEmailSentAt` | `2026-06-11` (managed by the script) |
| `DeletePreviewSentAt` | `2026-07-01` (managed by the script, date deletion preview was sent) |

See `example.csv` for a sample.

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

## Architecture

```
src/account_automation/
├── main.py              # Entry point, subcommands (run/delete/preview), file lock
├── config.py            # Environment variable loading (supports require_all mode)
├── models.py            # Frozen dataclasses, Status enum, ResourceItem, DeletePreview
├── duration.py          # Chinese duration strings -> date math
├── validators.py        # Input validation for sheet rows
├── orchestrator.py      # Read -> validate -> dispatch -> write per row
├── repositories/
│   ├── base.py          # SheetRepository protocol
│   ├── google_sheets.py # Google Sheets via gspread
│   ├── _sheet_mapping.py # Column parsing and serialization
│   └── csv_repository.py
├── services/
│   ├── openstack_service.py  # User/project/group/quota management, resource inventory, deletion preview, resource purge (including RGW buckets and implicit-tenant users), cross-project safe deletion
│   ├── rgw_admin.py          # Ceph RadosGW admin REST API client (AWS Sig V4 auth); lists/deletes buckets and users for any implicit-tenant project
│   └── email_service.py      # Welcome, expiry warning, and delete preview emails via Resend
└── processors/
    ├── registry.py        # Status -> processor dispatch
    ├── approved.py        # APPROVED -> ACTIVE
    ├── active.py          # ACTIVE -> EXPIRING
    ├── expiring.py        # EXPIRING -> EXPIRED (also disables the Keystone user)
    ├── renewal.py         # RENEWAL -> ACTIVE (recompute expiry, re-enable user)
    ├── pending_delete.py  # PENDING_DELETE: preview + notify admin
    └── ready_to_delete.py # READY_TO_DELETE -> DELETED
```

Each row is processed independently -- one row's failure does not block others. Sheet updates are written per-row immediately after successful processing; a write failure for one row is logged, recorded as a failure, and retried on the next run (OpenStack mutations are idempotent) rather than aborting the rest of the pass. All external API calls use retry with exponential backoff.
