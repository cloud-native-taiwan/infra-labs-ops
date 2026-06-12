#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
VENV_BIN="${REPO_ROOT}/.venv/bin"

PLAYBOOKS=(playbooks/bootstrap.yml playbooks/bbr.yml playbooks/exporter.yml playbooks/gpu-monitor.yml playbooks/pci-pass.yml playbooks/swap.yml playbooks/upgrade.yml playbooks/apply-tuning.yml playbooks/verify-tuning.yml playbooks/setup-mariadb-backup.yml playbooks/setup-cert-renewal.yml playbooks/deploy-haproxy.yml playbooks/ceph-audit.yml playbooks/ceph-apply.yml playbooks/ceph-verify.yml)

mkdir -p "${REPO_ROOT}/.ansible/tmp"
mkdir -p "${REPO_ROOT}/.cache"

export XDG_CACHE_HOME="${REPO_ROOT}/.cache"
export ANSIBLE_LOCAL_TEMP="${REPO_ROOT}/.ansible/tmp"
export ANSIBLE_CONFIG="${ROOT_DIR}/ansible.cfg"
export ANSIBLE_GALAXY_CACHE_DIR="${REPO_ROOT}/.cache/galaxy"

mkdir -p "${ANSIBLE_GALAXY_CACHE_DIR}"

cd "${ROOT_DIR}"

if [ ! -d "${ROOT_DIR}/collections/ansible_collections/community/docker" ]; then
  "${VENV_BIN}/ansible-galaxy" collection install -r "${ROOT_DIR}/collections/requirements.yml" -p "${ROOT_DIR}/collections"
fi

"${VENV_BIN}/yamllint" .
"${VENV_BIN}/ansible-lint" "${PLAYBOOKS[@]}"
"${VENV_BIN}/python" scripts/validate_inventory.py
"${VENV_BIN}/python" scripts/render_templates.py
"${VENV_BIN}/python" -m pytest tests/test_ceph_schema.py tests/test_health_gate.py -v

# Validate Python tools under tools/
for tool_dir in "${REPO_ROOT}"/tools/*/; do
  [ -f "${tool_dir}/pyproject.toml" ] || continue
  tool_name="$(basename "${tool_dir}")"
  echo "=== Validating tool: ${tool_name} ==="

  tool_venv="${tool_dir}/.venv"
  if [ ! -d "${tool_venv}" ] || [ "${tool_dir}/pyproject.toml" -nt "${tool_venv}/.install-stamp" ]; then
    python3 -m venv "${tool_venv}"
    "${tool_venv}/bin/pip" install -e "${tool_dir}[dev]" --quiet
    touch "${tool_venv}/.install-stamp"
  fi

  (cd "${tool_dir}" && "${tool_venv}/bin/python" -m pytest -v)
  "${tool_venv}/bin/ruff" check "${tool_dir}/src" "${tool_dir}/tests"
done
