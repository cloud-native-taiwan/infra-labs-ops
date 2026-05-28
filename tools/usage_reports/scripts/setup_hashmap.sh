#!/usr/bin/env bash
# Configure the CloudKitty hashmap rate card for the cluster.
#
# This script is idempotent: re-running it skips existing groups, services,
# fields, and mappings. Adjust the rate variables below to change pricing,
# then re-run; only new mappings are appended.
#
# Set DRY_RUN=1 to preview every mapping it WOULD create (and the flavor
# parsing) without calling any `create` command. Run this first against the
# live cluster to validate flavor/GPU detection before applying.
#
# After editing rates for periods already collected, re-rate historical
# data via CloudKitty's v2 reprocessing API (POST /v2/task/reprocesses)
# (see docs/runbooks/cloudkitty-rate-card.md).
#
# Prerequisites:
#   - bash 4+ (associative arrays)
#   - `openstack` CLI with admin scope and the `rating` service catalog entry
#   - `jq` (parses `openstack flavor list --long -f json`)
#   - CloudKitty deployed with the hashmap rating module enabled
#
# ---------------------------------------------------------------------------
# Pricing model (showback, not billing)
# ---------------------------------------------------------------------------
# Anchored at ~65% of the budget cloud tier (DigitalOcean / Vultr), which is
# itself well below hyperscalers. We run with NO uptime SLA and no storage
# QoS, so the honest peers are budget/LowEndBox providers, not AWS/GCP -- this
# lands us between budget VPS (~$20-24/mo for 2 vCPU/4 GB) and the no-SLA
# LowEndBox floor (~$5/mo). Provider-neutral (R10). The goal is awareness
# ("this free infrastructure has real value") and a nudge to clean up idle
# VMs -- NOT cost recovery. Dial the whole card up or down with MULTIPLIER.
#
# Compute is rated PER flavor, keyed on flavor_id (the exporter does not
# guarantee a flavor_name label on openstack_nova_server_status). Each
# flavor's flat per-period cost is DERIVED from its vCPU and RAM via the unit
# rates below, so new flavors are covered automatically -- no hand-maintained
# per-flavor table.
#
#   hourly_cost = VCPU_RATE_HOUR * vcpus + RAM_RATE_GB_HOUR * ram_gib
#   per_period  = hourly_cost / PERIODS_PER_HOUR        (period = 600 s)
#
# GPU flavors additionally get a per-hour adder keyed on their Nova PCI alias
# (see kolla/config/nova/*/nova.conf). Storage is a single blended GiB rate
# (Ceph RBD: 3x replication but NO QoS/IOPS guarantee, and the collected
# metric is a project aggregate with no volume-type split).
#
# Network is intentionally not metered (R6); sustained upstream is ~2 Gb/s,
# so egress-heavy workloads are discouraged but not billed.
#
# Rate rationale and commercial citations: docs/runbooks/cloudkitty-rate-card.md

set -euo pipefail

# Associative arrays (GPU_RATE_HOUR) require bash 4+. macOS ships 3.2; the
# deploy host is Linux, but fail loud rather than with a cryptic error.
if (( BASH_VERSINFO[0] < 4 )); then
  echo "[setup_hashmap] ERROR: bash 4+ required (found ${BASH_VERSION})" >&2
  exit 1
fi

log() { printf '[setup_hashmap] %s\n' "$*"; }

DRY_RUN="${DRY_RUN:-0}"

# --- Rates -----------------------------------------------------------------
# Global multiplier applied to every compute, GPU, and storage rate. 1.0 =
# the rates as written below; lower it (e.g. 0.5) for an even gentler nudge.
MULTIPLIER="${MULTIPLIER:-1.0}"

# Compute unit rates, USD per hour. ~65% of budget VPS (DigitalOcean/Vultr):
# 2 vCPU/4 GB -> $0.020/hr (~$15/mo) vs budget ~$22/mo. Keeps a vCPU+RAM split
# (RAM rate low: RAM is plentiful here, cores/instances are the scarce
# resource the report should nudge users to free).
VCPU_RATE_HOUR="0.006"
RAM_RATE_GB_HOUR="0.002"

# Storage, USD per GiB per month (730 h). ~65% of blended budget block storage
# (DO/Vultr ~$0.05-0.10/GiB-mo); reflects replicated Ceph RBD with no SLA and
# no QoS/IOPS guarantee.
STORAGE_RATE_GB_MONTH="0.04"

# GPU per-hour adders, keyed by Nova PCI alias (kolla/config/nova/*/nova.conf).
# A flavor requesting N of an alias gets N x this rate on top of its compute.
# NOT discounted to 65% like compute/storage: these are already anchored to
# the community marketplace (RunPod/Vast), which is itself the no-SLA floor
# for these cards, and GPUs are the scarce resource worth keeping salient.
declare -A GPU_RATE_HOUR=(
  ["TeslaT10"]="0.25"               # T4/T10-class community inference GPU
  ["NVIDIA-A5000-24Q"]="0.25"       # full A5000 24 GB; RunPod/Vast community on-demand
  ["NVIDIA-A5000-12Q"]="0.125"      # half A5000 12 GB slice
  ["Intel-Arc-Pro-B50-VF"]="0.15"   # no cloud reference; T-class by INT8 TOPS, MSRP $349
)

# CPU-generation discount (R4). When a flavor name matches OLDER_GEN_REGEX, its
# compute rate is multiplied by OLDER_GEN_MULTIPLIER. Disabled by default: the
# current flavors are not aggregate-pinned, so a flavor's generation is not
# deterministic. Enable once generation-specific flavors exist (e.g. a
# ".gen2" suffix pinned to an openstack01/02 host aggregate).
OLDER_GEN_REGEX="${OLDER_GEN_REGEX:-}"
OLDER_GEN_MULTIPLIER="0.8"

# Collection period (must match `period` in kolla/config/cloudkitty.conf).
PERIOD_SECONDS=600
PERIODS_PER_HOUR=$(awk -v s="${PERIOD_SECONDS}" 'BEGIN { print 3600 / s }')
PERIODS_PER_MONTH=$(awk -v s="${PERIOD_SECONDS}" 'BEGIN { print 730 * 3600 / s }')

# --- Helpers ---------------------------------------------------------------

# Idempotency: check whether a hashmap object exists by name before creating.
ensure_group() {
  local name="$1"
  if openstack rating hashmap group list -f value -c Name | grep -qx "${name}"; then
    log "group ${name} already exists"
    return 0
  fi
  log "creating group ${name}"
  [[ "${DRY_RUN}" == "1" ]] || openstack rating hashmap group create "${name}"
}

ensure_service() {
  local name="$1"
  if openstack rating hashmap service list -f value -c Name | grep -qx "${name}"; then
    log "service ${name} already exists"
    return 0
  fi
  log "creating service ${name}"
  [[ "${DRY_RUN}" == "1" ]] || openstack rating hashmap service create "${name}"
}

service_id() {
  # OSC `-f value` emits columns in the resource's canonical order (Name,
  # then Service ID), NOT in the order requested via `-c`. Match on Name
  # ($1) and print the ID ($2).
  openstack rating hashmap service list -f value -c Name -c "Service ID" \
    | awk -v n="$1" '$1 == n { print $2 }'
}

group_id() {
  openstack rating hashmap group list -f value -c Name -c "Group ID" \
    | awk -v n="$1" '$1 == n { print $2 }'
}

ensure_field() {
  local svc_id="$1"
  local field_name="$2"
  if [[ -z "${svc_id}" ]]; then
    # DRY_RUN on a fresh cluster: the service was never created, so we cannot
    # (and must not) query for its fields. Report intent and move on.
    log "would create field ${field_name} (service not yet created)"
    return 0
  fi
  if openstack rating hashmap field list "${svc_id}" -f value -c Name \
      | grep -qx "${field_name}"; then
    log "field ${field_name} already exists on service ${svc_id}"
    return 0
  fi
  log "creating field ${field_name} on service ${svc_id}"
  [[ "${DRY_RUN}" == "1" ]] || openstack rating hashmap field create "${svc_id}" "${field_name}"
}

field_id() {
  local svc_id="$1"
  local field_name="$2"
  # See service_id() for the rationale on column ordering.
  openstack rating hashmap field list "${svc_id}" -f value -c Name -c "Field ID" \
    | awk -v n="${field_name}" '$1 == n { print $2 }'
}

# Abort a real run if a just-created CloudKitty object is not yet queryable;
# an empty id would otherwise be passed to `create` and fail confusingly. In
# DRY_RUN nothing is created, so empty ids are expected and tolerated.
require_id() {
  if [[ -z "$1" && "${DRY_RUN}" != "1" ]]; then
    log "ERROR: $2 not found after creation"
    exit 1
  fi
  return 0
}

# Costs are derived floats, so compare numerically -- "0.18" and "0.180000"
# are the same mapping. The group is part of the match: a flavor_id carries
# both a compute-group and a gpu-group mapping, and they must not collide if
# their costs happen to be equal.
ensure_field_mapping() {
  local field="$1"
  local value="$2"
  local cost="$3"
  local grp="$4"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "would create mapping ${value} -> ${cost} (group ${grp})"
    return 0
  fi
  if openstack rating hashmap mapping list --field "${field}" -f value \
      -c Value -c Cost -c "Group ID" \
      | awk -v v="${value}" -v c="${cost}" -v g="${grp}" \
            '$1 == v && ($2 + 0) == (c + 0) && $3 == g { found = 1 } END { exit !found }'; then
    log "mapping ${value}=${cost} (group ${grp}) already exists"
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
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "would create service mapping cost=${cost} (group ${grp})"
    return 0
  fi
  if openstack rating hashmap mapping list --service "${svc}" -f value -c Cost -c "Group ID" \
      | awk -v c="${cost}" -v g="${grp}" '($1 + 0) == (c + 0) && $2 == g { found = 1 } END { exit !found }'; then
    log "service mapping cost=${cost} (group ${grp}) already exists on service ${svc}"
    return 0
  fi
  log "creating service mapping cost=${cost} on service ${svc} (group ${grp})"
  openstack rating hashmap mapping create \
    --service-id "${svc}" \
    --type flat \
    --group-id "${grp}" \
    "${cost}"
}

# compute_per_period <vcpus> <ram_mb> <flavor_name> -> per-period compute cost
compute_per_period() {
  local vcpus="$1" ram_mb="$2" name="$3" gen_mult="1.0"
  if [[ -n "${OLDER_GEN_REGEX}" && "${name}" =~ ${OLDER_GEN_REGEX} ]]; then
    gen_mult="${OLDER_GEN_MULTIPLIER}"
  fi
  awk -v vc="${vcpus}" -v ram="${ram_mb}" -v cpu="${VCPU_RATE_HOUR}" \
      -v rg="${RAM_RATE_GB_HOUR}" -v m="${MULTIPLIER}" -v g="${gen_mult}" \
      -v pph="${PERIODS_PER_HOUR}" \
      'BEGIN { printf "%.8f", (cpu * vc + rg * (ram / 1024)) * m * g / pph }'
}

# gpu_per_period <per_hour_rate> <count> -> per-period GPU adder
gpu_per_period() {
  awk -v r="$1" -v n="$2" -v m="${MULTIPLIER}" -v pph="${PERIODS_PER_HOUR}" \
      'BEGIN { printf "%.8f", r * n * m / pph }'
}

# --- Build the rate card ---------------------------------------------------

command -v openstack >/dev/null || { log "ERROR: openstack CLI is required"; exit 1; }
command -v jq >/dev/null || { log "ERROR: jq is required"; exit 1; }

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
  [[ "${DRY_RUN}" == "1" ]] || openstack rating module enable "${module}" >/dev/null
}

[[ "${DRY_RUN}" == "1" ]] && log "DRY_RUN: no objects will be created"
log "multiplier=${MULTIPLIER} vcpu/hr=${VCPU_RATE_HOUR} ram-gb/hr=${RAM_RATE_GB_HOUR} storage-gb/mo=${STORAGE_RATE_GB_MONTH}"

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
require_id "${INSTANCE_SVC_ID}" "service instance"
require_id "${STORAGE_SVC_ID}" "service storage"
require_id "${COMPUTE_GRP_ID}" "group compute"
require_id "${GPU_GRP_ID}" "group gpu"
require_id "${STORAGE_GRP_ID}" "group storage"

ensure_field "${INSTANCE_SVC_ID}" flavor_id
FLAVOR_FIELD_ID="$(field_id "${INSTANCE_SVC_ID}" flavor_id)"
require_id "${FLAVOR_FIELD_ID}" "field flavor_id"

# Auto-derive a compute mapping for every flavor, plus a GPU adder for any
# flavor that requests a known PCI alias. Materialize the flavor list to a
# temp file first: a failure in `openstack`/`jq` inside a `< <(...)` process
# substitution is invisible to the loop and would silently look like "zero
# flavors", masking the error and seeding an empty rate card.
log "deriving compute + GPU mappings from the live flavor list"
flavor_tsv="$(mktemp)"
trap 'rm -f "${flavor_tsv}"' EXIT
if ! openstack flavor list --long -f json \
    | jq -r '.[] | [.Name, .ID, (.VCPUs | tostring), (.RAM | tostring), (.Properties | tostring)] | @tsv' \
    > "${flavor_tsv}"; then
  log "ERROR: failed to enumerate flavors (openstack/jq)"
  exit 1
fi
unmapped_gpu=""
while IFS=$'\t' read -r f_name f_id f_vcpus f_ram f_props; do
  [[ -z "${f_id}" ]] && continue
  cost="$(compute_per_period "${f_vcpus}" "${f_ram}" "${f_name}")"
  log "compute: ${f_name} (${f_vcpus} vCPU, ${f_ram} MB) -> ${cost}/period"
  ensure_field_mapping "${FLAVOR_FIELD_ID}" "${f_id}" "${cost}" "${COMPUTE_GRP_ID}"

  # Extract "alias:count[,alias:count...]" from a pci_passthrough:alias property.
  alias_spec="$(printf '%s' "${f_props}" \
    | grep -oE "pci_passthrough:alias[^A-Za-z0-9]+[A-Za-z0-9:_,.-]+" \
    | sed -E "s/pci_passthrough:alias[^A-Za-z0-9]+//" || true)"
  [[ -z "${alias_spec}" ]] && continue
  IFS=',' read -ra aliases <<<"${alias_spec}"
  for entry in "${aliases[@]}"; do
    [[ -z "${entry}" ]] && continue
    a_name="${entry%%:*}"
    a_count="${entry##*:}"
    # No ":count" suffix (the strip is a no-op) means a single device.
    [[ "${a_count}" == "${a_name}" ]] && a_count=1
    if [[ -z "${GPU_RATE_HOUR[${a_name}]:-}" ]]; then
      unmapped_gpu+="${f_name} (${a_name})"$'\n'
      continue
    fi
    if ! [[ "${a_count}" =~ ^[0-9]+$ ]]; then
      log "WARN: ${f_name}: unparseable GPU count '${a_count}' for ${a_name}; skipping GPU charge"
      continue
    fi
    g_cost="$(gpu_per_period "${GPU_RATE_HOUR[${a_name}]}" "${a_count}")"
    log "gpu: ${f_name} -> ${a_name} x${a_count} -> ${g_cost}/period"
    ensure_field_mapping "${FLAVOR_FIELD_ID}" "${f_id}" "${g_cost}" "${GPU_GRP_ID}"
  done
done < "${flavor_tsv}"

log "applying storage rate (${STORAGE_RATE_GB_MONTH}/GiB/month)"
storage_cost="$(awk -v r="${STORAGE_RATE_GB_MONTH}" -v m="${MULTIPLIER}" \
  -v ppm="${PERIODS_PER_MONTH}" 'BEGIN { printf "%.8f", r * m / ppm }')"
ensure_service_mapping "${STORAGE_SVC_ID}" "${storage_cost}" "${STORAGE_GRP_ID}"

# --- Audit -----------------------------------------------------------------

if [[ -n "${unmapped_gpu}" ]]; then
  log "WARN: GPU flavors requesting an unpriced PCI alias (billed compute-only):"
  while IFS= read -r line; do
    [[ -n "${line}" ]] && log "  - ${line}"
  done <<<"${unmapped_gpu}"
  log "Add the alias to GPU_RATE_HOUR in this script to charge for it."
else
  log "all GPU flavor aliases are priced"
fi

log "rate card setup complete"
