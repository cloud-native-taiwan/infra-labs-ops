#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="${ROOT_DIR}/.venv/bin"

PLAYBOOKS=(playbooks/bootstrap.yml playbooks/bbr.yml playbooks/exporter.yml playbooks/gpu-monitor.yml playbooks/pci-pass.yml playbooks/swap.yml playbooks/upgrade.yml)

mkdir -p "${ROOT_DIR}/.ansible/tmp"
mkdir -p "${ROOT_DIR}/.cache"

export XDG_CACHE_HOME="${ROOT_DIR}/.cache"
export ANSIBLE_LOCAL_TEMP="${ROOT_DIR}/.ansible/tmp"
export ANSIBLE_CONFIG="${ROOT_DIR}/ansible.cfg"
export ANSIBLE_GALAXY_CACHE_DIR="${ROOT_DIR}/.cache/galaxy"

mkdir -p "${ANSIBLE_GALAXY_CACHE_DIR}"

if [ ! -d "${ROOT_DIR}/collections/ansible_collections/community/docker" ]; then
  "${VENV_BIN}/ansible-galaxy" collection install -r "${ROOT_DIR}/collections/requirements.yml" -p "${ROOT_DIR}/collections"
fi

"${VENV_BIN}/yamllint" .
"${VENV_BIN}/ansible-lint" "${PLAYBOOKS[@]}"
"${VENV_BIN}/python" scripts/validate_inventory.py
"${VENV_BIN}/python" scripts/render_templates.py
