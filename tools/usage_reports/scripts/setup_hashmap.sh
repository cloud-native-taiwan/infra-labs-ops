#!/usr/bin/env bash
# Configure the CloudKitty hashmap rate card for the cluster.
#
# This script is idempotent: re-running it skips existing groups, services,
# fields, and mappings. Adjust the *_RATE variables below to change pricing,
# then re-run; only new mappings are appended.
#
# After editing rates for periods already collected, re-rate historical
# data via CloudKitty's v2 reprocessing API (POST /v2/task/reprocesses)
# (see docs/runbooks/cloudkitty-rate-card.md).
#
# Prerequisites:
#   - `openstack` CLI with admin scope and the `rating` service catalog entry
#   - CloudKitty deployed with the hashmap rating module enabled
#
# Rate formula:
#   The collection period is 600 seconds (10 minutes).
#   per_period_cost = desired_hourly_rate / 6
#
# Edit the rate values below. The placeholders here express relative
# weighting between hardware tiers; replace with the operator's actual
# cost-recovery numbers before production use.

set -euo pipefail

log() { printf '[setup_hashmap] %s\n' "$*"; }

# --- Rate placeholders (per 600s collection period) -------------------------
# Compute: priced by flavor. Heavier CPU generations cost more per period.
# Replace flavor names with the actual flavor names in this cluster.
declare -A COMPUTE_RATES=(
  # Example: m1.small on legacy hardware
  # ["m1.small"]="0.0050"
  # Example: c1.large on newer-gen CPUs
  # ["c1.large"]="0.0200"
)

# GPU flavors: priced per period, GPU-type specific.
declare -A GPU_RATES=(
  # Example: g1.intel-arc-b50
  # ["g1.intel-arc-b50"]="0.0500"
  # Example: g1.nvidia-a5000-24q
  # ["g1.nvidia-a5000-24q"]="0.1500"
)

# Storage: flat per-GiB-period rate, independent of volume type.
STORAGE_RATE_PER_GIB_PERIOD="0.0001"

# --- Helpers ---------------------------------------------------------------

# Idempotency: check whether a hashmap object exists by name before creating.
ensure_group() {
  local name="$1"
  if openstack rating hashmap group list -f value -c Name | grep -qx "${name}"; then
    log "group ${name} already exists"
    return 0
  fi
  log "creating group ${name}"
  openstack rating hashmap group create "${name}"
}

ensure_service() {
  local name="$1"
  if openstack rating hashmap service list -f value -c Name | grep -qx "${name}"; then
    log "service ${name} already exists"
    return 0
  fi
  log "creating service ${name}"
  openstack rating hashmap service create "${name}"
}

service_id() {
  openstack rating hashmap service list -f value -c "Service ID" -c Name \
    | awk -v n="$1" '$2 == n { print $1 }'
}

group_id() {
  openstack rating hashmap group list -f value -c "Group ID" -c Name \
    | awk -v n="$1" '$2 == n { print $1 }'
}

ensure_field() {
  local svc_id="$1"
  local field_name="$2"
  if openstack rating hashmap field list "${svc_id}" -f value -c Name \
      | grep -qx "${field_name}"; then
    log "field ${field_name} already exists on service ${svc_id}"
    return 0
  fi
  log "creating field ${field_name} on service ${svc_id}"
  openstack rating hashmap field create "${svc_id}" "${field_name}"
}

field_id() {
  local svc_id="$1"
  local field_name="$2"
  openstack rating hashmap field list "${svc_id}" -f value -c "Field ID" -c Name \
    | awk -v n="${field_name}" '$2 == n { print $1 }'
}

ensure_field_mapping() {
  local field="$1"
  local value="$2"
  local cost="$3"
  local grp="$4"
  if openstack rating hashmap mapping list --field "${field}" -f value \
      -c Value -c Cost \
      | awk -v v="${value}" -v c="${cost}" \
            '$1 == v && $2 == c { found=1 } END { exit !found }'; then
    log "mapping ${value}=${cost} already exists on field ${field}"
    return 0
  fi
  log "creating mapping ${value} -> ${cost} on field ${field} (group ${grp})"
  openstack rating hashmap mapping create \
    --field-id "${field}" \
    --value "${value}" \
    --type flat \
    --group-id "${grp}" \
    "${cost}"
}

ensure_service_mapping() {
  local svc="$1"
  local cost="$2"
  local grp="$3"
  if openstack rating hashmap mapping list --service "${svc}" -f value -c Cost \
      | grep -qx "${cost}"; then
    log "service mapping cost=${cost} already exists on service ${svc}"
    return 0
  fi
  log "creating service mapping cost=${cost} on service ${svc} (group ${grp})"
  openstack rating hashmap mapping create \
    --service-id "${svc}" \
    --type flat \
    --group-id "${grp}" \
    "${cost}"
}

# --- Build the rate card ---------------------------------------------------

# Idempotent and fail-loud: enable the module only if present-and-disabled.
# A missing module (CloudKitty not deployed / catalog wrong) or a real
# enable failure must abort -- silently continuing seeds a rate card that
# never rates anything, producing zero-cost reports with no signal.
ensure_module_enabled() {
  local module="$1" state
  state=$(openstack rating module list -f value -c Module -c Enabled \
    | awk -v m="${module}" '$1 == m { print $2 }')
  if [[ -z "${state}" ]]; then
    log "ERROR: rating module '${module}' not found; is CloudKitty deployed and is the rating catalog entry present?" >&2
    return 1
  fi
  if [[ "${state}" == "True" ]]; then
    log "rating module ${module} already enabled"
    return 0
  fi
  log "enabling rating module ${module}"
  openstack rating module enable "${module}" >/dev/null
}

ensure_module_enabled hashmap

ensure_group compute
ensure_group gpu
ensure_group storage

ensure_service instance
ensure_service storage

INSTANCE_SVC_ID="$(service_id instance)"
STORAGE_SVC_ID="$(service_id storage)"
COMPUTE_GRP_ID="$(group_id compute)"
GPU_GRP_ID="$(group_id gpu)"
STORAGE_GRP_ID="$(group_id storage)"

ensure_field "${INSTANCE_SVC_ID}" flavor_name
FLAVOR_FIELD_ID="$(field_id "${INSTANCE_SVC_ID}" flavor_name)"

log "applying compute rate mappings"
for flavor in "${!COMPUTE_RATES[@]}"; do
  ensure_field_mapping "${FLAVOR_FIELD_ID}" "${flavor}" \
    "${COMPUTE_RATES[${flavor}]}" "${COMPUTE_GRP_ID}"
done

log "applying GPU rate mappings"
for flavor in "${!GPU_RATES[@]}"; do
  ensure_field_mapping "${FLAVOR_FIELD_ID}" "${flavor}" \
    "${GPU_RATES[${flavor}]}" "${GPU_GRP_ID}"
done

log "applying storage rate (per GiB per 600s period)"
ensure_service_mapping "${STORAGE_SVC_ID}" "${STORAGE_RATE_PER_GIB_PERIOD}" \
  "${STORAGE_GRP_ID}"

# --- Audit: flavors with no mapping ---------------------------------------

log "checking for active flavors with no compute/GPU mapping"
mapped=$(printf '%s\n' "${!COMPUTE_RATES[@]}" "${!GPU_RATES[@]}" | sort -u)
active=$(openstack flavor list -f value -c Name | sort -u)
unmapped=$(comm -23 <(echo "${active}") <(echo "${mapped}") || true)

if [[ -n "${unmapped}" ]]; then
  log "WARN: the following flavors have no rate mapping and will be billed at 0:"
  while IFS= read -r flav; do
    [[ -n "${flav}" ]] && log "  - ${flav}"
  done <<<"${unmapped}"
  log "Edit COMPUTE_RATES / GPU_RATES in this script to cover them."
else
  log "all active flavors have a rate mapping"
fi

log "rate card setup complete"
