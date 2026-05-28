#!/bin/bash
#
# Build Kolla images, push the per-arch tag, then create and push
# multi-arch manifests. The image list is derived from what kolla-build
# actually produced locally, so no hand-maintained list is required.
#
# Run on the kolla build host. Adjust REGISTRY/TAG below as needed.

set -euo pipefail

REGISTRY="registry.cloudnative.tw"
NAMESPACE="kolla"
BASE="ubuntu"
TAG="2026.1"
ARCH="amd64"
ARCH_TAG="${TAG}-${ARCH}"
VENV="/home/debian/dev/bin/activate"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Tracked build config. Passing --config-file makes oslo.config use ONLY
# this file and skip the default /etc/kolla/kolla-build.conf, so any
# host-side profile filter (which would silently drop cloudkitty) is
# bypassed and the image set is reproducible from the repo.
BUILD_CONF="${SCRIPT_DIR}/../kolla/kolla-build.conf"

if [[ ! -f "$BUILD_CONF" ]]; then
  echo "ERROR: build config not found at ${BUILD_CONF}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$VENV"

echo "==> Building and pushing ${REGISTRY}/${NAMESPACE}/*:${ARCH_TAG}"
kolla-build --config-file "$BUILD_CONF" -b "$BASE" --registry "$REGISTRY" --push --tag "$ARCH_TAG"

echo "==> Discovering images that were built"
mapfile -t IMAGES < <(
  docker images --format '{{.Repository}}' \
    --filter "reference=${REGISTRY}/${NAMESPACE}/*:${ARCH_TAG}" \
  | sed "s|^${REGISTRY}/${NAMESPACE}/||" \
  | sort -u
)

if [[ ${#IMAGES[@]} -eq 0 ]]; then
  echo "ERROR: no images found matching ${REGISTRY}/${NAMESPACE}/*:${ARCH_TAG}" >&2
  exit 1
fi

echo "==> Found ${#IMAGES[@]} image(s); building multi-arch manifests for tag :${TAG}"
for img in "${IMAGES[@]}"; do
  ref="${REGISTRY}/${NAMESPACE}/${img}:${TAG}"
  arch_ref="${REGISTRY}/${NAMESPACE}/${img}:${ARCH_TAG}"

  # Remove any stale local manifest list; ignore if it does not exist yet.
  docker manifest rm "$ref" >/dev/null 2>&1 || true

  docker manifest create "$ref" --amend "$arch_ref"
  docker manifest push "$ref"
done

echo "==> Pruning dangling and unused images"
docker image prune -a -f

echo "==> Done. Pushed ${#IMAGES[@]} manifest(s) at tag :${TAG}"
