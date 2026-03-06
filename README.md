# os-tcpgeo

**OPNsense Live Traffic Globe Plugin**

Real-time network traffic visualization on an interactive 3D globe — fully integrated into the OPNsense web UI.

![Screenshot](Screenshot.jpg)

---

## ✨ Features

- **Cyberpunk 3D Globe** — dark material, neon country borders, animated directional arcs
- **Live Packet Capture** — SYN-only BPF kernel filter with pfctl byte-count enrichment (~0 % CPU)
- **Multi-Interface Capture** — separate `tcpdump` per WAN and LAN interface, supports dual-WAN and VLANs
- **Client Detection** — LAN capture sees pre-NAT traffic → identifies actual client IPs (e.g. `192.168.1.50`)
- **pfctl Enrichment** — background thread polls kernel state table every 30 s for byte counts and NAT address recovery
- **Collector (Nacherfassung)** — enrichment automatically creates entries for connections whose SYN was missed (startup race, pre-existing connections)
- **GeoIP Resolution** — MaxMind GeoLite2-City with automatic weekly database updates (SHA256-verified)
- **Port-based Arc Colors** — assign a unique color to each port (443 → cyan, 80 → green, …)
- **Direction Detection** — inbound arcs point toward the firewall, outbound arcs are multicolor
- **GPU-rendered Labels** — city/country names appear at arc endpoints on the globe
- **Full OPNsense Integration** — settings page under *Services → TCPGeo*, start/stop/status via configd
- **High-throughput Optimized** — smart backend sampling, zero-DOM-mutation event feed, separated render loops
- **MQTT Export** — periodic publish of connection statistics (4 topics, pure-Python MQTT 3.1.1, zero dependencies)
- **Grafana + InfluxDB Dashboards** — ready-to-import per-client and overview dashboards with Node-RED flow
- **Single-script Install & Uninstall** — no pkg repo required, just `sh install.sh`
- **100% Offline** — Three.js, Globe.gl, Fonts und Geodaten lokal gebündelt (keine CDN-Abhängigkeiten)
- **Security-Hardened** — Privilege Separation (nobody), Basic Auth, WebSocket Rate-Limiting, IP-Masking, XSS-Schutz

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

### ConnectionTracker (Multi-Interface SYN Capture)

TCPGeo uses a unified **ConnectionTracker** that runs one lightweight `tcpdump` per configured interface and enriches connections with byte counts from the kernel state table.

| Property | Value |
|----------|-------|
| CPU Load | ~0 % (BPF kernel filter drops 99.9 % of packets before userspace) |
| Latency | Real-time (new connections appear on the globe instantly) |
| Byte Counts | ✓ via periodic `pfctl -ss -v` enrichment (every 30 s) |
| Client Detection | ✓ LAN capture sees pre-NAT source IPs → real client identification |
| Requirements | None — works out of the box on any OPNsense system |

#### 1. SYN-only BPF kernel filter

Each `tcpdump` instance uses an identical BPF filter compiled into the kernel:

```
ip and (tcp[tcpflags] & (tcp-syn) != 0 and tcp[tcpflags] & (tcp-ack) = 0)
     or (udp and (dst port 53 or 443 or 123 or 3478 or 51820))
     and not broadcast and not multicast
```

Only TCP SYN packets (first packet of a new connection) and selected UDP traffic (DNS, QUIC/HTTP3, NTP, STUN, WireGuard) pass the filter. This reduces events from ~10,000/s to ~5–50/s with near-zero CPU cost.

#### 2. Multi-interface capture (WAN + LAN)

TCPGeo spawns **separate `tcpdump` processes** for each configured interface. This is the key to both direction detection and client identification:

| Interface role | What it sees | Purpose |
|----------------|-------------|---------|
| **WAN** (e.g. `igb0`) | Post-NAT traffic: firewall IP ↔ remote | Incoming connections (remote → firewall) |
| **LAN** (e.g. `igb1`) | Pre-NAT traffic: client IP → remote | **Outgoing connections with real client IP** |

**Why this matters:** On the WAN interface, all outgoing traffic appears to originate from the firewall's public IP (due to NAT). The LAN interface sees the traffic *before* NAT is applied, so the source IP is the actual client (e.g. `192.168.1.50`). This is what makes per-client analytics, MQTT per-client topics, and Grafana client dashboards possible.

Multiple WAN and LAN interfaces are supported (e.g. dual-WAN, VLANs). Configure them as comma-separated lists in the OPNsense UI.

#### 3. Direction & client detection

For every captured packet, the ConnectionTracker determines direction by comparing both IPs against the known local IP set (all WAN IPs + all LAN IPs + VIPs):

```
src_ip is local, dst_ip is public  →  outgoing  (client = src_ip)
src_ip is public, dst_ip is local  →  incoming  (client = dst_ip)
both local                          →  ignored   (internal traffic)
```

The `localIP` field in each event contains the real client IP (from LAN capture) or the firewall IP (from WAN capture).

#### 4. pfctl byte-count enrichment

A background thread polls the kernel state table every 30 seconds via `pfctl -ss -v`, piped through `grep` with all known local IPs:

```
pfctl -ss -v | grep -F -A 1 -e 192.168.1.1 -e 10.0.0.1 -e ...
```

Both commands run in parallel (C-level pipe), so Python only sees matching states with their byte counters. For each active connection found:

- **Byte counts** (both directions) are extracted from the stats line
- **NAT translation** is parsed from the parenthesized address in pfctl output (e.g. `(203.0.113.1:12345)`) — this recovers the original client IP even in NAT'd states
- **Direction** is re-derived from the enriched state
- The enriched data is emitted as an **update packet** (`update: True`) through the same callback pipeline

These update packets flow to both the **globe frontend** (arc thickness scales with bytes) and the **MQTT publisher** (byte counters in all topics).

#### 5. UDP dedup

Identical UDP flows (same remote IP + destination port) are suppressed for 30 seconds to avoid flooding from DNS bursts. The dedup cache auto-prunes at 5,000 entries.

#### 6. Capture watchdog

An async watchdog checks every 5 seconds whether the capture processes are still running. If a `tcpdump` dies unexpectedly (e.g. interface goes down), it is automatically restarted.

### Collector (Nacherfassung / Enrichment-based Connection Recovery)

Because TCPGeo relies on SYN packets to detect new connections, there are edge cases where a connection is **already active** but its SYN was never seen:

| Scenario | Cause |
|----------|-------|
| **Startup race** | Connections established in the first moments before `tcpdump` is fully running |
| **Pre-existing connections** | Long-lived connections opened before TCPGeo was started |
| **Interface flap** | SYN captured on an interface that briefly went down, then came back |

The **Collector** solves this automatically for both the globe and MQTT — no user action required.

**How it works:**

1. **MQTT before capture** — `start_mqtt()` is called before `start_capture()` in the startup sequence. This ensures the MQTT publisher is ready to receive data before the first SYN arrives.
2. **pfctl discovers all active connections** — the enrichment cycle (every 30 s) runs `pfctl -ss -v` which returns *all* entries in the kernel state table, including connections whose SYN was never seen by TCPGeo.
3. **Automatic entry creation** — when an enrichment update arrives for a connection that has no corresponding entry in the MQTT aggregation tables (`_out_stats`, `_out_detail`, `_in_stats`), the Collector creates a new entry with `count: 1` and the current byte count. The connection is GeoIP-resolved (country, city) and port-labeled just like any SYN-triggered entry.
4. **Normal updates afterwards** — subsequent enrichment cycles update the byte count of the existing entry normally.

This works across all interfaces: a connection seen only in the pfctl state table (which is system-wide) gets properly attributed to the correct client IP through NAT address parsing.

**Impact on data:**

| Aspect | Behavior |
|--------|----------|
| Connection counts | May be slightly understated (multiple missed SYNs for the same flow still counted as 1) — conservative by design |
| Byte counts | **Accurate** — sourced from `pfctl` kernel counters, independent of SYN detection |
| Latency | Entry appears with up to 30 s delay (one `ENRICH_INTERVAL`) after connection was established |
| Deduplication | Key-based (`client + country + city + port`) — no duplicate entries possible |
| Direction | Correctly derived from pfctl state, including NAT translation |

### Data Pipeline

Every connection event flows through a unified pipeline that feeds both the live globe and the MQTT analytics:

```
  ┌──────────────────────────────────────────────────────────────────┐
  │                    ConnectionTracker                             │
  │                                                                  │
  │  tcpdump (WAN)  ──┐                                             │
  │  tcpdump (LAN)  ──┼──→  _parse_line()  ──→  on_packet()        │
  │  tcpdump (OPTx) ──┘     (SYN + UDP)         (new connection)   │
  │                                                                  │
  │  pfctl -ss -v ─────→  _poll_byte_counts() ──→  on_packet()     │
  │  (every 30 s)          (enrichment)            (update=True)    │
  └─────────────────────────────┬────────────────────────────────────┘
                                │
                                ▼
                        server.py: on_packet()
                        ├── GeoIP resolve (country, city, lat, lon)
                        ├── Port label + color lookup
                        ├── IP masking (if enabled)
                        │
                        ├──→  packet_buffer  ──→  flush_packets()  ──→  WebSocket → Globe
                        │     (SYN always sent, updates sampled)
                        │
                        └──→  mqtt_pub.on_packet()
                              ├── New connection: aggregate into stats tables
                              └── update=True:
                                  ├── Update byte counters
                                  └── Collector: create entry if SYN was missed
```

Both the globe and MQTT receive the same enriched data. The globe shows real-time arcs; MQTT accumulates statistics for long-term analytics.

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

#### Ready-to-use Integration: Node-RED + InfluxDB + Grafana

This repository includes a complete analytics stack:

| File | Description |
|------|-------------|
| [`nodered-flow-influxdb.json`](nodered-flow-influxdb.json) | Node-RED flow: MQTT → InfluxDB 1.6 (7 measurements, empty-tag-safe) |
| [`grafana-dashboard-client.json`](grafana-dashboard-client.json) | Grafana 12.4: Per-client analysis (10 panels, dropdown selector) |
| [`grafana-dashboard-overview.json`](grafana-dashboard-overview.json) | Grafana 12.4: All-clients overview (15 panels, 4 sections) |

**Setup:**

1. Import `nodered-flow-influxdb.json` into Node-RED → configure MQTT broker IP and InfluxDB connection
2. Create InfluxDB database: `CREATE DATABASE tcpgeo`
3. Import Grafana dashboards → set InfluxDB datasource

The Node-RED flow subscribes to all 4 MQTT topics and writes 7 InfluxDB measurements:
- `tcpgeo_outgoing_total` / `tcpgeo_outgoing_countries` — per-client outgoing stats
- `tcpgeo_incoming_total` / `tcpgeo_incoming_countries` — per-port incoming stats
- `tcpgeo_client_detail` — per-client detailed connections (country, city, port, bytes)
- `tcpgeo_connections` — recent connection snapshots
- `tcpgeo_stats_incoming` — per-port incoming detail with country breakdown

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
├── nodered-flow-influxdb.json              # Node-RED: MQTT → InfluxDB 1.6
├── grafana-dashboard-client.json           # Grafana: per-client analysis (10 panels)
├── grafana-dashboard-overview.json         # Grafana: all-clients overview (15 panels)
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
        │   ├── server.py                   # Python aiohttp server (HTTP + WS + data pipeline)
        │   ├── capture.py                  # ConnectionTracker (multi-interface SYN + pfctl enrichment + Collector)
        │   ├── mqtt_client.py               # Pure-Python MQTT 3.1.1 client + 4-topic publisher
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
| Packet Capture | Multi-interface SYN-only BPF `tcpdump` (one per WAN/LAN interface) |
| Byte Enrichment | `pfctl -ss -v` kernel state table polling (every 30 s) with NAT address parsing |
| Client Detection | LAN capture sees pre-NAT source IPs → per-client attribution |
| Collector | Enrichment-based recovery — creates entries for missed SYNs from pfctl state data |
| GeoIP | MaxMind GeoLite2-City (`maxminddb`) |
| Frontend | [Globe.gl](https://globe.gl) 2.32 + [Three.js](https://threejs.org) 0.160 (lokal gebündelt) |
| MQTT Export | Pure-Python MQTT 3.1.1 (keine externe Abhängigkeit), 4 topics, QoS 0, retain |
| Analytics | Node-RED → InfluxDB 1.6 → Grafana 12.4 (dashboards included) |
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
| IP Privacy | Letztes Oktett standardmäßig maskiert (z.B. `1.2.3.xxx`) — gilt für Globe UND MQTT |
| XSS Prevention | Keine `innerHTML`-Nutzung, alle Werte via `textContent` + Regex-Validierung |
| Path Traversal | `resolve()` + `is_relative_to()` für statische Dateien |
| GeoIP Integrity | SHA256-Prüfsumme bei jedem Download verifiziert |
| Input Validation | Interface-Names, Farbcodes, Ports serverseitig validiert |
| Config Security | `config.json` mit `chmod 640` / `root:nobody` gesichert |
| TLS/HTTPS | Optionales HTTPS mit selbstsigniertem oder OPNsense-Zertifikat (TLS 1.2+) |
| MQTT Security | Optionale Username/Password-Authentifizierung, Publish-only (kein Subscribe), Credentials in config.json |
| Timing-safe Auth | Passwortvergleich via `hmac.compare_digest()` |
| Startup Safety | SIGTERM/SIGINT absorption during startup prevents configd signal kills |
| Collector Safety | Key-based deduplication, kernel-only data source, no external input |

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
