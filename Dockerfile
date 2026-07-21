# Multi-stage build: Builder stage
FROM python:3.11-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies for compiling Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Multi-stage build: Runtime stage
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# ffmpeg is used to extract the last frame from generated videos (the ✂ overlay)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder stage
COPY --from=builder /root/.local /usr/local

# Copy application code
COPY app.py .
COPY agent_client.py .
COPY auth_store.py .
COPY catalogue.py .
COPY ComfyServer.py .
COPY config.py .
COPY crypto_key.py .
COPY generation_service.py .
COPY grok.py .
COPY gunicorn.conf.py .
COPY image_store.py .
COPY persistence.py .
COPY workflow.py .
COPY docker-entrypoint.sh .
COPY templates/ templates/
COPY static/ static/

# Create a non-root user for security
RUN chmod +x docker-entrypoint.sh && \
    adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port 5000
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; r=requests.get('http://localhost:5000/health', timeout=5); exit(0 if r.status_code == 200 else 1)" || exit 1

# Build version (e.g. output of `git describe --tags --always --dirty`),
# passed in at build time and exposed to the app so it logs it on startup.
# Kept near the end so a changing version doesn't bust the cache above it.
ARG BUILD_VERSION=unknown
ENV BUILD_VERSION=$BUILD_VERSION

# The entrypoint mounts/unmounts the encrypted output volume (when configured)
# around the app, then runs the CMD below. With encryption disabled it's a
# transparent passthrough to gunicorn.
ENTRYPOINT ["./docker-entrypoint.sh"]

# Run the application with Gunicorn
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
