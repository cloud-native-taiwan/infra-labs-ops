# ADR-0004: Build tool images in CI to GHCR; deploy host pulls instead of builds

- **Status:** Accepted
- **Date:** 2026-06
- **Deciders:** CNTUG ops

## Context
The deploy host required local Docker builds of the `account_automation` and `usage_reports` images. This added a build-toolchain dependency to the deploy host and made image builds slower and non-reproducible.

## Decision
Build tool images in CI and push them to GHCR.

- `.github/workflows/build-tools.yml` builds images and pushes to GHCR.
- Pull requests are build-only; pushes to `main` build and push.
- The `docker-compose` files pull prebuilt images with `--pull always` instead of `--build`.
- A specific build can be pinned with `-e tools_image_tag=sha-<commit>`.

## Alternatives considered
- Keep building on the deploy host — rejected; toolchain dependency, slower, non-reproducible.

## Consequences
The deploy host needs only a registry pull. Images are reproducible, and rollback is a matter of pinning a prior `sha` tag.

## References
- commit 1bee149 (build tool images in GHCR)
- .github/workflows/build-tools.yml
