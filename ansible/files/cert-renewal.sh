#!/usr/bin/env bash

set -euo pipefail

CERT_NAME="cloudnative.tw"
LIVE_DIR="/etc/letsencrypt/live/${CERT_NAME}"
FULLCHAIN_PEM="${LIVE_DIR}/fullchain.pem"
PRIVKEY_PEM="${LIVE_DIR}/privkey.pem"
HAPROXY_PEM="/etc/kolla/certificates/haproxy.pem"
TMP_PEM="${HAPROXY_PEM}.tmp"
KOLLA_CMD=(
  kolla-ansible
  -i /etc/kolla/multinode
  reconfigure
  --vault-password-file /etc/kolla/ansible_vault_pass
  -t haproxy
)

cleanup() {
  rm -f "${TMP_PEM}"
}

cert_fingerprint() {
  local cert_path="$1"

  if [[ ! -f "${cert_path}" ]]; then
    return 0
  fi

  openssl x509 -in "${cert_path}" -noout -fingerprint -sha256 | cut -d= -f2
}

require_expected_lineage() {
  if ! openssl x509 -in "${FULLCHAIN_PEM}" -noout -ext subjectAltName \
    | grep -Eq 'DNS:(\*\.)?cloudnative\.tw([[:space:]]|,|$)'; then
    echo "Renewed certificate does not match expected cloudnative.tw lineage" >&2
    exit 1
  fi
}

trap cleanup EXIT

echo "Starting certbot renewal for ${CERT_NAME}"
certbot renew --cert-name "${CERT_NAME}"

le_fingerprint="$(cert_fingerprint "${FULLCHAIN_PEM}")"

if [[ -z "${le_fingerprint}" ]]; then
  echo "Missing certificate at ${FULLCHAIN_PEM}" >&2
  exit 1
fi

deployed_fingerprint="$(cert_fingerprint "${HAPROXY_PEM}")"

if [[ "${le_fingerprint}" == "${deployed_fingerprint}" ]]; then
  echo "Deployed cert matches Let's Encrypt cert for ${CERT_NAME}; nothing to do"
  exit 0
fi

require_expected_lineage

if [[ -f "${HAPROXY_PEM}" ]]; then
  backup_path="${HAPROXY_PEM}.$(date -u +%Y%m%dT%H%M%SZ).bak"
  cp -p "${HAPROXY_PEM}" "${backup_path}"
  echo "Backed up existing HAProxy PEM to ${backup_path}"
else
  echo "No existing HAProxy PEM found at ${HAPROXY_PEM}; skipping backup"
fi

echo "Writing updated HAProxy PEM atomically"
(
  umask 077
  cat "${FULLCHAIN_PEM}" "${PRIVKEY_PEM}" > "${TMP_PEM}"
)
mv "${TMP_PEM}" "${HAPROXY_PEM}"
chmod 600 "${HAPROXY_PEM}"

echo "Running kolla-ansible haproxy reconfigure"
"${KOLLA_CMD[@]}"
echo "Certificate renewal workflow completed successfully"
