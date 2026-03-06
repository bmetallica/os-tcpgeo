#!/usr/local/bin/python3
"""
TCPGeo OPNsense - GeoIP Database Download (Python stdlib only)
Downloads MaxMind GeoLite2-City database.
Reads license key from config.json or environment variable.

Usage:
    python3 download_geoip.py
    MAXMIND_LICENSE_KEY=xxx python3 download_geoip.py
"""

import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get('TCPGEO_CONFIG', '/usr/local/etc/tcpgeo/config.json')
GEOIP_DIR = os.path.join(SCRIPT_DIR, 'geoip')
DB_FILE = os.path.join(GEOIP_DIR, 'GeoLite2-City.mmdb')


def get_license_key():
    """Get MaxMind license key from environment or config file"""
    # 1. Environment variable
    key = os.environ.get('MAXMIND_LICENSE_KEY', '')
    if key:
        return key

    # 2. Config file
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
        key = config.get('maxmindKey', '')
        if key:
            return key
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return None


def download_geoip():
    """Download and extract GeoLite2-City.mmdb from MaxMind"""
    license_key = get_license_key()
    if not license_key:
        print('[geoip] Kein MaxMind License Key gefunden!', file=sys.stderr)
        print('[geoip] Bitte in OPNsense unter Services → TCPGeo konfigurieren.',
              file=sys.stderr)
        sys.exit(1)

    # Ensure geoip directory exists
    os.makedirs(GEOIP_DIR, exist_ok=True)

    url = (
        f'https://download.maxmind.com/app/geoip_download'
        f'?edition_id=GeoLite2-City&license_key={license_key}&suffix=tar.gz'
    )

    print('[geoip] Lade GeoLite2-City Datenbank...')

    try:
        # Download with redirect following
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'tcpgeo-opnsense/1.0')

        with urllib.request.urlopen(req, timeout=120) as response:
            if response.status != 200:
                print(f'[geoip] Download fehlgeschlagen: HTTP {response.status}',
                      file=sys.stderr)
                sys.exit(1)

            data = response.read()
            print(f'[geoip] {len(data) / (1024*1024):.1f} MB heruntergeladen')

    except urllib.error.HTTPError as e:
        print(f'[geoip] Download fehlgeschlagen: HTTP {e.code} - {e.reason}',
              file=sys.stderr)
        if e.code == 401:
            print('[geoip] License Key ungültig oder abgelaufen.', file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f'[geoip] Netzwerk-Fehler: {e.reason}', file=sys.stderr)
        sys.exit(1)

    # Verify SHA256 checksum
    print('[geoip] Verifiziere SHA256-Pr\u00fcfsumme...')
    sha_url = (
        f'https://download.maxmind.com/app/geoip_download'
        f'?edition_id=GeoLite2-City&license_key={license_key}&suffix=tar.gz.sha256'
    )
    try:
        sha_req = urllib.request.Request(sha_url)
        sha_req.add_header('User-Agent', 'tcpgeo-opnsense/1.0')
        with urllib.request.urlopen(sha_req, timeout=30) as sha_resp:
            expected_hash = sha_resp.read().decode('utf-8').strip().split()[0]
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != expected_hash:
            print(f'[geoip] SHA256-Pr\u00fcfsumme stimmt NICHT \u00fcberein!', file=sys.stderr)
            print(f'[geoip]   Erwartet: {expected_hash}', file=sys.stderr)
            print(f'[geoip]   Erhalten: {actual_hash}', file=sys.stderr)
            sys.exit(1)
        print(f'[geoip] SHA256 OK: {actual_hash[:16]}...')
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f'[geoip] SHA256-Pr\u00fcfung nicht m\u00f6glich: {e}', file=sys.stderr)
        print('[geoip] WARNUNG: Fahre ohne Pr\u00fcfsummenverifikation fort.')

    # Extract .mmdb from tar.gz
    found = False
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            for member in tar.getmembers():
                if member.name.endswith('.mmdb'):
                    print(f'[geoip] Entpacke: {member.name}')
                    f = tar.extractfile(member)
                    if f:
                        with open(DB_FILE, 'wb') as out:
                            out.write(f.read())
                        found = True
                        break
    except (tarfile.TarError, gzip.BadGzipFile) as e:
        print(f'[geoip] Entpack-Fehler: {e}', file=sys.stderr)
        sys.exit(1)

    if not found:
        print('[geoip] Keine .mmdb Datei im Archiv gefunden', file=sys.stderr)
        sys.exit(1)

    size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
    print(f'[geoip] Datenbank bereit: {DB_FILE} ({size_mb:.1f} MB)')


if __name__ == '__main__':
    try:
        download_geoip()
        print('[geoip] Download abgeschlossen.')
    except KeyboardInterrupt:
        print('\n[geoip] Abgebrochen.')
        sys.exit(1)
    except Exception as e:
        print(f'[geoip] Fehler: {e}', file=sys.stderr)
        sys.exit(1)
