FROM python:3.12-slim

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

# Layer caching: install deps first (slow), then copy source (fast)
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# Copy and validate crontab
COPY deploy/crontab /app/crontab
RUN supercronic -test /app/crontab

ENTRYPOINT ["supercronic", "-split-logs", "/app/crontab"]
