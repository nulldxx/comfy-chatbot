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

# No additional runtime dependencies needed for basic Flask app

# Copy Python packages from builder stage
COPY --from=builder /root/.local /usr/local

# Copy application code
COPY app.py .
COPY gunicorn.conf.py .
COPY templates/ templates/

# Create a non-root user for security
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port 5000
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; r=requests.get('http://localhost:5000/health', timeout=5); exit(0 if r.status_code == 200 else 1)" || exit 1

# Run the application with Gunicorn
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
