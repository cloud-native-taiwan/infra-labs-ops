# python:3.12-slim (linux/amd64 manifest: sha256:86d3e4424d5e963e60594a3a6b4d597cc4d41f5152fe67a97a40dca9ea092475)
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.33

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
# --require-hashes: requirements.lock is hash-pinned (uv pip compile --generate-hashes);
# refuse to install anything that doesn't match a recorded hash.
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# Copy and validate crontab
COPY deploy/crontab /app/crontab
RUN supercronic -test /app/crontab

# Non-root user. account_automation only writes /tmp/account-automation.lock,
# which is world-writable in the base image, so no extra volume chown is needed.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin account-automation && \
    chown -R account-automation:account-automation /app
USER account-automation

ENTRYPOINT ["supercronic", "-split-logs", "/app/crontab"]
