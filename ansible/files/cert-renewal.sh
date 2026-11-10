#!/usr/bin/env bash

set -euo pipefail

CERT_NAME="cloudnative.tw"
# Paths are overridable only so tests/test_cert_renewal_script.py can run the
# script against a scratch tree; the systemd unit never sets them.
LIVE_DIR="${LIVE_DIR:-/etc/letsencrypt/live/${CERT_NAME}}"
FULLCHAIN_PEM="${LIVE_DIR}/fullchain.pem"
PRIVKEY_PEM="${LIVE_DIR}/privkey.pem"
HAPROXY_PEM="${HAPROXY_PEM:-/etc/kolla/certificates/haproxy.pem}"
TMP_PEM="${HAPROXY_PEM}.tmp"
# Fingerprint of the last cert successfully pushed to every consumer; written
# only after all of them succeed, so any failed step is retried on the next run.
DEPLOYED_STAMP="${HAPROXY_PEM}.deployed"
# Local consumers on the deploy host that copy the cert instead of reading
# /etc/letsencrypt live: the edge HAProxy bundle (roles/haproxy) and Harbor,
# whose ./prepare copies harbor.yml's cert paths into /data/secret/cert.
# prepare also regenerates Harbor's internal secrets, so every container whose
# env changed must be recreated (compose up -d), and the proxy restarted.
# The three consumers (edge, Harbor, kolla fleet) are refreshed independently:
# a broken Harbor must not leave the fleet serving an expiring cert. Any
# failure still fails the run (no stamp, non-zero exit, so the unit's
# ExecStartPost success metric is not written).
EDGE_PEM="${EDGE_PEM:-/etc/haproxy/certs/${CERT_NAME}.pem}"
# kolla-ansible must run as the deploy user, whose SSH key is authorized on
# the fleet; certbot itself still needs root for /etc/letsencrypt.
KOLLA_USER="${KOLLA_USER:?KOLLA_USER must be set (deploy user for kolla-ansible)}"
HARBOR_DIR="${HARBOR_DIR:-/home/${KOLLA_USER}/harbor}"
KOLLA_CMD=(
  runuser -u "${KOLLA_USER}" --
  env "HOME=/home/${KOLLA_USER}"
  kolla-ansible
  reconfigure
  -i /etc/kolla/multinode
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

# Explicit && chains: the step functions run under `if !` in run_step, where
# set -e is suspended, so a failed command must stop its step by itself.
write_pem_bundle() {
  local dest="$1"
  (umask 077 && cat "${FULLCHAIN_PEM}" "${PRIVKEY_PEM}" > "${TMP_PEM}") \
    && mv "${TMP_PEM}" "${dest}" \
    && chmod 600 "${dest}"
}

refresh_edge() {
  write_pem_bundle "${EDGE_PEM}" && systemctl reload haproxy
}

refresh_harbor() {
  # up -d only recreates containers whose config changed; the cert is a bind
  # mount, so nginx in the proxy needs an explicit restart to load it.
  (cd "${HARBOR_DIR}" && ./prepare && docker compose up -d && docker compose restart proxy)
}

push_kolla() {
  write_pem_bundle "${HAPROXY_PEM}" \
    && chown "${KOLLA_USER}:" "${HAPROXY_PEM}" \
    && "${KOLLA_CMD[@]}"
}

failed_steps=()

run_step() {
  local name="$1" description="$2"
  shift 2
  echo "${description}"
  if ! "$@"; then
    echo "Step failed: ${name}" >&2
    failed_steps+=("${name}")
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

deployed_fingerprint="$(cat "${DEPLOYED_STAMP}" 2>/dev/null || true)"

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

run_step edge "Refreshing deploy host edge HAProxy bundle" refresh_edge
run_step harbor "Refreshing Harbor certificate (prepare + compose up -d + proxy restart)" refresh_harbor
run_step kolla "Writing HAProxy PEM and running kolla-ansible haproxy reconfigure" push_kolla

if (( ${#failed_steps[@]} > 0 )); then
  echo "Certificate renewal incomplete, failed step(s): ${failed_steps[*]}." \
    "Not recording ${le_fingerprint} as deployed; the next run retries every step." >&2
  exit 1
fi

echo "${le_fingerprint}" > "${DEPLOYED_STAMP}"
echo "Certificate renewal workflow completed successfully"
