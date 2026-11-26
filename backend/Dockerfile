# ==============================================================================
# FloodWatch Backend Production Dockerfile
# ==============================================================================
# Base Image: Official Python 3.11 Slim (Debian Bookworm)
# Security & Operational Standards:
# - Non-root container execution (user: floodwatch, UID: 10001)
# - No secrets or credentials baked into image layers
# - Built-in container healthcheck via /api/v1/health/liveness
# - Single-worker Uvicorn startup (protects asyncio ingestion scheduler)
# ==============================================================================

FROM python:3.11-slim-bookworm AS runtime

# Set environment flags
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000

# Install security updates and curl for container health probes
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root dedicated application user & group
RUN groupadd -g 10001 floodwatch && \
    useradd -u 10001 -g floodwatch -m -d /home/floodwatch -s /bin/bash floodwatch

WORKDIR /app

# Install Python dependencies first for caching efficiency
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code with non-root ownership
COPY --chown=floodwatch:floodwatch app ./app

# Switch to non-root execution
USER floodwatch:floodwatch

# Expose internal API service port
EXPOSE 8000

# Container healthcheck using fast process liveness probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/v1/health/liveness || exit 1

# Production startup command
# NOTE: Uses authoritative application launcher which enforces worker count and single-process scheduler safety.
CMD ["python", "-m", "app.main"]
