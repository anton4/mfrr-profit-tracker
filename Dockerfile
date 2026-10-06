# --- Stage 1: build the React UI ---
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend + built UI in one image ---
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install sqlite3 and system CA certs
RUN apt-get update && \
    apt-get install -y sqlite3 ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install pip-system-certs to use system CA certificates
RUN pip install --no-cache-dir pip-system-certs && \
    ln -s /usr/local/lib/python3.11/site-packages/pip_system_certs/wrapt_requests.py \
          /usr/local/lib/python3.11/site-packages/requests.pth

COPY backend/ .
COPY --from=ui /ui/dist ./static
RUN mkdir -p data logs

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
