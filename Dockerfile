# ── Stage 1: Build ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install dependencies into a separate prefix for clean copy
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Install Google Cloud SDK (minimal) for service account activation
# This is needed so the pipeline can authenticate to GCP services at runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    apt-get update && \
    apt-get install -y --no-install-recommends google-cloud-sdk && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY config/ ./config/
COPY sql/ ./sql/
COPY src/ ./src/
COPY jobs/ ./jobs/
COPY entrypoint.sh .

# Create secrets directory (GCP key will be mounted here by Cloud Run)
# The actual key file is NEVER baked into the image — it's mounted at runtime.
RUN mkdir -p /app/secrets

# Environment defaults
ENV PIPELINE_MODE=daily \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/gcp-key.json

# Health check — verify the container can import the pipeline module
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "from src.pipeline import load_commodities; load_commodities()" || exit 1

# Run as non-root user for security
RUN useradd --create-home appuser && \
    chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["./entrypoint.sh"]
