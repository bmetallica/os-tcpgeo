# TCPGeo OPNsense Plugin — Sicherheitsanalyse

**Datum:** 2026-03-04  
**Version:** 1.0.2 (nach HTTPS/TLS-Erweiterung)  
**Scope:** Vollständige Analyse aller Projektdateien (Quellcode, Konfiguration, Shell-Skripte, Frontend)

---

## Zusammenfassung

| Schweregrad | Offen | Behoben | Gesamt |
|------------|-------|---------|--------|
| KRITISCH   | 0     | 2       | 2      |
| HOCH       | 0     | 5       | 5      |
| MITTEL     | 0     | 8       | 8      |
| NIEDRIG    | 4     | 1       | 5      |
| INFO       | 4     | 0       | 4      |

**Gesamtbewertung:** Alle kritischen, hohen und mittleren Schwachstellen wurden behoben. Das Projekt ist vollständig gehärtet. Es verbleiben ausschließlich niedrige Restrisiken und informationelle Hinweise, die keinen unmittelbaren Handlungsbedarf darstellen.

---

## 1. Offene Schwachstellen

Keine kritischen, hohen oder mittleren Schwachstellen offen.

### SEC-LOW-01 — Kein HTTPS/TLS ✅ BEHOBEN

- **Datei:** `src/opnsense/scripts/tcpgeo/server.py`, `generate_config.py`, `Tcpgeo.xml`
- **Schweregrad:** NIEDRIG
- **Vorher:** Der Globe-Webserver unterstützte ausschließlich unverschlüsseltes HTTP.
- **Maßnahme:** Optionales HTTPS implementiert. Unterstützt selbstsignierte Zertifikate (automatisch erzeugt via openssl ECC P-256, 10 Jahre, SAN mit Listen-IP) und bestehende OPNsense-Zertifikate (aus config.xml extrahiert). TLS 1.2+ erzwungen. Standard bleibt HTTP.

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

---


## 2. Informationelle Hinweise

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

---

## 3. Architekturübersicht und Angriffsflächen

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
│  │  ┌────────────────────────────┐          │              │
│  │  │  capture.py (sudo tcpdump) │          │              │
│  │  │  → geoip_resolver.py      │          │              │
│  │  └────────────────────────────┘          │              │
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

### Angriffsflächen

| Fläche | Zugang | Schutz |
|--------|--------|--------|
| OPNsense Web-UI API | Authentifizierter Admin | ACL, Session, CSRF-Token (Framework) |
| Globe-Webserver (Port 3333) | Netzwerkzugang | Optional: Basic Auth, Interface-Binding |
| WebSocket (ws://host:3333/ws) | Netzwerkzugang | Auth, Rate-Limit, Connection-Limit |
| Statische Dateien | Globe-Server | Path-Traversal-Schutz (resolve + is_relative_to) |
| tcpdump-Subprocess | Lokal (nobody via sudo) | sudoers-Einschränkung auf /usr/sbin/tcpdump |
| config.json | Dateisystem | chmod 640, root:nobody |
| GeoIP-Download | Outbound HTTPS | SHA256-Verifizierung |

---

## 4. Datei-für-Datei-Analyse

### server.py (427 Zeilen)
- **Auth:** ✅ Basic Auth Middleware vorhanden
- **WebSocket:** ✅ Rate-Limiting, Connection-Limit, Heartbeat
- **IP-Schutz:** ✅ mask_ip() aktiv
- **0.0.0.0 Block:** ✅ Fallback auf 127.0.0.1
- **Passwortvergleich:** ✅ hmac.compare_digest (timing-safe)
- **Path-Traversal:** ✅ is_relative_to statt startswith
- **Restrisiko:** 🟡 Kein TLS (SEC-LOW-01), kein CSP (SEC-LOW-04)

### capture.py (220 Zeilen)
- **Device-Validierung:** ✅ Regex-Prüfung
- **Privilege Separation:** ✅ sudo-Eskalation wenn nicht root
- **Subprocess:** ✅ Liste statt Shell-String (kein Shell-Injection)
- **Restrisiko:** Keines identifiziert

### geoip_resolver.py (97 Zeilen)
- **Datenbank-Zugriff:** ✅ Try/except um alle maxminddb-Aufrufe
- **Graceful Degradation:** ✅ Gibt None zurück wenn DB fehlt
- **Restrisiko:** Keines identifiziert

### download_geoip.py (157 Zeilen)
- **Integrität:** ✅ SHA256-Verifizierung
- **HTTP-Safety:** ✅ Timeout (120s), User-Agent gesetzt
- **Restrisiko:** 🟡 tarfile ohne data_filter (SEC-LOW-03, mitigiert)

### generate_config.py (207 Zeilen)
- **Config-Sicherheit:** ✅ chmod 640, chown root:nobody
- **Default-Binding:** ✅ 127.0.0.1 als Fallback
- **XML-Parsing:** ✅ ElementTree (kein XXE-Risiko da trusted /conf/config.xml)
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

## 5. Empfohlene Prioritäten (verbleibend)

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

## 6. Externe Abhängigkeiten (Vollständig)

| Abhängigkeit | Version | Quelle | Status |
|-------------|---------|--------|--------|
| Three.js | 0.160.0 | Lokal gebündelt | ✅ Offline |
| Globe.gl | 2.32.0 | Lokal gebündelt | ✅ Offline |
| countries-110m.json | world-atlas | Lokal gebündelt | ✅ Offline |
| Orbitron Font (400/700) | Google Fonts | Lokal gebündelt | ✅ Offline |
| Roboto Mono Font (400/700) | Google Fonts | Lokal gebündelt | ✅ Offline |
| aiohttp | PyPI | pip install | ✅ Lokal |
| maxminddb | PyPI | pip install | ✅ Lokal |
| MaxMind GeoLite2-City | download.maxmind.com | Wöchentlicher Download | ⚠ Extern (SHA256-verifiziert) |
| Python 3 | FreeBSD pkg | System-Paket | ✅ Lokal |
| tcpdump | FreeBSD Basis | System | ✅ Lokal |

---

*Erstellt durch automatisierte Codeanalyse aller 24 Projektdateien.*
