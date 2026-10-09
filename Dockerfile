# --- Stage 1: build the React UI ---
FROM node:26-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Version shown in the page header (after npm ci, so a new version keeps the dependency layer cached)
ARG BUILD_VERSION=dev
ENV VITE_APP_VERSION=${BUILD_VERSION}
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
# The add-on's options schema and translations, for the Configuration form in the UI
COPY mfrr_tracker/config.yaml ./addon/config.yaml
COPY mfrr_tracker/translations/ ./addon/translations/
RUN mkdir -p data logs

# Version shown in the log: passed by the image workflow, and by the Supervisor for a local build.
# Declared last, so a new version doesn't invalidate the cached layers above.
ARG BUILD_VERSION=dev
ENV TRACKER_VERSION=${BUILD_VERSION}

# start.py maps Home Assistant add-on options to env vars, then runs uvicorn
CMD ["python", "start.py"]
