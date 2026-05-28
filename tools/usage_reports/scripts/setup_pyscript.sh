#!/usr/bin/env bash
# Bootstrap CloudKitty pyscripts rating for the infra-labs cluster.
#
# What this does, in order:
#   1. Enables the pyscripts rating module and bumps its priority above
#      hashmap so that if anyone re-enables hashmap by mistake, pyscripts
#      still runs first.
#   2. Uploads tools/usage_reports/scripts/rate.py (or updates the existing
#      stored script if a script with the same name already exists).
#   3. Disables the hashmap rating module and deletes any leftover hashmap
#      services, fields, groups, and mappings so two rate cards do not
#      coexist.
#
# Why pyscripts and not hashmap: see the header of rate.py.
#
# Idempotent. Safe to re-run after editing rate.py to push a new version.
#
# Prerequisites:
#   - openstack CLI with admin scope and the rating service catalog entry
#   - python-cloudkittyclient installed in the same venv as the openstack
#     CLI (otherwise `openstack rating ...` subcommands are missing)
#   - jq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RATE_PY="${SCRIPT_DIR}/rate.py"
SCRIPT_NAME="${SCRIPT_NAME:-infra-labs-rate}"
PRIORITY="${PRIORITY:-10}"

log() { printf '[setup_pyscript] %s\n' "$*"; }

command -v openstack >/dev/null || { log "ERROR: openstack CLI is required"; exit 1; }
command -v jq        >/dev/null || { log "ERROR: jq is required"; exit 1; }
[[ -f "$RATE_PY" ]]              || { log "ERROR: rate.py not found at $RATE_PY"; exit 1; }

# Fail loud if the rating plugin is missing rather than no-op'ing the way
# the older setup_hashmap.sh did before its first OSC call.
if ! openstack rating module list >/dev/null 2>&1; then
  log "ERROR: 'openstack rating' subcommands not available"
  log "       install python-cloudkittyclient into the active venv and retry"
  exit 1
fi

# --- 1. Activate pyscripts -------------------------------------------------

log "enabling pyscripts module"
openstack rating module enable pyscripts >/dev/null

log "setting pyscripts priority=${PRIORITY}"
openstack rating module set priority pyscripts "${PRIORITY}" >/dev/null

# --- 2. (Re-)upload rate.py -----------------------------------------------
# The CLI is `openstack rating pyscript` (singular, no `script` subword);
# `create NAME DATA` accepts either a literal string or a file path.
#
# We always delete + recreate rather than `pyscript update`: once a script
# has started running, cloudkitty rejects updates with HTTP 400 "You are
# allowed to update only the attribute [end] as this rule is already
# running". Delete is unconditional; create is a clean slate.
# `-f json` sidesteps the canonical column-order gotcha that bit
# setup_hashmap.sh's awk-on-`-f value` lookups.

existing_id="$(openstack rating pyscript list -f json \
  | jq -r --arg n "$SCRIPT_NAME" '.[] | select(.Name == $n) | .["Script ID"]' \
  | head -n1)"

if [[ -n "$existing_id" ]]; then
  log "deleting existing pyscript ${SCRIPT_NAME} (${existing_id})"
  openstack rating pyscript delete "$existing_id" >/dev/null
fi

log "creating pyscript ${SCRIPT_NAME}"
openstack rating pyscript create "$SCRIPT_NAME" "$RATE_PY" >/dev/null

# --- 3. Decommission hashmap ----------------------------------------------

log "disabling hashmap module"
openstack rating module disable hashmap >/dev/null || true

# Delete every hashmap mapping. `openstack rating hashmap mapping list`
# rejects calls without a filter ("must provide field_id, service_id or
# group_id"), so enumerate from each parent type. Mappings double-listed
# across iterations are tolerated -- the second delete just 404s and the
# pipe to `2>/dev/null` swallows the noise.
delete_mapping() {
  local mid="$1" tag="$2"
  [[ -z "$mid" ]] && return 0
  if openstack rating hashmap mapping delete "$mid" 2>/dev/null; then
    log "  deleted mapping $mid ($tag)"
  fi
}

log "deleting hashmap mappings (by group)"
openstack rating hashmap group list -f json 2>/dev/null \
  | jq -r '.[] | .["Group ID"]' \
  | while read -r gid; do
      [[ -z "$gid" ]] && continue
      openstack rating hashmap mapping list --group-id "$gid" -f json 2>/dev/null \
        | jq -r '.[] | .["Mapping ID"]' \
        | while read -r mid; do delete_mapping "$mid" "group=$gid"; done
    done

log "deleting hashmap mappings (by field) + fields"
openstack rating hashmap service list -f json 2>/dev/null \
  | jq -r '.[] | .["Service ID"]' \
  | while read -r sid; do
      [[ -z "$sid" ]] && continue
      openstack rating hashmap field list "$sid" -f json 2>/dev/null \
        | jq -r '.[] | .["Field ID"]' \
        | while read -r fid; do
            [[ -z "$fid" ]] && continue
            openstack rating hashmap mapping list --field-id "$fid" -f json 2>/dev/null \
              | jq -r '.[] | .["Mapping ID"]' \
              | while read -r mid; do delete_mapping "$mid" "field=$fid"; done
            openstack rating hashmap field delete "$fid" >/dev/null \
              && log "  deleted field $fid"
          done
    done

log "deleting hashmap mappings (by service)"
openstack rating hashmap service list -f json 2>/dev/null \
  | jq -r '.[] | .["Service ID"]' \
  | while read -r sid; do
      [[ -z "$sid" ]] && continue
      openstack rating hashmap mapping list --service-id "$sid" -f json 2>/dev/null \
        | jq -r '.[] | .["Mapping ID"]' \
        | while read -r mid; do delete_mapping "$mid" "service=$sid"; done
    done

log "deleting hashmap services"
openstack rating hashmap service list -f json 2>/dev/null \
  | jq -r '.[] | .["Service ID"]' \
  | while read -r sid; do
      [[ -z "$sid" ]] && continue
      openstack rating hashmap service delete "$sid" >/dev/null \
        && log "  deleted service $sid"
    done

log "deleting hashmap groups"
openstack rating hashmap group list -f json 2>/dev/null \
  | jq -r '.[] | .["Group ID"]' \
  | while read -r gid; do
      [[ -z "$gid" ]] && continue
      openstack rating hashmap group delete "$gid" >/dev/null \
        && log "  deleted group $gid"
    done

log "bootstrap complete"
log "next: wait one collection period (~10 min), then verify with:"
log "    openstack rating summary get -b <begin> -e <end>"
log "    expect non-zero 'rate' rows for projects with active VMs"
