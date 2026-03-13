# Root Dockerfile — builds from the Real-Render subdirectory

# ---- Build stage: install Python deps ----
FROM python:3.13-slim AS builder

WORKDIR /build

COPY Real-Render/requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- Runtime stage ----
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY Real-Render/app/ ./app/

RUN mkdir -p ./data

ENV MCP_HOST="0.0.0.0"
ENV MCP_PORT="8000"
ENV PORT="8000"

EXPOSE 8000

CMD ["python", "-m", "app.main"]
