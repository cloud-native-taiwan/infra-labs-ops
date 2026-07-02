# ADR-0030: Shared `infra_labs_common` library, vendored into tool images

- **Status:** Accepted (amends ADR-0003)
- **Date:** 2026-07-03
- **Deciders:** CNTUG ops

## Context

The three deploy-host tools (account_automation, usage_reports,
period_reconcile) carried byte-identical copies of secret-redaction logging,
retry classification, env-var config helpers, atomic JSON persistence, and
period math, kept in sync only by "keep in sync with X" comments. The 2026-07
audit found the copies had already diverged once (the redaction filter bug was
fixed in one tool first), and every future fix had to be applied three times.

ADR-0003 made each tool standalone on purpose; a shared library weakens that
isolation, so the trade-off deserves its own record.

## Decision

Extract the duplicated code into `tools/infra_labs_common/`, a
stdlib-dependency-free package (`dependencies = []`) with its own test suite
(100% coverage, mypy strict). Consumers import it; it is **vendored** into
each image by the CI build workflow (rsync into the build context, then
`pip install --no-deps`), never published or resolved from an index.

Two rules keep the supply chain and semantics safe:

1. The package name appears in **no** `pyproject.toml` and **no**
   `requirements.lock`. The lockfiles are produced by `uv pip compile`, which
   would otherwise try to resolve the name from PyPI — a dependency-confusion
   hazard for a public repo. `validate.sh` and the Dockerfiles install it by
   path with `--no-deps` instead. Its retry module's third-party imports
   (tenacity, requests, keystoneauth1) must stay pinned in every consuming
   image's lockfile.
2. Extraction is semantics-preserving, pinned by tests: the redaction filter
   redacts **after** %-interpolation, and usage_reports keeps its own denylist
   retry policy rather than the shared opt-in transient classifier.

Unlike ADR-0003 tools, the package has no Dockerfile, no deploy playbook, and
no image of its own.

## Alternatives considered

- Keep duplication with sync comments: rejected — already diverged in
  practice; three-way manual sync does not scale with tool count.
- Publish to a private index or GHCR as a wheel: rejected — extra
  infrastructure and credentials for three consumers in one repo; vendoring
  from the same commit is simpler and atomically versioned with consumers.
- Declare it in pyprojects/lockfiles as a path dependency: rejected —
  `uv pip compile --generate-hashes` and `--require-hashes` installs do not
  compose cleanly with unhashable path deps, and naming it at all invites
  index resolution mistakes.

## Consequences

Fixes to shared behavior land once. Consumers cannot drift silently: CI
rebuilds both images whenever `tools/infra_labs_common/**` changes. The cost:
a change to the shared package can now break three tools at once, so its test
suite is held to the strictest gate in the repo (95% coverage floor, mypy
strict on src and tests), and cross-tool behavior pins remain duplicated in
each consumer's own tests on purpose.

## References

- ADR-0003 (tools convention — amended by this record)
- ADR-0004 (CI-built tool images; the vendoring step lives in
  `.github/workflows/build-tools.yml`)
- 2026-07-02 whole-repo/fleet audit, section 4 (duplication findings)
