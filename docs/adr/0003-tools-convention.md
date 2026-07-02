# ADR-0003: tools/ convention for deploy-host operational services

- **Status:** Accepted (amended by ADR-0030: `tools/infra_labs_common/` is a
  vendored shared library, not a standalone tool — no Dockerfile or deploy
  playbook of its own)
- **Date:** 2026-04-01
- **Deciders:** CNTUG ops

## Context
Operational automation (account lifecycle, usage reports, keystone-totp) needed a consistent home, build, and deploy story. `account_automation` originally lived in a separate repository, with no shared conventions across tools.

## Decision
Each standalone Python tool lives under `tools/<name>/` with its own `pyproject.toml`, `Dockerfile`, and an Ansible deploy playbook (`ansible/playbooks/deploy-<name>.yml`) targeting `deploy_host`.

- Each tool is independently buildable.
- Secrets live in `ansible/private/tools/<name>/`.
- Per-tool validation runs pytest + ruff through `validate.sh`, with staleness-based venv freshness detection.
- `docker-compose.yml` is the single source of truth for runtime.
- `scripts/` is reserved for manual admin scripts; deployment happens via Ansible.

## Alternatives considered
- One monolithic tool package — rejected; tools have different schedules and audiences (e.g. account_automation vs usage_reports).
- Separate repositories — rejected in favor of consolidation and shared conventions.

## Consequences
Build, deploy, and validate are uniform across tools, with a clear secret placement. Tools are scheduled independently via supercronic crons.

## References
- commit df5153d (migrate account_automation into tools/)
- commit e6e6bb3 (deploy playbook + validation)
- project tools convention note
