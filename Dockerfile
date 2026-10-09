# --- Stage 1: build the React UI ---
FROM node:26-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend + built UI in one image ---
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install sqlite3 and system CA certs
RUN apt-get update && \
    apt-get install -y sqlite3 ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python requirements (pip-system-certs makes requests use the system CA certificates)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=ui /ui/dist ./static
RUN mkdir -p data logs

# Version shown in the log: passed by the image workflow, and by the Supervisor for a local build.
# Declared last, so a new version doesn't invalidate the cached layers above.
ARG BUILD_VERSION=dev
ENV TRACKER_VERSION=${BUILD_VERSION}

# start.py maps Home Assistant add-on options to env vars, then runs uvicorn
CMD ["python", "start.py"]
