# python:3.12-slim (linux/amd64 manifest: sha256:86d3e4424d5e963e60594a3a6b4d597cc4d41f5152fe67a97a40dca9ea092475)
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.51

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install tzdata for timezone support, curl for supercronic download,
# procps for pgrep healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        procps \
        tzdata && \
    rm -rf /var/lib/apt/lists/*

# Install Supercronic (multi-arch, SHA256-verified)
# Update version + checksums: https://github.com/aptible/supercronic/releases
COPY deploy/supercronic-checksums.txt /tmp/
RUN SUPERCRONIC_BIN="supercronic-linux-${TARGETARCH}" && \
    curl -fsSLO "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/${SUPERCRONIC_BIN}" && \
    grep "${SUPERCRONIC_BIN}" /tmp/supercronic-checksums.txt | sha256sum -c - && \
    chmod +x "${SUPERCRONIC_BIN}" && \
    mv "${SUPERCRONIC_BIN}" /usr/local/bin/supercronic && \
    rm /tmp/supercronic-checksums.txt

WORKDIR /app

# Symlink supercronic into WORKDIR so the reaper can find it.
# https://github.com/aptible/supercronic/issues/177
RUN ln -s /usr/local/bin/supercronic /app/supercronic

# Layer caching: install deps first (slow), then copy source (fast)
# --require-hashes: requirements.lock is hash-pinned. CI exports it from uv.lock
# (`uv export --locked --no-dev --no-emit-project`); it is not committed.
# Refuse to install anything that doesn't match a recorded hash.
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Vendored shared helpers (tools/infra_labs_common/). The build workflow rsyncs
# it into ./vendor/infra_labs_common before the build; see
# .github/workflows/build-tools.yml. Installed --no-deps so it never pulls
# anything outside the hash-pinned requirements.lock.
COPY vendor/infra_labs_common/ ./vendor/infra_labs_common/
RUN pip install --no-cache-dir --no-deps ./vendor/infra_labs_common

COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# Copy and validate crontab
COPY deploy/crontab /app/crontab
RUN supercronic -test /app/crontab

# Non-root user. account_automation writes /tmp/account-automation.lock
# (world-writable in the base image) and its success metric into the
# bind-mounted textfile directory, which the host keeps owned by uid 10001,
# so no chown is needed in the image.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin account-automation && \
    chown -R account-automation:account-automation /app
USER account-automation

ENTRYPOINT ["supercronic", "-split-logs", "/app/crontab"]
