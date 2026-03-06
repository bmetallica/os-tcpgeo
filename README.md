# os-tcpgeo

**OPNsense Live Traffic Globe Plugin**

Real-time network traffic visualization on an interactive 3D globe — fully integrated into the OPNsense web UI.

![Screenshot](Screenshot.jpg)

---

## ✨ Features

- **Cyberpunk 3D Globe** — dark material, neon country borders, animated directional arcs
- **Live Packet Capture** — SYN-only BPF kernel filter with pfctl byte-count enrichment (~0 % CPU)
- **GeoIP Resolution** — MaxMind GeoLite2-City with automatic weekly database updates (SHA256-verified)
- **Port-based Arc Colors** — assign a unique color to each port (443 → cyan, 80 → green, …)
- **Direction Detection** — inbound arcs point toward the firewall, outbound arcs are multicolor
- **GPU-rendered Labels** — city/country names appear at arc endpoints on the globe
- **Full OPNsense Integration** — settings page under *Services → TCPGeo*, start/stop/status via configd
- **High-throughput Optimized** — smart backend sampling, zero-DOM-mutation event feed, separated render loops
- **Single-script Install & Uninstall** — no pkg repo required, just `sh install.sh`
- **100% Offline** — Three.js, Globe.gl, Fonts und Geodaten lokal gebündelt (keine CDN-Abhängigkeiten)
- **Security-Hardened** — Privilege Separation (nobody), Basic Auth, WebSocket Rate-Limiting, IP-Masking, XSS-Schutz
- **MQTT Export** — Periodische Übertragung von Verbindungsdaten an einen MQTT-Broker (Grafana, Home Assistant, InfluxDB). Reiner Python-MQTT-Client ohne externe Abhängigkeiten.

---

## 📋 Requirements

| Component | Version |
|-----------|---------|
| OPNsense  | 23.x / 24.x / 25.x |
| Python 3  | ≥ 3.9 (pre-installed on OPNsense) |
| pip       | any (installer auto-detects) |

Python packages installed automatically:

| Package | Purpose |
|---------|---------|
| `aiohttp ≥ 3.9` | Async HTTP + WebSocket server |
| `maxminddb ≥ 2.5` | GeoIP database reader |

> **Note:** No Node.js required. The entire backend runs on Python 3.

---

## 🚀 Installation

### Quick Install (recommended)

SSH into your OPNsense firewall as **root** and run:

```bash
fetch -o /tmp/os-tcpgeo.tar.gz https://github.com/bmetallica/os-tcpgeo/archive/refs/heads/main.tar.gz
tar -xzf /tmp/os-tcpgeo.tar.gz -C /tmp
cd /tmp/os-tcpgeo-main
sh install.sh
```

### With GeoIP Database

To download the MaxMind GeoLite2-City database during installation, pass your license key:

```bash
sh install.sh --with-geoip YOUR_MAXMIND_LICENSE_KEY
```

> Get a free license key at [maxmind.com/en/geolite2/signup](https://www.maxmind.com/en/geolite2/signup).

### From Git (alternative)

```bash
pkg install -y git
cd /tmp
git clone https://github.com/bmetallica/os-tcpgeo.git
cd os-tcpgeo
sh install.sh
```

---

## ⚙️ Configuration

After installation, open the OPNsense web UI:

**Services → TCPGeo**

| Setting | Description | Example |
|---------|-------------|---------|
| Enabled | Enable / disable the service | ✓ |
| Listen Interface | Interface the globe web server binds to | LAN |
| Listen Port | Port for the globe web server | `3333` |
| WAN Interfaces | Internet-facing interfaces for traffic capture | WAN |
| LAN Interfaces | Internal interfaces for client IP detection | LAN |
| MaxMind License Key | For automatic GeoIP database downloads | `abc123…` || Globe Password | Optional HTTP Basic Auth password (min. 8 chars) | `mySecret1` |
| Mask IPs | Hide last octet of IPs in frontend (e.g. `1.2.3.xxx`) | ✓ (default) |
| HTTPS aktivieren | Enable TLS encryption for the globe server | ✗ (default: HTTP) |
| Zertifikat-Modus | Self-signed (auto-generated) or OPNsense certificate | `Selbstsigniert` |
| OPNsense-Zertifikat | Select a certificate from System → Trust → Certificates | — |
| MQTT aktivieren | Enable periodic MQTT export of connection data | ✗ (default) |
| MQTT-Server | Hostname or IP of the MQTT broker | `192.168.1.10` |
| MQTT-Port | TCP port of the MQTT broker | `1883` |
| MQTT-Benutzername | Optional username for MQTT authentication | — |
| MQTT-Passwort | Optional password for MQTT authentication | — |
| MQTT-Topic | Base topic for published messages | `tcpgeo` |
| MQTT-Intervall | Publish interval in seconds (10–3600) | `60` |
| Port Colors | Color mapping per port (table) | `443 → #00ffff` |

Click **Save & Apply** — the service will (re)start automatically.

### ConnectionTracker (SYN-only Capture)

TCPGeo uses a unified **ConnectionTracker** that combines the best of all worlds:

| Property | Value |
|----------|-------|
| CPU Load | ~0 % (BPF kernel filter drops 99.9 % of packets before userspace) |
| Latency | Real-time (new connections appear instantly) |
| Byte Counts | ✓ via periodic `pfctl -ss -v` enrichment (every 15 s) |
| Requirements | None — works out of the box |

**How it works:**

1. **SYN-only BPF filter** — `tcpdump` runs with a kernel-level BPF filter that only passes TCP SYN packets (new connections) and selected UDP ports (DNS, QUIC, NTP, STUN, WireGuard). This reduces events from ~10,000/s to ~5–50/s.
2. **Multi-interface** — separate `tcpdump` processes for each configured WAN and LAN interface. WAN captures show direction; LAN captures reveal pre-NAT client IPs.
3. **Byte-count enrichment** — a background thread polls `pfctl -ss -v` every 15 s, batching active IPs into a single call and parsing byte counters. The frontend uses these to scale arc thickness.
4. **UDP dedup** — identical UDP flows (same src/dst/port) are suppressed for 30 s to avoid flooding from DNS bursts.

### MQTT Export

TCPGeo can periodically publish connection statistics to an MQTT broker. This enables long-term analytics, dashboards, and alerting without affecting globe performance.

**Topics** (base topic configurable, default `tcpgeo`):

| Topic | Content | Use Case |
|-------|---------|----------|
| `tcpgeo/stats/outgoing` | Per-client, per-country outgoing connection counts + bytes | "Client 192.168.1.50 made 42 connections to US today" |
| `tcpgeo/stats/incoming` | Per-port, per-country incoming connection counts | "150 SSH attempts from CN on WAN today" |
| `tcpgeo/clients/outgoing` | Per-client detailed outgoing connections (country, city, port, bytes) | "Client .50 → Ashburn/US:443 ×12, Frankfurt/DE:80 ×5" |
| `tcpgeo/connections` | Snapshot of recent connections (last 200) | Live feed for dashboards |

**Example: Outgoing stats payload**
```json
{
  "timestamp": "2026-03-06T14:30:00Z",
  "clients": {
    "192.168.1.50": { "total": 42, "bytes": 340000, "countries": { "US": 12, "DE": 5, "NL": 3 } },
    "192.168.1.51": { "total": 15, "bytes": 120000, "countries": { "FR": 8, "GB": 7 } }
  }
}
```

**Example: Incoming stats payload**
```json
{
  "timestamp": "2026-03-06T14:30:00Z",
  "ports": {
    "22": { "label": "SSH", "total": 150, "countries": { "CN": 80, "RU": 45, "US": 25 } },
    "443": { "label": "HTTPS", "total": 30, "countries": { "US": 20, "DE": 10 } }
  }
}
```

**Example: Per-client detailed outgoing payload**
```json
{
  "timestamp": "2026-03-06T14:30:00Z",
  "clients": {
    "192.168.1.xxx": [
      { "country": "US", "city": "Ashburn", "port": 443, "label": "HTTPS", "count": 12, "bytes": 52400 },
      { "country": "DE", "city": "Frankfurt", "port": 80, "label": "HTTP", "count": 5, "bytes": 8300 }
    ]
  }
}
```

**Integration ideas:**
- **Grafana + InfluxDB** — Connections per country over time, top-talker clients
- **Home Assistant** — Sensor per client, automations on unusual countries ("Alert: Client X connected to CN/RU")
- **Node-RED** — Custom alerting workflows
- **Any MQTT consumer** — statistics, logging, or archival

**Implementation:** Pure-Python MQTT 3.1.1 client (zero external dependencies). QoS 0, single persistent TCP connection with keepalive. CPU impact: negligible (~0 %).

---

## 🌍 Accessing the Globe

Open a browser and navigate to:

```
http://<YOUR-FIREWALL-IP>:3333
```

Oder bei aktiviertem HTTPS:

```
https://<YOUR-FIREWALL-IP>:3333
```

Replace `3333` with whatever listen port you configured.

---

## 🗑️ Uninstallation

```bash
sh /usr/local/opnsense/scripts/tcpgeo/uninstall.sh
```

This removes all plugin files. The TCPGeo configuration in `config.xml` is left intact and cleaned up automatically on the next firmware update.

---

## 🏗️ Architecture

```
os-tcpgeo/
├── install.sh                              # Single-script installer
├── uninstall.sh                            # Clean removal
├── pkg-descr                               # Package description
├── Screenshot.jpg                          # Globe screenshot
└── src/
    ├── etc/
    │   ├── inc/plugins.inc.d/
    │   │   └── tcpgeo.inc                  # OPNsense service hook
    │   └── rc.d/
    │       └── tcpgeo                      # FreeBSD rc.d service script
    └── opnsense/
        ├── mvc/app/
        │   ├── controllers/OPNsense/Tcpgeo/
        │   │   ├── IndexController.php     # Settings page controller
        │   │   └── Api/
        │   │       ├── SettingsController.php
        │   │       └── ServiceController.php
        │   ├── models/OPNsense/Tcpgeo/
        │   │   ├── Tcpgeo.xml              # Data model definition
        │   │   ├── Tcpgeo.php              # Model class
        │   │   ├── ACL/ACL.xml             # Access control
        │   │   └── Menu/Menu.xml           # Navigation menu entry
        │   └── views/OPNsense/Tcpgeo/
        │       └── index.volt              # Settings page template
        ├── scripts/tcpgeo/
        │   ├── server.py                   # Python aiohttp server (HTTP + WS)
        │   ├── capture.py                  # ConnectionTracker (SYN-only + pfctl enrichment)
        │   ├── mqtt_client.py               # Pure-Python MQTT 3.1.1 client + publisher
        │   ├── geoip_resolver.py           # MaxMind GeoIP lookup
        │   ├── download_geoip.py           # GeoIP database downloader (SHA256)
        │   ├── generate_config.py          # Reads OPNsense XML → JSON config
        │   ├── reconfigure.sh              # configd reconfigure action
        │   ├── status.sh                   # configd status action
        │   ├── requirements.txt            # Python dependencies
        │   └── frontend/
        │       ├── index.html              # Globe HTML page
        │       ├── globe.js                # 3D visualization (Globe.gl + Three.js)
        │       ├── cyberpunk.css           # Cyberpunk UI styling
        │       ├── three.min.js            # Three.js v0.160.0 (lokal)
        │       ├── globe.gl.min.js         # Globe.gl v2.32.0 (lokal)
        │       ├── countries-110m.json      # World atlas topology (lokal)
        │       └── fonts/                  # Orbitron + Roboto Mono (lokal)
        └── service/conf/actions.d/
            └── actions_tcpgeo.conf         # configd action definitions
```

---

## 🔧 Technical Details

| Layer | Technology |
|-------|-----------|
| Backend | Python 3 + aiohttp (async HTTP & WebSocket) |
| Packet Capture | SYN-only BPF tcpdump + pfctl byte-count enrichment |
| GeoIP | MaxMind GeoLite2-City (`maxminddb`) |
| Frontend | [Globe.gl](https://globe.gl) 2.32 + [Three.js](https://threejs.org) 0.160 (lokal gebündelt) |
| MQTT Export | Pure-Python MQTT 3.1.1 (keine externe Abhängigkeit) |
| Transport | Native WebSocket (JSON messages) |
| OPNsense | MVC Framework (Phalcon PHP), configd, FreeBSD rc.d |

### Performance Architecture

- **Backend sampling**: When packet buffer exceeds threshold, evenly samples across the batch (max 60 packets per flush)
- **Separated render loops**: WebGL globe runs on `requestAnimationFrame`; DOM updates run on `requestIdleCallback`
- **Zero DOM mutations**: 10 pre-created event rows — only `textContent` / `style` updates, no reflow
- **GPU labels**: City/country labels rendered as GPU sprites via Globe.gl's `labelsData()` API
- **No CSS blur**: `backdrop-filter` removed; `contain: layout style paint` on all HUD overlays

---

## � Security

Das Plugin wurde umfassend gehärtet:

| Maßnahme | Details |
|----------|--------|
| Privilege Separation | Service läuft als `nobody`, nur `tcpdump` und `pfctl` werden via sudoers eskaliert |
| Authentication | Optionaler HTTP Basic Auth Schutz für das Globe-Frontend |
| WebSocket Limits | Max. 10 Clients, 5 Connects/IP/Min, 30s Heartbeat |
| IP Privacy | Letztes Oktett standardmäßig maskiert (z.B. `1.2.3.xxx`) |
| XSS Prevention | Keine `innerHTML`-Nutzung, alle Werte via `textContent` + Regex-Validierung |
| Path Traversal | `resolve()` + `is_relative_to()` für statische Dateien |
| GeoIP Integrity | SHA256-Prüfsumme bei jedem Download verifiziert |
| Input Validation | Interface-Names, Farbcodes, Ports serverseitig validiert |
| Config Security | `config.json` mit `chmod 640` / `root:nobody` gesichert |
| TLS/HTTPS | Optionales HTTPS mit selbstsigniertem oder OPNsense-Zertifikat (TLS 1.2+) |
| MQTT Security | Optionale Username/Password-Authentifizierung, Credentials in config.json (chmod 640) |
| Timing-safe Auth | Passwortvergleich via `hmac.compare_digest()` |

Die vollständige Analyse aller 24 Dateien ist in [`security.md`](security.md) dokumentiert.

---

## �📄 License

MIT

---

## 🙏 Credits

- [Globe.GL](https://globe.gl) — WebGL globe visualization
- [Three.js](https://threejs.org) — 3D rendering engine
- [MaxMind GeoLite2](https://dev.maxmind.com/geoip/geolite2-free-geolite2-databases) — GeoIP database
- [OPNsense](https://opnsense.org) — Open source firewall platform
