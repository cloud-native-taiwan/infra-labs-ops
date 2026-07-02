# ADR-0005: Layered secret-leak prevention for the public repo

- **Status:** Accepted
- **Date:** 2026-06-13
- **Deciders:** CNTUG ops

## Context
The repository became public. It carries vault-encrypted secrets and must never leak plaintext credentials, so a single line of defense is insufficient.

## Decision
Apply defense in depth at three points.

1. **Local** — an offered pre-commit hook plus a guard that rejects commits if vault files are staged in plaintext.
2. **CI** — gitleaks v8.30.1 runs on every push and PR and fails the build on detected leaks.
3. **Platform** — GitHub native secret scanning plus push protection are enabled.

Vault-encrypted configs (e.g. `prometheus-alertmanager.yml`) are re-tracked so configuration is recoverable while staying encrypted.

## Alternatives considered
- Single CI scan only — rejected; no local catch before push.
- Rely on GitHub scanning alone — rejected; no custom rules, no vault-plaintext guard.

## Consequences
Leaks are caught at the commit, CI, and platform layers. Contributors must keep vault files encrypted. A full-history scan confirmed no secret material is reachable from `main`.

## References
- commit eef1ae4 (gitleaks guardrails)
- commit 40760de (re-track vault alertmanager + pre-commit guard)
- .pre-commit-config.yaml
