#!/usr/bin/env python3
"""
Starlink Mini → MQTT bridge (Docker-friendly)

What it does
- Authenticates to Starlink cloud using cookies.json
- Discovers the first user terminal (dish) from your account
- Polls dish status over gRPC-Web
- Publishes:
    * Raw status JSON to:   <TOPIC_PREFIX>/raw_status
    * Selected metrics to:  <TOPIC_PREFIX>/<metric_name>
      (state, uptime_s, obstruction_percent, obstructed, software_version)

Why this version exists
- starlink-client 0.1.13 currently hard-parses the service-lines JSON into a strict
  Pydantic model and fails when Starlink returns nulls for certain fields.
  This refactor bypasses that parsing (Option 2) by fetching service-lines as raw JSON
  and extracting only what we need.

Environment variables (set in docker compose)
- STARLINK_MQTT_HOST (default: "localhost")
- STARLINK_MQTT_PORT (default: "1883")
- STARLINK_MQTT_USER (default: "")
- STARLINK_MQTT_PASS (default: "")
- STARLINK_MQTT_PREFIX (default: "starlink/mini")
- STARLINK_POLL_INTERVAL (default: "30")
- STARLINK_LOG_LEVEL (default: "INFO")

- STARLINK_COOKIE_FILE (default: "/data/cookies.json")
- STARLINK_COOKIE_CACHE_DIR (default: "/data/cookie_cache")

Optional
- STARLINK_DISH_ID (default: "")  # if set, skip discovery
- STARLINK_MQTT_CLIENT_ID (default: "starlink-mini-bridge")
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Any, Dict

import paho.mqtt.client as mqtt
from google.protobuf.json_format import MessageToDict

# starlink-client package
from starlink_client.cookies_parser import parse_cookie_json
from starlink_client.grpc_web_client import GrpcWebClient


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    mqtt_host: str = os.getenv("STARLINK_MQTT_HOST", "localhost")
    mqtt_port: int = int(os.getenv("STARLINK_MQTT_PORT", "1883"))
    mqtt_user: str = os.getenv("STARLINK_MQTT_USER", "")
    mqtt_pass: str = os.getenv("STARLINK_MQTT_PASS", "")

    topic_prefix: str = os.getenv("STARLINK_MQTT_PREFIX", "starlink/mini")
    poll_interval_s: int = int(os.getenv("STARLINK_POLL_INTERVAL", "30"))
    log_level: str = os.getenv("STARLINK_LOG_LEVEL", "INFO").upper()

    cookie_file: str = os.getenv("STARLINK_COOKIE_FILE", "/data/cookies.json")
    cookie_cache_dir: str = os.getenv("STARLINK_COOKIE_CACHE_DIR", "/data/cookie_cache")

    dish_id: str = os.getenv("STARLINK_DISH_ID", "").strip()
    mqtt_client_id: str = os.getenv("STARLINK_MQTT_CLIENT_ID", "starlink-mini-bridge")


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def setup_logging(level: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    return logging.getLogger("starlink-mini-mqtt")


# -----------------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------------

def fail_config(msg: str) -> None:
    print("\nCONFIG ERROR:\n" + msg + "\n", file=sys.stderr)
    sys.exit(2)


def validate_config(cfg: Config) -> None:
    if not cfg.cookie_file:
        fail_config("STARLINK_COOKIE_FILE is empty.")
    if not os.path.exists(cfg.cookie_file):
        fail_config(
            f"cookies.json not found at {cfg.cookie_file}\n"
            f"  - In Docker, mount it and set STARLINK_COOKIE_FILE, e.g.:\n"
            f"      volumes:\n"
            f"        - ./cookies.json:/data/cookies.json:ro\n"
            f"      environment:\n"
            f"        STARLINK_COOKIE_FILE: /data/cookies.json\n"
        )
    if os.path.isdir(cfg.cookie_file):
        fail_config(
            f"STARLINK_COOKIE_FILE points to a directory, not a file: {cfg.cookie_file}\n"
            f"  - Make sure you mounted a file, not a folder.\n"
        )

    # ensure cache dir exists (inside container)
    try:
        os.makedirs(cfg.cookie_cache_dir, exist_ok=True)
    except Exception as e:
        fail_config(f"Could not create cookie cache dir {cfg.cookie_cache_dir}: {e}")

    if cfg.poll_interval_s < 5:
        fail_config("STARLINK_POLL_INTERVAL must be >= 5 seconds (be nice to the API).")


# -----------------------------------------------------------------------------
# Starlink client + raw service-lines (Option 2)
# -----------------------------------------------------------------------------

def load_starlink_client(cfg: Config, logger: logging.Logger) -> GrpcWebClient:
    """
    Load cookies.json and create a GrpcWebClient.
    """
    with open(cfg.cookie_file, "r", encoding="utf-8") as f:
        cookie_json = f.read()

    # starlink-client expects a certain cookie JSON format; it returns a cookie header string
    initial_cookies = parse_cookie_json(cookie_json)

    logger.info("Initializing GrpcWebClient with cookie cache: %s", cfg.cookie_cache_dir)
    client = GrpcWebClient(initial_cookies, cfg.cookie_cache_dir)

    # This will also refresh auth and populate account info (may be patched in your image)
    try:
        acc = client.get_account()
        email = getattr(acc, "email", None)
        name = getattr(acc, "name", None)
        logger.info("Authenticated to Starlink cloud. email=%s name=%s", email, name)
    except Exception as e:
        logger.warning("Authenticated client created, but account fetch failed: %s", e)

    return client


def get_service_lines_raw(client: GrpcWebClient) -> Dict[str, Any]:
    """
    Fetch service lines as raw JSON to avoid Pydantic validation issues when fields are null.

    Endpoint taken from starlink_client.grpc_web_client.GrpcWebClient.get_service_lines().
    """
    url = (
        "https://api.starlink.com/webagg/v2/accounts/service-lines"
        "?limit=10&page=0&isConverting=false&serviceAddressId="
        "&onlyActive=false&searchString=&onlyNoUts=false"
    )

    http = getattr(client, "_client", None)  # httpx.Client
    if http is None:
        raise RuntimeError("starlink-client internal http client not found (client._client missing)")

    resp = http.get(url, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"service-lines HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"service-lines JSON decode failed: {e}; first 300 chars: {resp.text[:300]}")


def extract_first_ut_from_service_lines(raw: Dict[str, Any]) -> Tuple[str, str, Optional[str]]:
    """
    Extract (dish_id, serial, nickname) from the raw service-lines JSON.
    Null-safe: nickname/displayName/etc may be None.

    Expected shape (based on starlink-client dto / your validation error):
      raw["content"]["results"][0]["userTerminals"][0]["id"|"userTerminalId" ...]
    """
    content = raw.get("content") or {}
    results = content.get("results") or []
    if not results:
        raise RuntimeError("No results[] in service-lines response")

    r0 = results[0] or {}
    nickname = r0.get("nickname") or r0.get("displayName") or None

    uts = r0.get("userTerminals") or []
    if not uts:
        raise RuntimeError("No userTerminals[] in first service-lines result")

    ut0 = uts[0] or {}

    # Dish/user terminal id typically "id" and already prefixed with "ut"
    dish_id = ut0.get("id") or ut0.get("userTerminalId") or ut0.get("utId")
    if not dish_id:
        raise RuntimeError(f"Could not find dish id in userTerminals[0]. keys={list(ut0.keys())}")

    serial = ut0.get("serialNumber") or ut0.get("serial")
    if not serial:
        # Sometimes nested
        dish_obj = ut0.get("dish") or {}
        serial = dish_obj.get("serialNumber") or dish_obj.get("serial")

    if not serial:
        raise RuntimeError(f"Could not find serialNumber in userTerminals[0]. keys={list(ut0.keys())}")

    return str(dish_id), str(serial), nickname


def get_first_dish(client: GrpcWebClient, logger: logging.Logger) -> Tuple[str, str, Optional[str]]:
    """
    Discover first dish from account using raw service-lines JSON.
    """
    raw = get_service_lines_raw(client)

    # Helpful shape debugging without dumping secrets
    logger.debug("service-lines top keys: %s", list(raw.keys()))
    logger.debug("service-lines content keys: %s", list((raw.get("content") or {}).keys()))

    dish_id, serial, nickname = extract_first_ut_from_service_lines(raw)

    logger.info("Using dish: id=%s serial=%s nickname=%s", dish_id, serial, nickname or "<none>")
    return dish_id, serial, nickname


# -----------------------------------------------------------------------------
# MQTT
# -----------------------------------------------------------------------------

def create_mqtt_client(cfg: Config, logger: logging.Logger) -> mqtt.Client:
    client = mqtt.Client(client_id=cfg.mqtt_client_id)

    if cfg.mqtt_user:
        client.username_pw_set(cfg.mqtt_user, cfg.mqtt_pass or None)

    def on_connect(mq, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info("Connected to MQTT %s:%d", cfg.mqtt_host, cfg.mqtt_port)
        else:
            logger.error("MQTT connect failed with rc=%s", rc)

    client.on_connect = on_connect

    logger.info("Connecting to MQTT broker %s:%d...", cfg.mqtt_host, cfg.mqtt_port)
    client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=60)
    client.loop_start()
    return client


def publish_metric(
    mq: mqtt.Client,
    cfg: Config,
    logger: logging.Logger,
    metric_name: str,
    value: Any,
    dish_id: str,
    serial: str,
) -> None:
    """
    Publish one metric to: <TOPIC_PREFIX>/<metric_name>
    Payload: {"value": ..., "ts": <unix>, "dish_id": "...", "serial": "...", "name": "<metric_name>"}
    """
    payload = {
        "value": value,
        "ts": int(time.time()),
        "dish_id": dish_id,
        "serial": serial,
        "name": metric_name,
    }
    topic = f"{cfg.topic_prefix}/{metric_name}"
    mq.publish(topic, json.dumps(payload), qos=0, retain=False)
    logger.debug("Published metric %s to %s: %s", metric_name, topic, payload)


def publish_raw_status(
    mq: mqtt.Client,
    cfg: Config,
    logger: logging.Logger,
    status_msg: Any,
    dish_id: str,
    serial: str,
) -> None:
    """
    Publish the entire dish status protobuf as JSON to: <TOPIC_PREFIX>/raw_status
    """
    status_dict = MessageToDict(status_msg, preserving_proto_field_name=True)
    payload = {
        "ts": int(time.time()),
        "dish_id": dish_id,
        "serial": serial,
        "status": status_dict,
    }
    topic = f"{cfg.topic_prefix}/raw_status"
    mq.publish(topic, json.dumps(payload), qos=0, retain=False)
    logger.debug("Published raw status to %s (size=%d keys)", topic, len(status_dict))


# -----------------------------------------------------------------------------
# Metrics
# -----------------------------------------------------------------------------

def extract_metrics_from_status(status_msg: Any) -> dict:
    """
    Safely pull a few metrics out of the dish status message.
    """
    device_info = getattr(status_msg, "device_info", None)
    device_state = getattr(status_msg, "device_state", None)
    obstruction_stats = getattr(status_msg, "obstruction_stats", None)

    uptime_s = None
    if device_state is not None:
        uptime_s = getattr(device_state, "uptime_s", None)

    software_version = None
    if device_info is not None:
        software_version = getattr(device_info, "software_version", None)

    obstruction_percent = None
    obstructed = None
    if obstruction_stats is not None:
        frac = None
        if hasattr(obstruction_stats, "fraction_obstructed"):
            frac = getattr(obstruction_stats, "fraction_obstructed", None)
        elif hasattr(obstruction_stats, "avg_prolonged_fraction_obstructed"):
            frac = getattr(obstruction_stats, "avg_prolonged_fraction_obstructed", None)

        if frac is not None:
            try:
                obstruction_percent = float(frac) * 100.0
            except Exception:
                obstruction_percent = None

        if hasattr(obstruction_stats, "currently_obstructed"):
            obstructed = getattr(obstruction_stats, "currently_obstructed", None)

    if uptime_s is None:
        state = "unknown"
    elif obstructed:
        state = "obstructed"
    else:
        state = "online"

    return {
        "state": state,
        "uptime_s": uptime_s,
        "obstruction_percent": obstruction_percent,
        "obstructed": obstructed,
        "software_version": software_version,
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    cfg = Config()
    logger = setup_logging(cfg.log_level)
    validate_config(cfg)

    logger.info("Starting Starlink Mini → MQTT bridge")
    logger.info(
        "Config summary: mqtt=%s:%d prefix=%s poll=%ss cookies=%s cache=%s dish_id=%s",
        cfg.mqtt_host,
        cfg.mqtt_port,
        cfg.topic_prefix,
        cfg.poll_interval_s,
        cfg.cookie_file,
        cfg.cookie_cache_dir,
        cfg.dish_id or "<auto>",
    )

    starlink = load_starlink_client(cfg, logger)

    # Dish discovery (Option 2 raw mode) unless user provided dish id
    if cfg.dish_id:
        dish_id = cfg.dish_id
        serial = "unknown"
        nickname = None
        logger.info("Using STARLINK_DISH_ID override: %s", dish_id)
    else:
        dish_id, serial, nickname = get_first_dish(starlink, logger)

    mqtt_client = create_mqtt_client(cfg, logger)

    logger.info(
        "Entering polling loop for dish_id=%s serial=%s nickname=%s",
        dish_id,
        serial,
        nickname or "<none>",
    )

    try:
        while True:
            try:
                dish_status = starlink.get_dish_status(dish_id)
            except Exception as e:
                logger.warning("Failed to get dish status for %s: %s", dish_id, e)
                publish_metric(mqtt_client, cfg, logger, "state", "error", dish_id, serial)
                time.sleep(cfg.poll_interval_s)
                continue

            publish_raw_status(mqtt_client, cfg, logger, dish_status, dish_id, serial)

            metrics = extract_metrics_from_status(dish_status)
            for name, value in metrics.items():
                publish_metric(mqtt_client, cfg, logger, name, value, dish_id, serial)

            logger.info(
                "Pushed Starlink metrics to MQTT: state=%s uptime_s=%s obstructed=%s",
                metrics.get("state"),
                metrics.get("uptime_s"),
                metrics.get("obstructed"),
            )

            time.sleep(cfg.poll_interval_s)

    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down...")
    finally:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()

