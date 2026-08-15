# Starlink Mini → MQTT Bridge/Node-RED Dashbaord

A Docker-friendly bridge and example Node-RED flow that polls **Starlink Mini** dish status via the Starlink cloud
and publishes metrics to **MQTT** for use with Node-RED, Home Assistant, InfluxDB, etc.

> ⚠️ This project uses unofficial Starlink endpoints.
> It may break if Starlink changes their backend.

## August 2026 Starlink API Compatibility

Starlink changed portions of its cloud API in August 2026.

This project includes compatibility patches for:

- Starlink authentication responses that may omit `canManageClients`
- The current gRPC-Web endpoint:
  `https://starlink.com/api/SpaceX.API.Device.Device/Handle`
- The required `Origin: https://starlink.com` request header

These patches are included automatically in the Docker image.

---

# Screenshot

<img width="1004" height="516" alt="Updated Screen SHot Node-Red Starling DB" src="https://github.com/user-attachments/assets/aaee7d45-8120-4578-890d-aceb2d52dd19" />


---

## Features
- Works with **Starlink Mini (consumer accounts)**
- Docker-first (no Python install required)
- Publishes:
  - `state`
  - `uptime_s`
  - `obstruction_percent`
  - `obstructed`
  - `software_version`
  - full raw status JSON
  - Designed for **Node-RED dashboards**
  - MQTT-friendly (works with Home Assistant, InfluxDB, etc.)

---

## Quick Start (Recommended)

## 1) Create working directories

```bash
mkdir -p starlink-mini-mqtt/data starlink-mini-mqtt/cookie_cache
cd starlink-mini-mqtt
```

### 2) Export cookies from starlink.com
- Log into https://www.starlink.com
- Export cookies using a browser extension that can capture them in .json format like Cookies Extractor for Chrome.
- Save as `data/cookies.json`

Example file is provided:
```bash
cp data/cookies.json.example data/cookies.json
```

---

### 3) Create docker-compose.yml
**This example code assumes you already have an MQTT broker**

```
services:
  starlink-mini-mqtt-1:
    image: ghcr.io/n3bkv/starlink-mini-mqtt-1:latest
    container_name: starlink-mini-mqtt-1
    restart: unless-stopped
    volumes:
      - ./data:/data:ro
      - ./cookie_cache:/cookie_cache
    environment:
      STARLINK_MQTT_HOST: 192.168.1.10   # your broker
      STARLINK_MQTT_PORT: "1883"

```

### 4) Start the container
```bash
docker compose up -d
docker compose logs -f starlink-mini-mqtt-1

```

---

## MQTT Topics

Default prefix: `starlink/mini`

```
starlink/mini/state
starlink/mini/uptime_s
starlink/mini/obstruction_percent
starlink/mini/obstructed
starlink/mini/software_version
starlink/mini/raw_status
```

Payload example:
```json
{
  "value": 123,
  "ts": 1705600000,
  "dish_id": "ut012345",
  "serial": "KIT123456",
  "name": "uptime_s"
}
```

---

## Sample Node-RED Flow

A sample Node-RED flow is included: starlink-stats-node-red.json
Import it into Node-RED to visualize metrics immediately.

---
## Configuration (Environment Variables)

| Variable | Default |
|--------|---------|
| STARLINK_MQTT_HOST | `mosquitto` |
| STARLINK_MQTT_PORT | `1883` |
| STARLINK_MQTT_PREFIX | `starlink/mini` |
| STARLINK_POLL_INTERVAL | `30` |
| STARLINK_LOG_LEVEL | `INFO` |
| STARLINK_COOKIE_FILE | `/data/cookies.json` |
| STARLINK_COOKIE_CACHE_DIR | `/cookie_cache` |

Important - Make sure to edit your mqtt broker host IP into the docker-compose.yml file to replace the 'mosquitto' placeholder.

---

## macOS Users (Important)
If connecting to an MQTT broker running **on your Mac**, use:

```
STARLINK_MQTT_HOST=host.docker.internal
```

`localhost` will NOT work inside Docker containers.

---

## Security Notes
- `cookies.json` contains authentication tokens
- Keep it local, never commit it
- Rotate cookies periodically

---

## Development / Customization

```bash
git clone https://github.com/n3bkv/starlink-mini-mqtt-node-red-1
cd starlink-mini-mqtt-node-red-1

```



## License
MIT

---

## Shout Out

I'd like to salute Eitol's starlink-client project which is the basis for this work.

---

## Support This Project
If you find this useful, star ⭐ the repo! It helps others discover it.

---

##More Info

Blog: https://hamradiohacks.blogspot.com

GitHub: https://github.com/n3bkv

