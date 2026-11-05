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
TAG="2026.2"
KOLLA_MAJOR="23"  # kolla release series that builds ${TAG}
ARCH="amd64"
ARCH_TAG="${TAG}-${ARCH}"
VENV="/home/debian/dev/bin/activate"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Tracked build config. Passing --config-file makes oslo.config use ONLY
# this file and skip the default /etc/kolla/kolla-build.conf, so any
# host-side profile filter (which would silently drop cloudkitty) is
# bypassed and the image set is reproducible from the repo.
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_CONF="${REPO_ROOT}/kolla/kolla-build.conf"
# Ceph-consuming images that must carry aes256k-capable client libs
# (>= 20.2.4, CVE-2025-30156) before the :${TAG} manifests are published.
CEPH_IMAGES=(glance-api cinder-volume cinder-backup nova-compute nova-libvirt gnocchi-api manila-share)
CEPH_MIN="20.2.4"

if [[ ! -f "$BUILD_CONF" ]]; then
  echo "ERROR: build config not found at ${BUILD_CONF}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$VENV"

kolla_version="$(python3 -c 'import importlib.metadata as m; print(m.version("kolla"))')"
if [[ "$kolla_version" != "${KOLLA_MAJOR}."* ]]; then
  echo "ERROR: kolla ${kolla_version} in ${VENV}; tag ${TAG} needs kolla ${KOLLA_MAJOR}.x" >&2
  exit 1
fi

# kolla-build.conf uses repo-relative paths for the apt/template overrides.
cd "$REPO_ROOT"

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

echo "==> Checking Ceph client libs (librbd1 >= ${CEPH_MIN})"
for img in "${CEPH_IMAGES[@]}"; do
  [[ " ${IMAGES[*]} " == *" ${img} "* ]] || continue
  ver="$(docker run --rm --entrypoint dpkg-query "${REGISTRY}/${NAMESPACE}/${img}:${ARCH_TAG}" \
    -W -f '${Version}' librbd1 || true)"
  if [[ -z "$ver" ]] || ! dpkg --compare-versions "$ver" ge "$CEPH_MIN"; then
    echo "ERROR: ${img} has librbd1 '${ver:-missing}', need >= ${CEPH_MIN}; not publishing :${TAG}" >&2
    exit 1
  fi
  echo "    ${img}: librbd1 ${ver}"
done

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
