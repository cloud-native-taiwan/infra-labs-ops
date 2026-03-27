#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
VENV_BIN="${REPO_ROOT}/.venv/bin"

PLAYBOOKS=(playbooks/bootstrap.yml playbooks/bbr.yml playbooks/exporter.yml playbooks/gpu-monitor.yml playbooks/pci-pass.yml playbooks/swap.yml playbooks/upgrade.yml)

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
