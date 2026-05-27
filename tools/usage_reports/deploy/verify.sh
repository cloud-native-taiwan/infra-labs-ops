#!/usr/bin/env bash
set -euo pipefail

errors=0

fail() {
  echo "FAIL: $1"
  errors=$((errors + 1))
}

[ -f .env ] || fail ".env not found (copy from .env.example)"
[ -f secrets/clouds.yaml ] || fail "secrets/clouds.yaml not found"

# Keep in sync with src/usage_reports/config.py required_names
if [ -f .env ]; then
  for var in INFRA_LABS_RESEND_API_KEY INFRA_LABS_RESEND_FROM_EMAIL; do
    grep -q "^${var}=.\+" .env || fail "${var} not set or empty in .env"
  done
fi

if ! command -v docker > /dev/null 2>&1; then
  fail "docker not found"
elif ! docker compose version > /dev/null 2>&1; then
  fail "'docker compose' plugin not found (required by deployment commands)"
fi

if [ "$errors" -gt 0 ]; then
  echo ""
  echo "${errors} check(s) failed. Fix the above issues before deploying."
  exit 1
fi

echo "All pre-deploy checks passed."
