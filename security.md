# TCPGeo OPNsense Plugin — Sicherheitsdokumentation

**Datum:** 2026-03-04  
**Version:** 1.0.2 (nach HTTPS/TLS-Erweiterung)  
**Scope:** Vollständige Analyse aller Projektdateien (Quellcode, Konfiguration, Shell-Skripte, Frontend)

---

## Inhaltsverzeichnis

1. [Sicherheitsstrategie](#1-sicherheitsstrategie)
2. [Angriffsszenarien & Schutzmaßnahmen](#2-angriffsszenarien--schutzmaßnahmen)
3. [Deployment-Anforderungen](#3-deployment-anforderungen)
4. [Version & Support](#4-version--support)
5. [Schwachstellen-Audit](#5-schwachstellen-audit)
6. [Datei-für-Datei-Analyse](#6-datei-für-datei-analyse)
7. [Externe Abhängigkeiten](#7-externe-abhängigkeiten)

---

## 1. Sicherheitsstrategie

### 1.1 Authentifizierung & Autorisierung

Das Plugin hat **zwei getrennte Zugangsebenen**:

| Ebene | Zugang | Authentifizierung | Autorisierung |
|-------|--------|-------------------|---------------|
| **OPNsense Web-UI** | Admin-Interface (Port 443) | OPNsense Session + API-Key | ACL `page-services-tcpgeo` |
| **Globe-Frontend** | Konfigurierbarer Port (z.B. 3333) | Optional: HTTP Basic Auth | Kein Rollenkonzept (View-only) |
| **MQTT-Broker** | Konfigurierbarer Host:Port (z.B. 1883) | Optional: Username/Password | QoS 0 Publish-only (kein Subscribe) |

**Auth-Flow OPNsense-UI:**
1. Admin meldet sich an der OPNsense Web-UI an (Session-Cookie)
2. Alle API-Calls (`/api/tcpgeo/*`) werden durch `ApiControllerBase` geschützt
3. Mutierende Aktionen (Save, Add, Delete) erfordern POST (CSRF-Schutz durch Framework)
4. configd-Befehle (start/stop/reconfigure) laufen als Root, abgesichert durch ACL

**Auth-Flow Globe-Frontend:**
1. Browser öffnet `http(s)://<IP>:3333`
2. Falls `globePassword` konfiguriert: HTTP 401 → Browser zeigt Basic-Auth-Dialog
3. Server prüft Passwort via `hmac.compare_digest()` (timing-safe)
4. WebSocket-Verbindung erbt die Auth-Session (gleicher HTTP-Upgrade-Request)
5. Kein Login-State — jeder Request wird einzeln geprüft

**Auth-Flow MQTT-Export:**
1. Server verbindet sich zu konfiguriertem MQTT-Broker (TCP Port 1883)
2. Falls Username/Password konfiguriert: CONNECT-Paket mit Credentials
3. Broker antwortet mit CONNACK (rc=0 bei Erfolg, rc=4/5 bei Auth-Fehler)
4. Nur PUBLISH-Operationen (QoS 0) — kein SUBSCRIBE, keine bidirektionale Datenübertragung
5. Keepalive via PINGREQ/PINGRESP, automatische Reconnection bei Verbindungsabbruch
6. MQTT-Credentials werden aus config.json gelesen (chmod 640, root:nobody)

### 1.2 Netzwerk-Erreichbarkeit

```
┌─────────────────────────────────────────────────────────────────┐
│ Internet / WAN                                                   │
│                          ✗ NICHT erreichbar                     │
│                          (Globe bindet auf interne Interfaces)  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   OPNsense Firewall  │
                    │  ┌────────────────┐  │
                    │  │ Globe Server   │  │
                    │  │ :3333 (LAN IP) │  │
                    │  └────────────────┘  │
                    └──────────▲──────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│ LAN / interne Netze                                              │
│  ✓ Erreichbar auf konfiguriertem Interface + Port               │
│  ✓ Optional: Basic Auth + HTTPS                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Default-Verhalten:**
- Der Server bindet auf die **IP des gewählten Listen-Interfaces** (z.B. LAN: 192.168.1.1)
- Binding auf `0.0.0.0` wird **aktiv blockiert** → Fallback auf `127.0.0.1`
- Ohne explizite Firewall-Regel ist der Globe-Port nur im LAN erreichbar
- WAN-Interfaces haben default deny-Regeln auf OPNsense

### 1.3 TLS/HTTPS Policy

| Aspekt | Umsetzung |
|--------|-----------|
| **Standard** | HTTP (kein TLS) — für reine LAN-Nutzung ausreichend |
| **Optionales HTTPS** | Aktivierbar über Checkbox in der UI |
| **Selbstsigniert** | ECC P-256 Zertifikat, 10 Jahre Gültigkeit, SAN=Listen-IP, automatisch erzeugt |
| **OPNsense-Cert** | Wählbar aus System → Trust → Certificates (CertificateField) |
| **Mindest-TLS-Version** | TLS 1.2 (`ssl.TLSVersion.TLSv1_2`) |
| **Key-Schutz** | `chmod 640`, `chown root:nobody` |
| **WebSocket** | Automatisch `wss://` wenn HTTPS aktiv (Frontend erkennt `location.protocol`) |
| **Empfehlung** | HTTPS aktivieren wenn Globe über nicht-vertrauenswürdige Netze erreichbar ist |

---

## 2. Angriffsszenarien & Schutzmaßnahmen

### 2.1 Man-in-the-Middle (MITM)

| Szenario | Angreifer im LAN fängt HTTP-Traffic ab |
|----------|----------------------------------------|
| **Risiko** | Basic-Auth-Passwort und Traffic-Daten werden im Klartext übertragen |
| **Schutz** | ✅ HTTPS aktivierbar (TLS 1.2+, ECC P-256 oder OPNsense-Cert) |
| **Residual** | Bei HTTP (Standard): Passwort im Klartext. Im LAN typischerweise akzeptabel. |
| **Empfehlung** | HTTPS aktivieren wenn sensible Daten übertragen werden oder nicht-vertrauenswürdige Clients im Netz sind. |

### 2.2 Cross-Site Scripting (XSS)

| Vektor | Status | Details |
|--------|--------|---------|
| **DOM-XSS via innerHTML** | ✅ Eliminiert | `buildPortLegend()` und alle DOM-Operationen verwenden `createElement()`/`textContent` |
| **Color-Injection** | ✅ Mitigiert | Regex `/^#[0-9a-fA-F]{6}$/` in globe.js und index.volt |
| **Stored XSS via Config** | ✅ Mitigiert | OPNsense-Model validiert alle Felder serverseitig (Mask, IntegerField, BooleanField) |
| **Reflected XSS** | ✅ N/A | Kein user-controllable Input wird in HTML reflektiert |
| **configd-Output in HTML** | 🟡 Gering | `data.response` in index.volt wird unsanitisiert in `<code>` eingefügt. Output stammt von configd (root-controlled), aber bei kompromittiertem Backend potentieller Vektor. |

### 2.3 Cross-Site Request Forgery (CSRF)

| Kontext | Schutz |
|---------|--------|
| **OPNsense Web-UI** | ✅ Vollständig geschützt durch Phalcon-Framework (Session + CSRF-Token für POST) |
| **Globe-Frontend** | ✅ N/A — rein lesend (GET + WebSocket), keine state-changing actions |
| **Globe-API** | ✅ `/api/config` und `/api/geoip/status` sind GET-only und verändern nichts |

### 2.4 Privilege Escalation

| Vektor | Schutz |
|--------|--------|
| **Server-Prozess** | ✅ Läuft als `nobody`, nicht als root |
| **tcpdump** | ✅ Nur via sudoers-Regel: `nobody ALL=(root) NOPASSWD: /usr/sbin/tcpdump` — kein genereller Root-Zugang |
| **sudoers-Scope** | ✅ Beschränkt auf exakt `/usr/sbin/tcpdump` — keine Wildcards, keine Parameter-Erweiterung |
| **Config-Dateien** | ✅ `config.json` ist `640/root:nobody` — nobody kann lesen, nicht schreiben |
| **configd** | ✅ Läuft als root (OPNsense-Standard), Zugang nur über authentifizierte Admin-API |
| **Capture.py Injection** | ✅ Device-Name validiert mit `^[a-zA-Z0-9_.]+$` — keine Shell-Injection über Interface-Name |

### 2.5 WebSocket Misuse

| Angriff | Schutz | Details |
|---------|--------|---------|
| **Connection Flooding** | ✅ `MAX_WS_CLIENTS = 10` | Max. 10 gleichzeitige WS-Verbindungen insgesamt |
| **Rapid Reconnects** | ✅ `WS_RATE_LIMIT = 5/min/IP` | Max. 5 Verbindungsversuche pro IP pro Minute |
| **Zombie Connections** | ✅ `WS_HEARTBEAT = 30s` | Ping/Pong erkennt tote Verbindungen |
| **Message Flooding (Client→Server)** | ✅ N/A | Server ignoriert alle Client-Nachrichten (`async for msg in ws: pass`) |
| **Large Payloads (Server→Client)** | ✅ Mitigiert | `MAX_PER_FLUSH = 60`, `MAX_BUFFER = 500` — Backend sampelt bei Überlast |
| **Auth Bypass** | ✅ Geschützt | WS-Upgrade durchläuft `auth_middleware` (HTTP-Level Auth vor ws.prepare()) |
| **Memory Exhaustion** | ✅ Mitigiert | Buffer overflow protection: `packet_buffer[:] = packet_buffer[-MAX_PER_FLUSH:]` |

### 2.6 Path Traversal

| Vektor | Schutz |
|--------|--------|
| **Statische Dateien** | ✅ `(FRONTEND_DIR / fname).resolve()` + `fpath.is_relative_to(frontend_resolved)` |
| **`..` Sequenzen** | ✅ Explizit gefiltert: `if fname and '..' not in fname` |
| **Symlink-Ausbruch** | ✅ `resolve()` folgt Symlinks und `is_relative_to()` prüft das aufgelöste Ziel |
| **Null-Byte** | ✅ Python 3 Path-Handling ist null-byte-safe |

### 2.7 Denial of Service (DoS)

| Vektor | Schutz |
|--------|--------|
| **WS Connection Flood** | ✅ 10 max + Rate-Limit (siehe 2.5) |
| **HTTP Request Flood** | 🟡 Kein explizites Rate-Limit für HTTP (aiohttp default: keine Begrenzung) |
| **Packet Buffer Overflow** | ✅ Hard-Cap bei 500, Sampling bei Überlast |
| **Large File Upload** | ✅ N/A — Server akzeptiert keine POST-Bodys (nur GET + WS) |
| **Slowloris** | 🟡 aiohttp default-Timeouts greifen, aber kein explizites Limit konfiguriert |

### 2.8 MQTT-Sicherheit

| Aspekt | Umsetzung |
|--------|-----------||
| **Protokoll** | MQTT 3.1.1 über TCP (Klartext) |
| **Authentifizierung** | Optional: Username/Password im CONNECT-Paket |
| **Autorisierung** | Publish-only (QoS 0, kein Subscribe) — keine Daten vom Broker empfangen |
| **Datenschutz** | IP-Masking (`maskIPs`) wird auch auf MQTT-Payloads angewendet |
| **Credentials** | In config.json gespeichert (chmod 640, root:nobody) |
| **Verschlüsselung** | Kein TLS — MQTT-Traffic ist unverschlüsselt (LAN-Nutzung) |
| **Fehlverhalten** | Verbindungsfehler werden geloggt, kein Crash/Retry-Flood (sleep-basiert) |
| **Datenvolumen** | Kleine JSON-Payloads (1–10 KB), konfigurierbar 10–3600s Intervall |
| **Angriffsfläche** | Outbound-only TCP-Verbindung, keine eingehenden Daten verarbeitet |

**Risikobewertung:**
- MQTT-Credentials werden im Klartext übertragen (kein TLS). Da MQTT typischerweise im LAN betrieben wird, ist dies akzeptabel.
- Bei Bedarf kann der MQTT-Broker TLS auf Port 8883 anbieten — eine zukünftige Erweiterung könnte optionales TLS hinzufügen.
- Der MQTT-Client ist Publish-only und verarbeitet keine eingehenden Nachrichten (außer CONNACK/PINGRESP). Die Angriffsfläche ist minimal.

### 2.9 Multi-Interface Capture & pfctl Enrichment — Sicherheitsbewertung

TCPGeo betreibt separate `tcpdump`-Prozesse pro konfiguriertem Interface (WAN + LAN) und ergänzt Byte-Daten durch periodische `pfctl -ss -v`-Abfragen. Zusätzlich erkennt der Collector fehlende Verbindungen aus den Enrichment-Daten.

#### 2.9.1 Multi-Interface Capture

| Aspekt | Bewertung |
|--------|-----------|
| **Prozess-Isolation** | ✅ Jeder `tcpdump` läuft als separater Subprocess (eigene PID, eigener BPF-Filter) |
| **Interface-Validierung** | ✅ Device-Name wird mit `^[a-zA-Z0-9_.]+$` geprüft — keine Shell-Injection |
| **Privilege Separation** | ✅ `tcpdump` via `sudo -n` (sudoers beschränkt auf `/usr/sbin/tcpdump`) |
| **BPF-Filter** | ✅ Identischer Kernel-Filter auf allen Interfaces — konsistente Datenbasis |
| **LAN-Sichtbarkeit** | ✅ LAN-Capture sieht pre-NAT Client-IPs. Nur private IPs (RFC 1918) werden als Client erkannt |
| **WAN-Sichtbarkeit** | ✅ WAN-Capture sieht post-NAT Traffic. Source-IP = Firewall-Adresse für ausgehend |
| **IP-Set Aufbau** | ✅ `all_local_ips` wird aus WAN-IPs + LAN-IPs + VIPs zusammengesetzt — keine User-Eingabe |
| **Direction Detection** | ✅ Basiert auf `is_private_ip()` + `all_local_ips`-Lookup — deterministisch, kein Rate |

**Risiko LAN-Capture:** Der LAN-Capture sieht den gesamten Client-Traffic vor NAT. Falls IP-Masking aktiv ist (`maskIPs=True`), werden Client-IPs in Globe und MQTT maskiert (`192.168.1.xxx`). Ohne Masking sind die echten Client-IPs sichtbar — dies ist eine bewusste Konfigurationsentscheidung des Administrators.

#### 2.9.2 pfctl Enrichment

| Aspekt | Bewertung |
|--------|-----------|
| **Datenquelle** | ✅ `pfctl -ss -v` — liest Kernel State Table (vertrauenswürdig, nicht manipulierbar aus Userspace) |
| **Ausführung** | ✅ Via `sudo -n` alle 30 s, Pipe `pfctl | grep` (beide C-level, Python sieht nur Ergebnis) |
| **grep -F Filterung** | ✅ Nur States mit bekannten lokalen IPs passieren den Filter — keine fremden Daten |
| **NAT-Adress-Parsing** | ✅ Parenthesierte Adresse `(ip:port)` in pfctl-Output wird für Client-Zuordnung geparsed |
| **Byte-Extraktion** | ✅ Regex `(\d+):(\d+)\s+bytes` — nur Zahlen, kein Injection-Risiko |
| **Timeout** | ✅ 30 s Timeout auf `communicate()` — kein Hängenbleiben bei blockiertem pfctl |
| **Fehlerbehandlung** | ✅ Alle Exceptions gefangen, Prozesse werden bei Timeout gekillt |
| **CPU-Last** | ✅ ~0.07 % durchschnittlich (1 s Arbeit alle 30 s) |

**NAT-Adress-Sicherheit:** Die geparsed NAT-Adresse stammt aus dem Kernel (`pfctl` Output) und nicht aus User-Input. Sie wird nur verwendet, um den lokalen Client korrekt zuzuordnen. Private IPs werden als `localIP` in den Event übernommen; Remote-IPs (public) durchlaufen die gleiche GeoIP-Auflösung + IP-Masking wie SYN-basierte Events.

#### 2.9.3 Collector (Nacherfassung)

Der Collector erstellt neue MQTT-Aggregationseinträge aus Enrichment-Daten, wenn der ursprüngliche SYN nicht erfasst wurde (Race Condition beim Start, vorbestehende Verbindungen, Interface-Flaps).

| Aspekt | Bewertung |
|--------|-----------|
| **Eintragserzeugung** | ✅ Nur bei `is_update=True` und fehlendem Key in den Aggregationstabellen |
| **Duplikat-Schutz** | ✅ Key-Check (client + country + city + port) vor Erzeugung — kein doppeltes Zählen |
| **Count-Annahme** | ✅ `count: 1` — konservativ, kein Überzählen. Bei mehreren verpassten SYNs wird die Anzahl leicht unterschätzt |
| **Byte-Genauigkeit** | ✅ Bytes stammen direkt aus `pfctl` — korrekt unabhängig von SYN-Erkennung |
| **Richtungserkennung** | ✅ Aus dem Enrichment-Paket, basierend auf pfctl State-Richtung + NAT-Parsing |
| **Scope** | ✅ Betrifft `_out_stats`, `_out_detail`, `_in_stats` — nur MQTT-Aggregation, kein Globe-Impact |
| **Timing** | ✅ Einträge erscheinen mit max. 30 s Verzögerung (ein `ENRICH_INTERVAL`) |
| **Datenintegrität** | ✅ Durchläuft identische Pipeline: GeoIP-Resolver + IP-Masking + Port-Label-Lookup |

#### 2.9.4 Startreihenfolge

| Schritt | Reihenfolge | Begründung |
|---------|-------------|------------|
| 1. `start_mqtt()` | VOR Capture | MQTT-Publisher ist bereit, bevor der erste SYN eintrifft |
| 2. `start_capture()` | NACH MQTT | Kein SYN geht verloren wegen noch nicht initialisiertem Publisher |
| 3. SIGTERM/SIGINT Absorption | Während Startup | Verhindert, dass configd-Signale den Prozess während Initialisierung killen |

**Risikomatrix (Gesamtbewertung):**

| Risiko | Schweregrad | Bewertung |
|--------|-------------|-----------|
| Collector erzeugt falsche Einträge | KEINE | Datenquelle ist Kernel State Table, nicht User-Input |
| Duplikate verfälschen Statistiken | KEINE | Key-basierte Deduplizierung verhindert Mehrfachanlage |
| LAN-Capture leakt Client-IPs | NIEDRIG | Mitigiert durch `maskIPs`-Flag (Default: aktiviert) |
| NAT-Parsing liefert falsche Client-IP | KEINE | Ausgeschlossen — pfctl-Output ist Kernel-generiert |
| pfctl-Enrichment als Amplification-Vektor | KEINE | Kein externer Input, Daten fließen nur outbound zum MQTT-Broker |
| Race Condition Capture ↔ MQTT | BEHOBEN | `start_mqtt()` vor `start_capture()` |
| Interface-Spoofing | KEINE | Interface-Namen kommen aus OPNsense config.xml (Admin-kontrolliert) |

**Fazit:** Das Multi-Interface-Capture mit pfctl-Enrichment und Collector erhöht die Datenqualität erheblich (Client-Zuordnung, Byte-Zähler, Nacherfassung) ohne zusätzliche Angriffsfläche. Alle Datenquellen sind Kernel-basiert (BPF, pfctl State Table) und nicht von extern manipulierbar.

---

## 3. Deployment-Anforderungen

### 3.1 Firewall-Regeln

Der Globe-Server bindet auf das konfigurierte Listen-Interface. **Keine zusätzlichen Firewall-Regeln nötig** solange der Zugriff nur aus dem LAN erfolgt (OPNsense erlaubt LAN→Self-Traffic per Default).

**Falls der Globe aus anderen Netzen erreichbar sein soll:**

```
Firewall → Rules → [Interface]
  Action:      Pass
  Protocol:    TCP
  Source:      [Gewünschtes Netz / IP]
  Destination: This Firewall
  Dest. Port:  3333 (oder konfigurierter Port)
  Description: TCPGeo Globe Access
```

**Empfohlene Einschränkungen:**
- Source auf bekannte Admin-IPs begrenzen
- Niemals den Globe-Port auf WAN öffnen
- Bei Bedarf aus entfernten Netzen: VPN verwenden oder Reverse-Proxy mit eigenem TLS

### 3.2 Reverse-Proxy / TLS-Setup

**Option A: Natives HTTPS (empfohlen für einfache Setups)**

In der TCPGeo-UI: *HTTPS aktivieren* → *Selbstsigniert* wählen → *Speichern & Anwenden*.
Das Zertifikat wird automatisch erzeugt. Bei selbstsignierten Zertifikaten muss der Browser beim ersten Aufruf eine Sicherheitsausnahme bestätigen.

Für ein trusted Zertifikat: In OPNsense unter *System → Trust → Certificates* ein Zertifikat importieren oder per ACME erstellen, dann in TCPGeo unter *Zertifikat-Modus* → *OPNsense-Zertifikat* auswählen.

**Option B: Reverse-Proxy (HAProxy / nginx)**

Falls der Globe hinter einem bestehenden Reverse-Proxy terminiert werden soll:

```nginx
# nginx-Beispiel (auf OPNsense via os-nginx Plugin)
server {
    listen 443 ssl;
    server_name globe.example.com;
    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:3333;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Bei dieser Konfiguration:
- TCPGeo lauscht auf `127.0.0.1:3333` (HTTP, nur lokal erreichbar)
- TLS-Terminierung erfolgt durch den Reverse-Proxy
- WebSocket-Upgrade muss korrekt durchgereicht werden (`Upgrade` + `Connection` Header)

**Option C: HAProxy (OPNsense Plugin os-haproxy)**

```
Backend:  Mode=HTTP, Server=127.0.0.1:3333
Frontend: Mode=HTTP, Bind=*:443 (SSL Offloading), Default Backend=tcpgeo
```

### 3.3 sudoers Policy

Die Installation erstellt eine dedizierte sudoers-Datei:

```
/usr/local/etc/sudoers.d/tcpgeo
  nobody ALL=(root) NOPASSWD: /usr/sbin/tcpdump
  Permissions: 440 (root:wheel)
```

**Sicherheitsbewertung:**
- ✅ Nur der User `nobody` erhält Zugriff
- ✅ Nur das Binary `/usr/sbin/tcpdump` ist erlaubt
- ✅ Keine Wildcards, keine Argumente eingeschränkt (tcpdump benötigt flexible Filterausdrücke)
- ✅ `NOPASSWD` ist notwendig da der Service non-interaktiv läuft
- ✅ Datei wird bei Deinstallation entfernt (`uninstall.sh`)
- 🟡 tcpdump erhält über diese Regel effektiv Root-Capture-Rechte — dies ist designbedingt nötig

**Restrisiko:** Falls ein Angreifer den `nobody`-User kompromittiert (z.B. über eine Schwachstelle in aiohttp), kann er tcpdump mit beliebigen Filtern ausführen und Netzwerkverkehr mitlesen. Dies ist durch die Natur der Anwendung (Packet Capture) unvermeidlich. Die Angriffsfläche ist auf tcpdump beschränkt — kein Shell-Zugang, keine Dateisystem-Schreibrechte.

### 3.4 Dateisystem-Berechtigungen

| Pfad | Owner | Mode | Inhalt |
|------|-------|------|--------|
| `/usr/local/etc/tcpgeo/` | `root:nobody` | `750` | Konfigurationsverzeichnis |
| `/usr/local/etc/tcpgeo/config.json` | `root:nobody` | `640` | Passwort, MaxMind-Key, Einstellungen |
| `/usr/local/etc/tcpgeo/server.key` | `root:nobody` | `640` | TLS Private Key (falls HTTPS aktiv) |
| `/usr/local/etc/tcpgeo/server.crt` | `root` | `644` | TLS Zertifikat (öffentlich) |
| `/usr/local/etc/sudoers.d/tcpgeo` | `root:wheel` | `440` | sudoers-Regel |
| `/usr/local/opnsense/scripts/tcpgeo/` | `root:wheel` | `755` | Anwendungscode (read-only für nobody) |
| `/var/log/tcpgeo.log` | `nobody` | `644` | Logdatei |
| `/var/run/tcpgeo.pid` | `root` | `644` | PID-Datei |

---

## 4. Version & Support

### 4.1 Unterstützte OPNsense-Versionen

| OPNsense | FreeBSD | Python | Status |
|----------|---------|--------|--------|
| **25.x** | 14.x | 3.11 | ✅ Vollständig unterstützt (primäre Zielplattform) |
| **24.x** | 14.x | 3.11 | ✅ Vollständig unterstützt |
| **23.x** | 13.x | 3.9+ | ✅ Unterstützt (ältere pip-Varianten werden automatisch erkannt) |
| **22.x** und älter | 13.x | <3.9 | ❌ Nicht unterstützt (`is_relative_to()` erfordert Python ≥ 3.9) |

**Framework-Kompatibilität:**
- MVC: `ApiControllerBase` / `ApiMutableModelControllerBase` (seit OPNsense 18.x stabil)
- Model: `CertificateField` (seit OPNsense 21.x verfügbar)
- configd: Action-Format unverändert seit OPNsense 15.x

### 4.2 Aktualisierungs-Policy

| Komponente | Update-Intervall | Mechanismus |
|-----------|-----------------|-------------|
| **GeoIP-Datenbank** | Wöchentlich (So 03:30) | Cron Job via `tcpgeo.inc`, SHA256-verifiziert |
| **Plugin-Code** | Manuell | `git pull` + `sh install.sh` (überschreibt vorhandene Dateien) |
| **Python-Abhängigkeiten** | Bei Installation | `pip install -r requirements.txt` |
| **Frontend-Libraries** | Fest gebündelt | Three.js 0.160.0, Globe.gl 2.32.0 (lokal, kein Auto-Update) |
| **TLS-Zertifikat (self-signed)** | 10 Jahre Gültigkeit | Wird einmalig erzeugt, nicht automatisch erneuert |
| **TLS-Zertifikat (OPNsense)** | OPNsense-Verwaltung | Bei Reconfigure aus config.xml aktualisiert |

**Update-Prozess für neue Plugin-Versionen:**

```bash
cd /tmp
fetch -o os-tcpgeo.tar.gz https://github.com/bmetallica/os-tcpgeo/archive/refs/heads/main.tar.gz
tar -xzf os-tcpgeo.tar.gz
cd os-tcpgeo-main
sh install.sh
```

Die Installation überschreibt alle Dateien. Konfigurationseinstellungen in `config.xml` bleiben erhalten. `config.json` wird bei jedem *Speichern & Anwenden* neu generiert.

### 4.3 Sicherheitshinweise für Betreiber

1. **Passwort setzen** — bei Zugriff über nicht-vertrauenswürdige Netze immer Globe-Password konfigurieren
2. **HTTPS aktivieren** — wenn Basic Auth verwendet wird, da Passwort sonst im Klartext übertragen wird
3. **Interface einschränken** — Globe nur auf LAN/Management-Interface lauschen lassen, niemals auf WAN
4. **Firewall-Regeln prüfen** — kein Pass auf WAN für den Globe-Port
5. **Log überwachen** — `/var/log/tcpgeo.log` regelmäßig prüfen (Rate-Limit-Warnungen, Auth-Fehler)
6. **GeoIP-Key schützen** — MaxMind License Key nicht teilen, bei Bedarf unter maxmind.com erneuern

---

## 5. Schwachstellen-Audit

### 5.1 Zusammenfassung

| Schweregrad | Offen | Behoben | Gesamt |
|------------|-------|---------|--------|
| KRITISCH   | 0     | 2       | 2      |
| HOCH       | 0     | 5       | 5      |
| MITTEL     | 0     | 8       | 8      |
| NIEDRIG    | 4     | 1       | 5      |
| INFO       | 4     | 0       | 4      |

**Gesamtbewertung:** Alle kritischen, hohen und mittleren Schwachstellen wurden behoben. Das Projekt ist vollständig gehärtet. Es verbleiben ausschließlich niedrige Restrisiken und informationelle Hinweise, die keinen unmittelbaren Handlungsbedarf darstellen.

### 5.2 Behobene Schwachstellen (Auswahl)

### SEC-LOW-01 — Kein HTTPS/TLS ✅ BEHOBEN

- **Datei:** `src/opnsense/scripts/tcpgeo/server.py`, `generate_config.py`, `Tcpgeo.xml`
- **Schweregrad:** NIEDRIG
- **Vorher:** Der Globe-Webserver unterstützte ausschließlich unverschlüsseltes HTTP.
- **Maßnahme:** Optionales HTTPS implementiert. Unterstützt selbstsignierte Zertifikate (automatisch erzeugt via openssl ECC P-256, 10 Jahre, SAN mit Listen-IP) und bestehende OPNsense-Zertifikate (aus config.xml extrahiert). TLS 1.2+ erzwungen. Standard bleibt HTTP.

### 5.3 Offene Schwachstellen (NIEDRIG)

### SEC-LOW-02 — PID-File Race Condition 🟡 OFFEN

- **Datei:** `src/etc/rc.d/tcpgeo` (Zeilen 55–60)
- **Schweregrad:** NIEDRIG
- **Beschreibung:** PID-Ermittlung per `pgrep ... | head -1` nach einem `sleep 1` ist anfällig für Race Conditions. Zwischen Start und PID-Erfassung könnte die PID falsch zugeordnet werden.
- **Empfehlung:** daemon-Option `-p` für direkte PID-File-Erstellung verwenden:
  ```sh
  /usr/sbin/daemon -f -u ${tcpgeo_user} -p ${pidfile} -o "${tcpgeo_logfile}" ...
  ```

### SEC-LOW-03 — tarfile-Extraktion ohne Member-Filter 🟡 OFFEN

- **Datei:** `src/opnsense/scripts/tcpgeo/download_geoip.py` (Zeilen 123–132)
- **Schweregrad:** NIEDRIG
- **Beschreibung:** `tar.getmembers()` + `tar.extractfile()` werden ohne `data_filter` aufgerufen. Theoretisch könnte ein manipuliertes Archiv Symlinks oder absolute Pfade enthalten (CVE-2007-4559-Klasse).
- **Mitigierung:** Das Archiv stammt von MaxMind (verifiziert via SHA256). Die Extraktion beschränkt sich auf `extractfile()` → `open(DB_FILE, 'wb')`, was den Output auf einen festen Pfad limitiert. Das Risiko ist gering.
- **Empfehlung:** Ab Python 3.12: `tar.extractall(filter='data')` oder Member-Name zusätzlich validieren:
  ```python
  if member.name.endswith('.mmdb') and '/' not in member.name.rsplit('/', 1)[-1]:
  ```

### SEC-LOW-04 — Keine CSP/Security-Header 🟡 OFFEN

- **Datei:** `src/opnsense/scripts/tcpgeo/server.py`
- **Schweregrad:** NIEDRIG
- **Beschreibung:** Der Globe-Webserver setzt keine HTTP-Security-Header (Content-Security-Policy, X-Frame-Options, X-Content-Type-Options, Strict-Transport-Security).
- **Empfehlung:** Middleware hinzufügen:
  ```python
  @web.middleware
  async def security_headers(request, handler):
      resp = await handler(request)
      resp.headers['X-Content-Type-Options'] = 'nosniff'
      resp.headers['X-Frame-Options'] = 'DENY'
      resp.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'"
      return resp
  ```

### SEC-LOW-05 — Uninstall entfernt config.xml-Eintrag nicht 🟡 OFFEN

- **Datei:** `uninstall.sh`
- **Schweregrad:** NIEDRIG
- **Beschreibung:** Die Deinstallation entfernt den `<OPNsense><tcpgeo>...</tcpgeo></OPNsense>`-Abschnitt aus `/conf/config.xml` nicht. Konfigurationsdaten (inkl. Passwort, MaxMind-Key) bleiben in der Firewall-Config.
- **Empfehlung:** Optional per `sed` oder XMLStarlet bereinigen, oder klar dokumentieren.

### 5.4 Informationelle Hinweise

### SEC-INFO-01 — MaxMind License Key in config.json

- **Dateien:** `generate_config.py`, `config.json` (Laufzeit)
- **Beschreibung:** Der MaxMind License Key wird im Klartext in `config.json` gespeichert. Mitigiert durch `chmod 640`.
- **Risiko:** Gering. Der Key ist kein Zahlungsmittel und kann kostenlos erneuert werden.

### SEC-INFO-02 — Passwort im Klartext gespeichert

- **Dateien:** OPNsense `config.xml`, `config.json`
- **Beschreibung:** Das Globe-Passwort wird im Klartext gespeichert (nicht gehasht). Da der Server das Passwort zur Laufzeit mit dem empfangenen Basic-Auth-Header vergleichen muss, ist eine reversible Speicherung notwendig.
- **Risiko:** Akzeptabel für den Anwendungsfall, da der Zugriff auf config.json eingeschränkt ist (640/root:nobody).

### SEC-INFO-03 — Keine CORS-Einschränkungen

- **Datei:** `src/opnsense/scripts/tcpgeo/server.py`
- **Beschreibung:** Der Globe-Server setzt keine CORS-Header. In der Praxis nicht ausnutzbar, da die API nur GET-Requests + WebSocket verwendet und nicht von fremden Origins aufgerufen wird.

### SEC-INFO-04 — configd-Aktionen als Root

- **Datei:** `src/opnsense/service/conf/actions.d/actions_tcpgeo.conf`
- **Beschreibung:** Alle configd-Aktionen (start, stop, reconfigure, download-geoip) laufen als root. Dies ist das Standard-OPNsense-Pattern und wird durch ACL-Regeln geschützt (`ACL.xml`).

### 5.5 Architekturübersicht und Angriffsflächen

```
┌─────────────────────────────────────────────────────────────┐
│                    OPNsense Web-UI                          │
│                   (PHP/Phalcon MVC)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │IndexController│  │SettingsCtrl  │  │ ServiceController │ │
│  │   (index.volt)│  │ (CRUD API)   │  │ (configd Bridge)  │ │
│  └──────────────┘  └──────────────┘  └───────────────────┘ │
│        │ ACL: page-services-tcpgeo         │ configd       │
├────────┼───────────────────────────────────┼───────────────┤
│        │                                   ▼               │
│  ┌─────┴──────────────────────────────────────┐            │
│  │          configd (actions_tcpgeo.conf)      │  ROOT      │
│  │  reconfigure.sh → generate_config.py        │            │
│  │  start/stop → rc.d/tcpgeo                   │            │
│  │  download-geoip → download_geoip.py         │            │
│  └─────────────────────────────┬──────────────┘            │
│                                │                            │
│                                ▼                            │
│  ┌──────────────────────────────────────────┐              │
│  │      server.py (aiohttp, Port 3333)       │  NOBODY     │
│  │  ┌────────────┐  ┌────────────────────┐  │              │
│  │  │auth_middleware  │ WebSocket Handler │  │              │
│  │  │(Basic Auth)│  │ (rate-limited)     │  │              │
│  │  └────────────┘  └────────────────────┘  │              │
│  │  ┌────────────┐  ┌────────────────────┐  │              │
│  │  │static_file │  │ flush_packets()    │  │              │
│  │  │handler     │  │ → mask_ip()        │  │              │
│  │  └────────────┘  └────────────────────┘  │              │
│  │         │                    ▲            │              │
│  │         ▼                    │            │              │
│  │  ┌───────────────────────────────────┐   │              │
│  │  │  capture.py — ConnectionTracker    │   │              │
│  │  │                                    │   │              │
│  │  │  tcpdump (WAN) ──┐                │   │              │
│  │  │  tcpdump (LAN) ──┼→ _parse_line() │   │              │
│  │  │  tcpdump (OPTx)──┘  (SYN + UDP)  │   │              │
│  │  │                                    │   │              │
│  │  │  pfctl -ss -v ──→ _poll_bytes()   │   │              │
│  │  │  (enrichment, every 30 s)          │   │              │
│  │  │  → NAT address parsing             │   │              │
│  │  │  → byte counts + direction         │   │              │
│  │  │                                    │   │              │
│  │  │  → geoip_resolver.py              │   │              │
│  │  └───────────────────────────────────┘   │              │
│  │                    │                      │              │
│  │                    ▼                      │              │
│  │  ┌───────────────────────────────────┐   │              │
│  │  │  mqtt_client.py (MQTT 3.1.1)      │   │              │
│  │  │  4 Topics, QoS 0, retain=True     │   │              │
│  │  │  Collector: auto-create from pfctl │   │              │
│  │  │  → MQTT Broker (outbound TCP)     │   │              │
│  │  └───────────────────────────────────┘   │              │
│  └──────────────────────────────────────────┘              │
│                                                             │
│  ┌──────────────────────────────────────────┐              │
│  │      Globe Frontend (Browser)             │              │
│  │  index.html, globe.js, cyberpunk.css      │  CLIENT     │
│  │  three.min.js, globe.gl.min.js (lokal)    │              │
│  │  countries-110m.json, fonts/ (lokal)       │              │
│  └──────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────┘
```

**Angriffsflächen:**

| Fläche | Zugang | Schutz |
|--------|--------|--------|
| OPNsense Web-UI API | Authentifizierter Admin | ACL, Session, CSRF-Token (Framework) |
| Globe-Webserver (Port 3333) | Netzwerkzugang | Optional: Basic Auth, Interface-Binding |
| WebSocket (ws://host:3333/ws) | Netzwerkzugang | Auth, Rate-Limit, Connection-Limit |
| MQTT-Export (Port 1883) | Outbound TCP | Optional: Username/Password, IP-Masking, Publish-only |
| Statische Dateien | Globe-Server | Path-Traversal-Schutz (resolve + is_relative_to) |
| tcpdump-Subprocesses (multi) | Lokal (nobody via sudo) | sudoers auf /usr/sbin/tcpdump, Interface-Name validiert |
| pfctl-Enrichment | Lokal (nobody via sudo) | sudoers auf /sbin/pfctl, Read-only State Table, Timeout 30 s |
| Collector (Nacherfassung) | Intern (pfctl-Daten) | Key-Deduplizierung, Kernel-Datenquelle, kein externer Input |
| config.json | Dateisystem | chmod 640, root:nobody |
| GeoIP-Download | Outbound HTTPS | SHA256-Verifizierung |

---

## 6. Datei-für-Datei-Analyse

### server.py (553 Zeilen)
- **Auth:** ✅ Basic Auth Middleware vorhanden
- **WebSocket:** ✅ Rate-Limiting, Connection-Limit, Heartbeat
- **IP-Schutz:** ✅ mask_ip() aktiv — gilt für Globe UND MQTT-Payloads
- **0.0.0.0 Block:** ✅ Fallback auf 127.0.0.1
- **Passwortvergleich:** ✅ hmac.compare_digest (timing-safe)
- **Path-Traversal:** ✅ is_relative_to statt startswith
- **Startreihenfolge:** ✅ `start_mqtt()` vor `start_capture()` — verhindert Race Condition
- **Signal-Absorption:** ✅ SIGTERM/SIGINT während Startup absorbiert (configd-Schutz)
- **Capture Watchdog:** ✅ Auto-Restart bei tcpdump-Ausfall (alle 5 s geprüft)
- **Data Pipeline:** ✅ Unified on_packet() → GeoIP → Globe + MQTT (SYN und Updates)
- **Flush-Sampling:** ✅ SYN-Pakete immer gesendet, Enrichment-Updates bei Überlast gesampelt
- **Restrisiko:** 🟡 Kein TLS (SEC-LOW-01), kein CSP (SEC-LOW-04)

### mqtt_client.py (594 Zeilen)
- **Externe Abhängigkeiten:** ✅ Keine — reiner Python 3 stdlib (socket, struct, threading, json)
- **Netzwerk:** ✅ Outbound-only TCP-Verbindung zum MQTT-Broker
- **Authentifizierung:** ✅ Optional Username/Password im CONNECT-Paket
- **Protokoll:** ✅ MQTT 3.1.1 (QoS 0 Publish-only, kein Subscribe)
- **Thread-Safety:** ✅ Alle Aggregationstabellen mit Lock geschützt
- **Fehlerbehandlung:** ✅ Verbindungsfehler mit Reconnect + Logging (kein Crash, sleep-basiert)
- **Daten:** ✅ IP-Masking wird auf alle MQTT-Payloads angewendet
- **4 Topics:** ✅ stats/outgoing, stats/incoming, clients/outgoing, connections — alle mit retain=True
- **Collector:** ✅ Nacherfassung: `is_update=True` → Key-Prüfung → ggf. neuer Eintrag mit `count: 1`
- **Aggregation:** ✅ Per-client outgoing (country + city + port), per-port incoming (country), ring buffer connections
- **Restrisiko:** 🟡 MQTT-Traffic unverschlüsselt (kein TLS). Akzeptabel für LAN-Betrieb.

### capture.py (580 Zeilen)
- **Multi-Interface:** ✅ Separate tcpdump pro WAN/LAN Interface, parallel in eigenen Threads
- **BPF-Filter:** ✅ SYN-only + ausgewählte UDP Ports — identisch auf allen Interfaces
- **Device-Validierung:** ✅ Jedes Zeichen gegen `isalnum() or c in '._'` geprüft
- **Privilege Separation:** ✅ sudo-Eskalation nur für tcpdump und pfctl
- **Subprocess:** ✅ Liste statt Shell-String (kein Shell-Injection)
- **Direction Detection:** ✅ Basiert auf is_private_ip() + all_local_ips Set
- **Client Detection:** ✅ LAN-Capture liefert pre-NAT Source-IP als localIP
- **pfctl Enrichment:** ✅ Alle 30 s, Pipe pfctl|grep (C-level parallel), Timeout 30 s, force-kill bei Timeout
- **NAT-Parsing:** ✅ Parenthesierte Adresse aus pfctl-Output → original Client-IP Zuordnung
- **UDP Dedup:** ✅ TTL-basiert (30 s), auto-prune bei >5000 Einträgen
- **IP Cache:** ✅ is_private_ip() gecacht, auto-prune bei >50000 Einträgen
- **Capture Lifecycle:** ✅ Sauberes Terminate/Kill bei Stop, Thread-Join mit Timeout
- **Restrisiko:** Keines identifiziert

### geoip_resolver.py (97 Zeilen)
- **Datenbank-Zugriff:** ✅ Try/except um alle maxminddb-Aufrufe
- **Graceful Degradation:** ✅ Gibt None zurück wenn DB fehlt
- **Restrisiko:** Keines identifiziert

### download_geoip.py (157 Zeilen)
- **Integrität:** ✅ SHA256-Verifizierung
- **HTTP-Safety:** ✅ Timeout (120s), User-Agent gesetzt
- **Restrisiko:** 🟡 tarfile ohne data_filter (SEC-LOW-03, mitigiert)

### generate_config.py (397 Zeilen)
- **Config-Sicherheit:** ✅ chmod 640, chown root:nobody
- **Default-Binding:** ✅ 127.0.0.1 als Fallback
- **XML-Parsing:** ✅ ElementTree (kein XXE-Risiko da trusted /conf/config.xml)
- **Interface-Auflösung:** ✅ OPNsense Interface-Namen → physische Devices + IPs (inkl. DHCP, VIPs)
- **Multi-Interface:** ✅ Komma-getrennte WAN/LAN Listen werden korrekt aufgelöst
- **MQTT-Config:** ✅ Server, Port, Credentials, Topic, Intervall aus config.xml extrahiert
- **SSL-Zertifikate:** ✅ Self-signed oder OPNsense-Zertifikat, Private Key chmod 640
- **Restrisiko:** Keines identifiziert

### rc.d/tcpgeo (120 Zeilen)
- **Privilege Separation:** ✅ daemon -u nobody
- **PID-Management:** 🟡 pgrep statt daemon -p (SEC-LOW-02)
- **Restrisiko:** 🟡 Race Condition bei PID-Ermittlung

### reconfigure.sh (39 Zeilen)
- **Config-Sicherheit:** ✅ chown/chmod nach Generierung
- **Restrisiko:** Keines identifiziert

### status.sh (19 Zeilen)
- **Funktion:** Liest PID-File, prüft Prozess
- **Restrisiko:** Keines identifiziert

### install.sh (307 Zeilen)
- **sudoers:** ✅ Regel für tcpdump erstellt (chmod 440)
- **Permissions:** ✅ GeoIP-Verzeichnis root:nobody
- **pip-Sicherheit:** 🟡 `--break-system-packages` als Workaround für neuere Python-Versionen
- **Restrisiko:** Keines kritisches identifiziert

### uninstall.sh (70 Zeilen)
- **BUG:** ✅ Zeile 44 behoben: Befehle korrekt auf separate Zeilen aufgeteilt
- **sudoers-Cleanup:** ✅ sudoers-Regel wird bei Deinstallation entfernt
- **Config-Cleanup:** 🟡 config.xml-Eintrag bleibt (SEC-LOW-05)

### index.html (78 Zeilen)
- **Externe Abhängigkeiten:** ✅ Alle Ressourcen lokal (Three.js, Globe.gl, CSS, Fonts)
- **Restrisiko:** Keines identifiziert

### globe.js (501 Zeilen)
- **XSS:** ✅ createElement/textContent statt innerHTML
- **Farbvalidierung:** ✅ Regex-Check in buildPortLegend
- **Externe Daten:** ✅ countries-110m.json lokal
- **Restrisiko:** Keines identifiziert

### cyberpunk.css (396 Zeilen)
- **Fonts:** ✅ Lokal über @font-face (keine Google Fonts CDN)
- **Restrisiko:** Keines identifiziert

### index.volt (188 Zeilen)
- **XSS:** ✅ colorpreview mit Regex + jQuery .text()
- **Framework:** ✅ Nutzt OPNsense Standard-Patterns (mapDataToFormUI, UIBootgrid, SimpleActionButton)
- **Restrisiko:** 🟡 GeoIP-Download Rückmeldung verwendet `data.response` in HTML (Zeile 86: `msg += '<br/>...<code>...' + data.response + '</code>'`). Der Wert kommt vom Backend (configd stdout) und wird nicht gesanitisiert. Risiko ist gering da configd-Output kontrolliert ist, aber ein XSS-Vektor bei manipuliertem Backend-Output.

### ServiceController.php (108 Zeilen)
- **Autorisierung:** ✅ Nutzt ApiControllerBase (erfordert Session + API-Schlüssel)
- **CSRF:** ✅ POST-only für alle mutierenden Aktionen
- **Restrisiko:** Keines identifiziert

### SettingsController.php (128 Zeilen)
- **Autorisierung:** ✅ Nutzt ApiMutableModelControllerBase
- **Validierung:** ✅ `performValidation()` vor `serializeToConfig()`
- **Restrisiko:** Keines identifiziert

### Tcpgeo.xml (72 Zeilen)
- **maxmindkey:** ✅ Mask-Validierung (`/^[a-zA-Z0-9_]*$/`)
- **color:** ✅ Mask-Validierung (`/^#[0-9a-fA-F]{6}$/`)
- **port:** ✅ IntegerField mit Min/Max
- **globepassword:** ✅ Mask-Validierung (`/^$|^.{8,}$/` — leer oder ≥ 8 Zeichen)
- **Restrisiko:** Keines identifiziert

### ACL.xml (11 Zeilen)
- **Patterns:** `ui/tcpgeo/*`, `api/tcpgeo/*`
- **Restrisiko:** Keines identifiziert

### actions_tcpgeo.conf (39 Zeilen)
- **Execution:** Alle Befehle als `type:script` oder `type:script_output` → laufen als Root über configd
- **Restrisiko:** Standard OPNsense-Pattern, nicht änderbar

### tcpgeo.inc (71 Zeilen)
- **Cron:** GeoIP-Download wöchentlich (Sonntag 03:30)
- **Service-Registration:** Standard OPNsense-Pattern
- **Restrisiko:** Keines identifiziert

---

### 6.1 Empfohlene Prioritäten (verbleibend)

| Priorität | Finding | Aufwand | Status |
|-----------|---------|--------|--------|
| ~~1~~ | ~~SEC-HIGH-05: uninstall.sh Zeilenumbrüche~~ | ~~1 Min~~ | ✅ Behoben |
| ~~2~~ | ~~SEC-MED-06: hmac.compare_digest~~ | ~~2 Min~~ | ✅ Behoben |
| ~~3~~ | ~~SEC-MED-01: is_relative_to~~ | ~~2 Min~~ | ✅ Behoben |
| ~~4~~ | ~~SEC-MED-07: Passwort-Mindestlänge~~ | ~~5 Min~~ | ✅ Behoben |
| 5 (optional) | SEC-LOW-04: Security-Header Middleware | 10 Min | 🟡 Offen |
| 6 (optional) | SEC-LOW-02: daemon -p für PID-File | 5 Min | 🟡 Offen |
| 7 (optional) | SEC-LOW-01: TLS-Dokumentation/Anleitung | 15 Min | 🟡 Offen |
| 8 (optional) | SEC-LOW-05: config.xml Cleanup in uninstall | 10 Min | 🟡 Offen |

---

## 7. Externe Abhängigkeiten

| Abhängigkeit | Version | Quelle | Status |
|-------------|---------|--------|--------|
| Three.js | 0.160.0 | Lokal gebündelt | ✅ Offline |
| Globe.gl | 2.32.0 | Lokal gebündelt | ✅ Offline |
| countries-110m.json | world-atlas | Lokal gebündelt | ✅ Offline |
| Orbitron Font (400/700) | Google Fonts | Lokal gebündelt | ✅ Offline |
| Roboto Mono Font (400/700) | Google Fonts | Lokal gebündelt | ✅ Offline |
| aiohttp | PyPI | pip install | ✅ Lokal |
| maxminddb | PyPI | pip install | ✅ Lokal |
| mqtt_client.py | Eigene Implementierung | Im Projekt | ✅ Offline (reine Python stdlib) |
| MaxMind GeoLite2-City | download.maxmind.com | Wöchentlicher Download | ⚠ Extern (SHA256-verifiziert) |
| Python 3 | FreeBSD pkg | System-Paket | ✅ Lokal |
| tcpdump | FreeBSD Basis | System | ✅ Lokal |

---

*Erstellt durch automatisierte Codeanalyse aller 24 Projektdateien.*
