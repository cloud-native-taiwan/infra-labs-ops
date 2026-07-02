# infra_labs_common

Shared library: primitives used by the `account_automation`, `usage_reports`,
and `period_reconcile` operational tools. Extracted because the three tools
each carried byte-identical copies (secret redaction, retry classification,
env-var readers, atomic writes) that repeatedly had to be synced by hand.

## Modules

| Module | Purpose |
|--------|---------|
| `redaction` | `SecretRedactingFilter`: redacts **after** %-interpolation (`record.msg = redacted getMessage()`, `record.args = None`), so rewriting the format string can never make `getMessage()` raise TypeError and drop the log line |
| `retry` | Transient-error classification (connection failures, timeouts, HTTP 5xx) and the `STANDARD_RETRY` tenacity decorator (3 attempts, exponential backoff with jitter) |
| `env_config` | `INFRA_LABS_`-prefixed environment variable readers (empty string counts as absent) |
| `periods` | `month_bounds`: half-open `[start, end)` interval for a calendar month in a timezone |
| `persistence` | `atomic_write_json`: tmp file + fsync + rename + directory fsync |
| `openstack` | `device_owner` constants for system-managed ports the deletion paths must skip |

Each tool keeps its own `LOG_FORMAT`, `configure_logging`, `AppConfig`, and
`load_config`; this package only provides the underlying primitives. The
`usage_reports` retry policy (denylist: retry anything non-permanent) has
different semantics from this package's opt-in transient classifier and is
**deliberately not** unified (see
`tools/usage_reports/src/usage_reports/retry.py`).

## Packaging and vendoring

This package is **not** published to PyPI and does **not** appear in any
tool's `pyproject.toml` dependencies or `requirements.lock` (the hash-pinned
lockfiles are generated with `uv pip compile pyproject.toml`; declaring it
would make resolution reach for PyPI, a dependency-confusion hazard on a
public repo):

- **Image builds**: `.github/workflows/build-tools.yml` rsyncs the package
  into each consuming tool's build context (`vendor/infra_labs_common/`);
  the Dockerfiles install it with `pip install --no-deps`.
- **Local development**: `ansible/scripts/validate.sh` installs it by path
  (`pip install --no-deps -e`) into each tool's `.venv`.

`dependencies = []` is deliberate: the only module needing third-party
packages is `retry` (tenacity / requests / keystoneauth1), which the
consuming tools' lockfiles already pin; the stdlib-only consumer
(`period_reconcile`) never imports `retry` and needs nothing.

## Tests

```bash
cd tools/infra_labs_common
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

`ansible/scripts/validate.sh` covers this package automatically (pytest with
a 95% coverage gate, plus ruff check).
