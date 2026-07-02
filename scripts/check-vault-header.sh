#!/usr/bin/env bash
# Pre-commit hook: kolla/passwords.yml must always be Ansible Vault encrypted.
# gitleaks scans for known secret patterns, but a plaintext Kolla passwords
# file is a structured YAML document, not a recognizable secret string -- it
# would slip straight through. Only the first line is inspected, so this
# never reads or prints the encrypted (or, if someone slipped up, plaintext)
# credential contents.
set -euo pipefail

status=0
for file in "$@"; do
  if [ ! -f "$file" ]; then
    continue
  fi
  if ! head -n 1 -- "$file" | grep -q '^\$ANSIBLE_VAULT'; then
    echo "ERROR: $file is not Ansible Vault encrypted (first line must start with \$ANSIBLE_VAULT)." >&2
    echo "Encrypt it before committing: ansible-vault encrypt $file" >&2
    status=1
  fi
done

exit "$status"
