# syntax=docker/dockerfile:1
#
# Starlink Mini → MQTT bridge
# Dockerfile (production-friendly, non-root, env-configurable)
#
# Build:
#   docker build -t starlink-mini-mqtt:0.1.0 .
#
# Run (example):
#   docker run --rm \
#     -e STARLINK_MQTT_HOST=192.168.50.10 \
#     -e STARLINK_COOKIE_FILE=/data/cookies.json \
#     -e STARLINK_COOKIE_CACHE_DIR=/data/cookie_cache \
#     -v "$PWD/cookies.json:/data/cookies.json:ro" \
#     -v "$PWD/cookie_cache:/data/cookie_cache" \
#     starlink-mini-mqtt:0.1.0

FROM python:3.12-slim

# Make logs stream in Docker and avoid .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user
RUN useradd -m -u 10001 appuser

WORKDIR /app

# Install Python deps first for better caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ---- Patch starlink-client (cloud auth/XSRF behavior) ----
# starlink-client 0.1.13 installs into /usr/local/lib/python3.12/site-packages/
COPY patches/grpc_web_base_client.py /usr/local/lib/python3.12/site-packages/starlink_client/grpc_web_base_client.py

# Copy your app (script + local module)
COPY starlink_mini_mqtt.py /app/starlink_mini_mqtt.py

# Prepare mount points and permissions
RUN mkdir -p /data/cookie_cache /data && chown -R appuser:appuser /app /data

USER appuser

# Sensible defaults; users override in docker-compose
ENV STARLINK_MQTT_HOST=localhost \
    STARLINK_MQTT_PORT=1883 \
    STARLINK_MQTT_PREFIX=starlink/mini \
    STARLINK_POLL_INTERVAL=30 \
    STARLINK_LOG_LEVEL=INFO \
    STARLINK_COOKIE_FILE=/data/cookies.json \
    STARLINK_COOKIE_CACHE_DIR=/data/cookie_cache \
    STARLINK_MQTT_CLIENT_ID=starlink-mini-mqtt

CMD ["python", "/app/starlink_mini_mqtt.py"]

