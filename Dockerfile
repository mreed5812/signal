# syntax=docker/dockerfile:1
# Multi-arch (linux/amd64, linux/arm64) — build with:
#   docker buildx build --platform linux/amd64,linux/arm64 -t btc-pipeline:latest .
FROM python:3.12-slim

# Keeps Python from buffering stdout/stderr so structlog output appears immediately.
# PYTHONPATH=/app lets all services import src.* without an editable install.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# System deps: libpq for psycopg2, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    curl \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps before copying source so Docker layer cache is effective.
COPY requirements.txt .

# CPU-only torch for ARM64 / Pi 5 compatibility — avoids pulling CUDA wheels.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
    torch==2.5.1 && \
    pip install -r requirements.txt

# Copy project source
COPY . .

CMD ["python", "-m", "src.api.main"]
