# ADR-0001: Infrastructure-as-code baseline: unified Ansible + Kolla-Ansible

- **Status:** Accepted
- **Date:** 2026-03-26
- **Deciders:** CNTUG ops

## Context
Fleet configuration spanning Ansible host bootstrap and OpenStack Kolla-Ansible was not version-controlled. `/etc/kolla` lived outside git, leaving no history and inviting drift. The repository root had also accumulated mixed concerns.

## Decision
Use a single repository as the source of truth.

- `ansible/` holds the inventory (inline `ansible_host`, host_vars, roles, playbooks).
- `kolla/` holds `globals.yml`, the multinode inventory, config overrides, and a vault-encrypted `passwords.yml`.
- `/etc/kolla` is a symlink to the repo's `kolla/` directory so every Kolla change is tracked in git.
- The Ansible setup was later moved into the `ansible/` subdirectory to keep the repo root clean.
- A validation pipeline runs yamllint, ansible-lint, and smoke tests for repo structure and inventory consistency.
- The README is bilingual (zh-TW + en).

## Alternatives considered
- Keep `/etc/kolla` outside version control — rejected; causes drift with no history.
- Separate repositories per concern — rejected; a single fleet with a single source of truth is simpler.

## Consequences
All configuration changes are tracked and reviewable. `passwords.yml` must remain vault-encrypted at all times. Path references were updated after the `ansible/` move.

## References
- commit b6882e6 (initial baseline)
- commit 52d8056 (move ansible into subdir)
- commit 32c61a8 (symlink /etc/kolla)
