# ---- Build stage: install Python deps into a virtualenv ----
FROM python:3.13-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- Runtime stage ----
FROM python:3.13-slim

# Install ffmpeg (needed for video processing / keyframe extraction)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Bring in the pre-built virtualenv from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy application code
COPY app/ ./app/

# Ensure the data directory exists (will be mounted as a volume)
RUN mkdir -p ./data

# Bind to all interfaces so the container is reachable from the host
ENV MCP_HOST="0.0.0.0"
ENV MCP_PORT="8000"

EXPOSE 8000

CMD ["python", "-m", "app.main"]
