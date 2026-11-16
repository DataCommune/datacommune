# Multi-stage build for DataCommune API using uv
# Build context: ./backend (set in docker-compose.yml)

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (layer cache, no dev deps)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-editable --no-dev

# Copy source code and install project
COPY . .
RUN uv sync --locked --no-editable --no-dev

# --- Stage 2: Runtime ---
FROM python:3.12-slim

# API and scheduler implement the journal/OID/locking protocol tested in COR20.
# This capability marker is necessary for rollback admission, not a release proof.
LABEL org.datacommune.publication-contract="1"

# GDAL/ogr2ogr for ETL pipelines (cadastre GeoJSON loading); 7zip provides the
# native `7z` that extracts BD TOPO archives one department at a time (04.1-05).
# Note: versions follow the base image's Debian release (currently trixie)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    7zip \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --no-create-home --gecos "" appuser

WORKDIR /app

# Copy the entire app with venv from builder
COPY --from=builder /app /app

# Writable temp dir for ETL downloads (also handles Docker volumes mounted at /etl_tmp)
RUN mkdir -p /tmp/etl /etl_tmp && chown appuser:appuser /tmp/etl /etl_tmp

ENV PATH="/app/.venv/bin:$PATH"

# Run as non-root for security
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/v1/health/live').raise_for_status()"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
