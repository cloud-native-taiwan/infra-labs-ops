# Tools

The `tools/` directory contains operational tools deployed on the deploy host. Each tool is an independent application with its own build system, Docker image, and test suite.

## Convention

Each `tools/<name>/` must include:

| Item | Purpose |
|------|---------|
| `pyproject.toml` (or equivalent) | Defines dependencies and build config |
| `Dockerfile` | Builds a deployable container image |
| `deploy/` | Schedule config, runtime config |
| `tests/` | Tests runnable via a standard command |
| `.env.example` | Template for required environment variables |
| `README.md` | Tool documentation |

> **Exception (shared libraries):** A few `tools/<name>/` are libraries consumed
> by other tools rather than independently deployed containers (e.g.
> `period_reconcile`). Such packages have no `Dockerfile`/`deploy/`/docker-compose
> of their own; the consuming tool vendors them into its build context at deploy
> time (see the package README and the consumer's playbook).

## Secrets

Each tool's secrets live in `ansible/private/tools/<name>/`, **not** in the tool directory itself. This path is excluded from version control via `.gitignore`.

Typical secrets include:
- `.env` -- environment variables (API keys, etc.)
- `clouds.yaml` -- OpenStack credentials
- `service-account.json` -- Google service account key

## Deployment

Each tool has a corresponding Ansible playbook: `ansible/playbooks/deploy-<name>.yml`

These playbooks target the `deploy_host` inventory group (192.168.0.1). They sync source code and secrets to the remote host, then run `docker compose`.

```bash
cd ansible
ansible-playbook playbooks/deploy-account-automation.yml
```

## Validation

`ansible/scripts/validate.sh` automatically discovers tools under `tools/*/`, creates an isolated `.venv` for each, and runs pytest and ruff check.

## Current Tools

| Tool | Description |
|------|-------------|
| [`account_automation`](account_automation/) | OpenStack account lifecycle automation (create, extend, expire, delete) |
| [`usage_reports`](usage_reports/) | Monthly per-project usage and estimated-cost emails, driven by CloudKitty |
| [`period_reconcile`](period_reconcile/) | Shared library: period-job integrity contract; back-fills closed periods a cron job missed (adopted by usage_reports) |
