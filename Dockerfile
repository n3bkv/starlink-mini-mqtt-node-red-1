# syntax=docker/dockerfile:1
#
# Starlink Mini → MQTT bridge
# Dockerfile
#
# Version: 0.1.1
#
# Includes compatibility fixes for current Starlink cloud API:
#   - Updated gRPC-Web endpoint
#   - Required Origin header
#   - canManageClients may be omitted from account response
#
# Build:
#   docker build -t starlink-mini-mqtt:0.1.1 .
#
# Run:
#   docker run --rm \
#     -e STARLINK_MQTT_HOST=192.168.50.8 \
#     -e STARLINK_COOKIE_FILE=/data/cookies.json \
#     -e STARLINK_COOKIE_CACHE_DIR=/data/cookie_cache \
#     -v "$PWD/cookies.json:/data/cookies.json:ro" \
#     -v "$PWD/cookie_cache:/data/cookie_cache" \
#     starlink-mini-mqtt:0.1.1
#

FROM python:3.12-slim


# ---------------------------------------------------------------------------
# Runtime environment
# ---------------------------------------------------------------------------

# Do not create .pyc files.
# Send Python output directly to Docker logs without buffering.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1


# ---------------------------------------------------------------------------
# Non-root application user
# ---------------------------------------------------------------------------

RUN useradd \
    --create-home \
    --uid 10001 \
    --shell /usr/sbin/nologin \
    appuser


# ---------------------------------------------------------------------------
# Application directory
# ---------------------------------------------------------------------------

WORKDIR /app


# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------

# Copy requirements separately so Docker can cache this layer unless
# requirements.txt changes.
COPY requirements.txt /app/requirements.txt

RUN python -m pip install \
        --no-cache-dir \
        --disable-pip-version-check \
        -r /app/requirements.txt


# ---------------------------------------------------------------------------
# Starlink client compatibility patches
# ---------------------------------------------------------------------------
#
# The upstream Starlink cloud API changed in 2026.
#
# grpc_web_base_client.py patch:
#   - Uses:
#       https://starlink.com/api/SpaceX.API.Device.Device/Handle
#   - Sends:
#       Origin: https://starlink.com
#
# account.py patch:
#   - Allows Starlink account responses that omit:
#       canManageClients
#
# Do NOT hard-code:
#   /usr/local/lib/python3.12/site-packages/
#
# Instead, let Python locate the installed starlink_client package.
#

COPY patches/grpc_web_base_client.py /tmp/starlink-patches/grpc_web_base_client.py
COPY patches/account.py /tmp/starlink-patches/account.py

RUN python - <<'PY'
from pathlib import Path
import shutil
import starlink_client

package_dir = Path(starlink_client.__file__).resolve().parent
patch_dir = Path("/tmp/starlink-patches")

patches = [
    "grpc_web_base_client.py",
    "account.py",
]

print(f"Installed starlink_client package: {package_dir}")

for filename in patches:
    source = patch_dir / filename
    destination = package_dir / filename

    if not source.exists():
        raise RuntimeError(f"Required patch does not exist: {source}")

    if not destination.exists():
        raise RuntimeError(
            f"Expected starlink-client module does not exist: {destination}"
        )

    print(f"Installing patch: {source} -> {destination}")
    shutil.copy2(source, destination)

print("Starlink client patches installed successfully.")
PY


# ---------------------------------------------------------------------------
# Validate patched starlink-client
# ---------------------------------------------------------------------------
#
# Fail the Docker build immediately if:
#
#   - account.py still requires canManageClients
#   - the Starlink endpoint is still the old api.starlink.com endpoint
#   - the new endpoint is missing
#   - the Origin support is missing
#
# This prevents accidentally building an image with the old broken client.
#

RUN python - <<'PY'
from pathlib import Path
import starlink_client

from starlink_client.account import Account

package_dir = Path(starlink_client.__file__).resolve().parent

account_file = package_dir / "account.py"
grpc_file = package_dir / "grpc_web_base_client.py"

account_text = account_file.read_text(encoding="utf-8")
grpc_text = grpc_file.read_text(encoding="utf-8")


# -----------------------------------------------------------------------
# Verify Account model
# -----------------------------------------------------------------------

field = Account.model_fields.get("canManageClients")

if field is None:
    raise RuntimeError(
        "Patched Account model does not contain canManageClients"
    )

if field.is_required():
    raise RuntimeError(
        "Starlink patch validation FAILED: "
        "canManageClients is still required"
    )

print(
    "Verified Account.canManageClients: "
    f"required={field.is_required()} default={field.default}"
)


# -----------------------------------------------------------------------
# Verify gRPC-Web endpoint
# -----------------------------------------------------------------------

new_endpoint = (
    "https://starlink.com/api/"
    "SpaceX.API.Device.Device/Handle"
)

old_endpoint = (
    "https://api.starlink.com/"
    "SpaceX.API.Device.Device/Handle"
)

if new_endpoint not in grpc_text:
    raise RuntimeError(
        "Starlink patch validation FAILED: "
        "new gRPC-Web endpoint not found"
    )

if old_endpoint in grpc_text:
    raise RuntimeError(
        "Starlink patch validation FAILED: "
        "old gRPC-Web endpoint is still present"
    )

print(f"Verified Starlink gRPC-Web endpoint: {new_endpoint}")


# -----------------------------------------------------------------------
# Verify Origin support
# -----------------------------------------------------------------------

if "https://starlink.com" not in grpc_text:
    raise RuntimeError(
        "Starlink patch validation FAILED: "
        "Starlink web origin is missing"
    )

if "Origin" not in grpc_text:
    raise RuntimeError(
        "Starlink patch validation FAILED: "
        "Origin header support is missing"
    )

print("Verified Starlink Origin header support.")

print("All Starlink compatibility checks passed.")
PY


# ---------------------------------------------------------------------------
# Remove temporary patch files
# ---------------------------------------------------------------------------

RUN rm -rf /tmp/starlink-patches


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

COPY starlink_mini_mqtt.py /app/starlink_mini_mqtt.py


# ---------------------------------------------------------------------------
# Persistent/runtime data directory
# ---------------------------------------------------------------------------
#
# Expected mounts:
#
#   /data/cookies.json
#   /data/cookie_cache/
#

RUN mkdir -p /data/cookie_cache \
    && chown -R appuser:appuser /app /data


# ---------------------------------------------------------------------------
# Runtime user
# ---------------------------------------------------------------------------

USER appuser


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------
#
# All of these can be overridden by docker-compose or docker run.
#

ENV STARLINK_MQTT_HOST=localhost \
    STARLINK_MQTT_PORT=1883 \
    STARLINK_MQTT_PREFIX=starlink/mini \
    STARLINK_POLL_INTERVAL=30 \
    STARLINK_LOG_LEVEL=INFO \
    STARLINK_COOKIE_FILE=/data/cookies.json \
    STARLINK_COOKIE_CACHE_DIR=/data/cookie_cache \
    STARLINK_MQTT_CLIENT_ID=starlink-mini-mqtt


# ---------------------------------------------------------------------------
# Start application
# ---------------------------------------------------------------------------

CMD ["python", "/app/starlink_mini_mqtt.py"]
