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
| APPROVED -> ACTIVE | Creates OpenStack user + project (tagged `managed-by:account-automation`, see [Ownership tag](#ownership-tag)), sets quotas, sends welcome email with password |
| ACTIVE -> EXPIRING | Sends expiry warning email (14 days before expiry by default) |
| EXPIRING -> EXPIRED | Marks expired after grace period (7 days after warning by default) and disables the user in Keystone |
| (ACTIVE / EXPIRING / EXPIRED) -> RENEWAL | **Manual** -- after reviewing the user's email reply, the admin sets the status to `RENEWAL` to approve renewal |
| RENEWAL -> ACTIVE | Recomputes the expiry date, re-enables the user in Keystone, and clears `ExpiryEmailSentAt` to reset the warning cycle |
| EXPIRED -> PENDING_DELETE | **Manual** -- admin must set this in the sheet |
| PENDING_DELETE | Previews resources to be deleted (user, project, group, VMs, volumes, networks, routers, floating IPs, security groups, snapshots, load balancers, images, Ceph RadosGW object storage buckets) and emails the admin |
| READY_TO_DELETE | **Manual** -- admin confirms after reviewing the preview. Next run purges all project resources (VMs, volumes, networks, routers, floating IPs, security groups, snapshots, load balancers, images, Ceph RadosGW object storage buckets and their objects, then the RGW implicit-tenant user) then deletes the OpenStack group (removes all members) and project. The RGW user is only deleted after all buckets are successfully removed; if any bucket deletion fails, the RGW user is retained to prevent orphaning data. The OpenStack user is only deleted if they have no role assignments on other projects; otherwise only the target project's roles are removed and the user account is retained |

The script never auto-deletes. An admin must set `PENDING_DELETE` (triggers preview notification), then manually set `READY_TO_DELETE` to authorize deletion.

`READY_TO_DELETE` is gated, not just a convention: deletion is **refused** unless a delete preview has actually been sent (`DeletePreviewSentAt` is set) **and** is at least 4 calendar days old. `DeletePreviewSentAt` records only the day (not the time) a preview was sent, so the age is measured in whole days; a 4-day floor guarantees more than 72 real hours even in the worst case (a preview dated late in the day, purged at the 02:00 cron). A row set to `READY_TO_DELETE` by mistake -- a typo or a wrong-row paste -- is logged and left untouched instead of being purged at the next run. A preview that is too fresh is also left untouched and proceeds automatically once it has aged.

### Renewal

After receiving the expiry warning, a user simply replies to the email to ask the admin for renewal. Once the admin approves, they set the `Status` cell to `RENEWAL`; the next run renews the account automatically:

- The **new expiry** is the **later** of "the original duration (`使用時間`) recomputed from today" and "the date already in `ExpiryDate`" -- so renewal can only extend, never shorten. To shorten, change the duration field; to set a specific later date, type it into `ExpiryDate`.
- Renewal **re-enables** the user in Keystone and clears `ExpiryEmailSentAt` so the next warning cycle re-arms.
- The user is **disabled** in Keystone when the account enters `EXPIRED`, so the backup window is the warning period (14 days) plus the grace period (7 days) before `EXPIRED`; after `EXPIRED` the account is locked but data persists until the deletion stage. Renewal can only restore accounts that have not yet reached `DELETED`.
- If the Keystone user no longer exists (the account was already deleted), renewal **fails and is logged** instead of flipping the row back to `ACTIVE` for a ghost account.

### Ownership tag

Lab accounts share the Keystone domain with `admin`, the `service` project and the service users, so the tool refuses to touch anything it did not create:

- Users, projects and groups are looked up by exact name **within** `INFRA_LABS_OPENSTACK_DOMAIN_ID` (never openstacksdk `find_*()`, which tries GET-by-ID first and ignores the domain). A case-variant match (an existing `alice` when `Alice` is requested) is refused, since Keystone may treat names case-insensitively.
- Every project the tool creates is tagged `managed-by:account-automation` (`MANAGED_PROJECT_TAG` in `services/keystone_accounts.py`). The tool owns account `<name>` only if the same-name project carries that tag; the same-name user and group are touched only when that owned project exists.
- If a same-name project exists without the tag, or a same-name user/group exists with no same-name project, provisioning, disabling, renewing, previewing and deleting all **refuse**: the error is logged, the row is recorded as a failure (exit code 1), and nothing is reused, modified or deleted. The `delete` / `preview` CLI prints the refusal and exits 1. One exception: previewing or deleting when neither a same-name project nor group exists is a no-op success that leaves any same-name user untouched (with a warning) (so a retry after a user was retained for its other project roles completes).
- An `APPROVED` row whose username is 32 hex characters (looks like a Keystone ID) or is in `RESERVED_USERNAMES` (`validators.py`: `admin`, `service`, Kolla service users, case-insensitive) is rejected as invalid. Rows already past `APPROVED` are not re-checked, so existing accounts keep expiring and deleting normally (the tag guards them).

Projects created before the tag existed must be backfilled once, or every later operation on them (expiry disable, renewal re-enable, preview, deletion) is refused and the row fails each run. Run `backfill-tags` right after deploying this version. `backfill-tags` reads the sheet and, for each username with a row in `ACTIVE`/`EXPIRING`/`EXPIRED`/`RENEWAL`/`PENDING_DELETE`/`READY_TO_DELETE`, tags the project only if the username passes validation, a same-name project and user exist in the domain, the user's email matches a sheet email for that username, and that user is the project's only role assignee. Because even this fingerprint cannot rule out a hand-made project that an older run reused, `--apply` tags only the usernames you name: run the dry run, confirm each `would-tag` account, then pass those usernames. A skipped account (or an `APPROVED` row whose project an older run created) can be tagged by hand after checking it is really a lab account:

```bash
openstack project set --tag managed-by:account-automation --domain <domain-id> <username>
```

If the account has an associated Keystone group (group name = project name), deletion removes all group members first, then deletes the group. The preview email and CLI preview show group membership.

User-facing emails have `Reply-To: infra@cloudnative.tw` so user replies route to the admin mailing list. The welcome email carries the initial password, so it is sent to the user **only** -- admins instead receive a separate credential-free "account provisioned" notification, so the shared `infra@` mailbox never accumulates live passwords. The expiry warning (no credentials) is CC'd to `infra@cloudnative.tw`. The delete-preview email (admin-only) sets `Reply-To` but is not CC'd, since the admin alias is typically already on the recipient list. The welcome email instructs the user to change their password immediately on first login. All emails include footer links to Horizon, Skyline, the docs site, the Telegram channel, Grafana stats, and the Upptime status page.

## Setup

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install --no-deps -e ../infra_labs_common  # shared library; never on PyPI
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
| `INFRA_LABS_TEXTFILE_COLLECTOR_DIR` | `/var/lib/node_exporter/textfile_collector` | node_exporter textfile directory for the success metric (see [Exit codes and monitoring](#exit-codes-and-monitoring)). If the directory does not exist the metric is skipped, so local runs need nothing. |

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

# One-off: tag projects created before the ownership tag (dry run; add --apply to tag)
account-automation backfill-tags
account-automation backfill-tags --apply <username> [<username> ...]
```

The `run` subcommand takes an `flock` on `/tmp/account-automation.lock` to prevent concurrent runs; a run that finds it held does nothing and exits 75. The kernel releases the lock when its holder exits, so it cannot go stale (a held lock means a live or hung run). The `delete` and `preview` subcommands are not blocked by the lock and only require OpenStack credentials.

### Exit codes and monitoring

| `run` exit code | Meaning | Success metric written? |
|---|---|---|
| `0` | Every actionable row succeeded (or there were none) | Yes (not in dry run) |
| `1` | At least one row failed to process, or the run aborted (config, Google Sheets or other fatal error) | No |
| `65` | No row failed, but one or more actionable rows were skipped as invalid | No |
| `75` | Another run holds the lock; this run did nothing | No |

Each run ends with a `Processing summary: succeeded=N failed=N invalid=N` log line. Failure reasons are logged as `<ExceptionType>: <message>` in the log message (and a fatal error's traceback as message text). Log messages, exception tracebacks and stack info all pass through secret redaction.

**Invalid rows**: only rows whose `Status` has a processor (`approved`, `active`, `expiring`, `renewal`, `pending_delete`, `ready_to_delete`) are checked. A row that has one of those statuses but cannot be parsed (e.g. a non-numeric quota or a malformed date) or fails validation (username, email, duration, quota) is skipped and never acted on, logged at WARNING with its row number and reason, and counted as `invalid`; the other rows still process. Rows with no status, `expired` or `deleted` are not checked, so old data there never fails a run. An unrecognized `Status` value is logged at WARNING with its row number and skipped, but not counted as invalid.

**Success metric**: after a fully successful run (exit 0, not dry run, including `INFRA_LABS_DRY_RUN=true`), the tool atomically writes `task_account_automation.prom` (mode `0644`) to `INFRA_LABS_TEXTFILE_COLLECTOR_DIR`:

```
# HELP deploy_host_task_last_success_timestamp_seconds Unix time of the last successful run of a deploy-host task.
# TYPE deploy_host_task_last_success_timestamp_seconds gauge
deploy_host_task_last_success_timestamp_seconds{task="account_automation"} <unix seconds>
```

node_exporter on the deploy host exports it, and the `DeployHostTaskStale` alert fires when it goes stale, so failed rows, invalid rows, lock skips, crashes and hangs all surface the same way. A failure to write the file is logged as an error but does not change the exit code. The container healthcheck still only checks that supercronic is running: job health is tracked by this metric, and Docker health status is not wired to any alert.

**Timeouts**: OpenStack API requests time out after 120 s unless `clouds.yaml` sets `api_timeout`; Google Sheets requests after 60 s; RGW admin requests after 10 s if the connection cannot be made (no read timeout: RGW cleanup errors are logged and skipped while the project is still deleted, so cutting off a slow purge would orphan its buckets); Resend uses its built-in 30 s timeout. OpenStack and Google Sheets timeouts are retried like other transient errors.

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
1. Syncs tool source to `/opt/infra-labs-tools/account_automation/` on `deploy_host`. The sync excludes `.env` and `secrets/`, so `--delete` leaves them alone.
2. Copies `.env`, `service-account.json`, and `clouds.yaml` to the remote host (mode `0600`). The secret files and the `secrets/` directory (`0700`) are owned by the container uid `10001`.
3. Runs `deploy/verify.sh` on the remote host as a pre-deploy check
4. Pulls the prebuilt image from GHCR and starts the container via `docker compose up -d --pull always` (pass the tag with `-e tools_image_tag=sha-<commit>`). It adds `--force-recreate` when step 2 changed `.env` or a secret file, or when you pass `-e tools_force_recreate=true`.
5. Verifies the container is running

The container mounts `secrets/` as a directory, read-only, so a replaced secret file is visible at once. To rotate a secret, update it under `ansible/private/tools/account_automation/` and rerun the playbook (see "Secrets" in `tools/README.en.md`).

The image is built and pushed to GHCR by `.github/workflows/build-tools.yml`, so the deploy host pulls it instead of building locally.

### Prerequisites

- `deploy_host` group must be defined in `ansible/hosts`
- `ansible.posix` and `community.docker` Ansible collections must be installed

## Docker Deployment

### Prerequisites

- Docker
- Docker Compose
- `/var/lib/node_exporter/textfile_collector` on the host, owned by uid `10001` (the deploy playbook creates it). Compose bind-mounts it for the success metric; if it is missing, Docker creates it root-owned and metric writes fail (logged as an error).

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
├── main.py              # Entry point, subcommands (run/delete/preview/backfill-tags), file lock, exit codes
├── config.py            # Environment variable loading (supports require_all mode)
├── metrics.py           # Last-success gauge for the node_exporter textfile collector
├── models.py            # Frozen dataclasses, Status enum, ResourceItem, DeletePreview
├── duration.py          # Chinese duration strings -> date math
├── validators.py        # Input validation for sheet rows (reserved and ID-like usernames)
├── backfill.py          # backfill-tags: tag pre-existing projects the tool provisioned
├── orchestrator.py      # Read -> status gate -> validate -> dispatch -> write per row
├── repositories/
│   ├── base.py          # SheetRepository protocol
│   ├── google_sheets.py # Google Sheets via gspread
│   ├── _sheet_mapping.py # Column parsing and serialization
│   └── csv_repository.py
├── services/
│   ├── keystone_accounts.py  # Domain-scoped name lookups, ownership tag, refusal of unmanaged accounts
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

Each row is processed independently -- one row's failure does not block others. Sheet updates are written per-row immediately after successful processing; a write failure for one row is logged, recorded as a failure, and retried on the next run (OpenStack mutations are idempotent) rather than aborting the rest of the pass. Invalid rows are skipped and reported without blocking the valid ones. All external API calls have timeouts and use retry with exponential backoff.
