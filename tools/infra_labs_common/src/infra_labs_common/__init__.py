"""Shared helpers for CNTUG Infra Labs operational tools.

Import submodules directly (e.g. ``from infra_labs_common.redaction import
SecretRedactingFilter``). This package deliberately performs no eager
submodule imports so that a stdlib-only consumer (period_reconcile) can import
the stdlib-only pieces without pulling in ``retry`` -- whose third-party
dependencies (tenacity / requests / keystoneauth1) it does not install.
"""
