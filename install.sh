#!/bin/sh
#
# TCPGeo OPNsense Plugin - Installation Script
# Installs all components on an OPNsense system.
# Backend: Python 3 + aiohttp + maxminddb (no Node.js required)
#
# Usage:
#   sh install.sh
#   sh install.sh --with-geoip MAXMIND_LICENSE_KEY
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"

printf "\n"
printf "\033[0;36m╔══════════════════════════════════════════════════╗\033[0m\n"
printf "\033[0;36m║     TCPGeo OPNsense Plugin - Installation       ║\033[0m\n"
printf "\033[0;36m║     Live Traffic Globe Visualization             ║\033[0m\n"
printf "\033[0;36m╚══════════════════════════════════════════════════╝\033[0m\n"
printf "\n"

# ---- Pre-checks ----
if [ "$(id -u)" -ne 0 ]; then
    printf "\033[0;31m[ERROR] Dieses Skript muss als root ausgeführt werden.\033[0m\n"
    exit 1
fi

if [ ! -f /conf/config.xml ]; then
    printf "\033[1;33m[WARN] /conf/config.xml nicht gefunden.\033[0m\n"
    printf "\033[1;33m       Ist dies ein OPNsense-System?\033[0m\n"
    printf "\n"
    printf "Trotzdem fortfahren? (y/N): "
    read -r answer
    if [ "${answer}" != "y" ] && [ "${answer}" != "Y" ]; then
        printf "Installation abgebrochen.\n"
        exit 1
    fi
fi

# ---- Step 1: Check Python 3 ----
printf "\033[0;36m[1/7] Prüfe Python 3...\033[0m\n"
if command -v python3 >/dev/null 2>&1; then
    PY_VERSION=$(python3 --version 2>&1)
    printf "  \033[0;32m✓ %s gefunden\033[0m\n" "${PY_VERSION}"
else
    printf "\033[0;31m[ERROR] Python 3 nicht gefunden!\033[0m\n"
    printf "  Auf OPNsense sollte Python 3 vorinstalliert sein.\n"
    printf "  Versuche: pkg install python3\n"
    exit 1
fi

# Determine pip command - try multiple variants that exist on OPNsense
PIP_CMD=""
# OPNsense typically ships py311-pip or similar
for pip_try in "pip-3.11" "pip-3.9" "pip3" "pip"; do
    if command -v "${pip_try}" >/dev/null 2>&1; then
        PIP_CMD="${pip_try}"
        break
    fi
done

# If no pip binary found, try python3 -m pip
if [ -z "${PIP_CMD}" ]; then
    if python3 -m pip --version >/dev/null 2>&1; then
        PIP_CMD="python3 -m pip"
    fi
fi

# Still no pip? Try to install it
if [ -z "${PIP_CMD}" ]; then
    printf "  pip nicht gefunden, versuche Installation...\n"
    # Find installed Python version for pkg name
    PY_VER=$(python3 -c "import sys; print(f'py{sys.version_info.major}{sys.version_info.minor}')" 2>/dev/null || echo "py311")
    pkg install -y "${PY_VER}-pip" 2>/dev/null || true

    for pip_try in "pip-3.11" "pip-3.9" "pip3" "pip"; do
        if command -v "${pip_try}" >/dev/null 2>&1; then
            PIP_CMD="${pip_try}"
            break
        fi
    done
    if [ -z "${PIP_CMD}" ] && python3 -m pip --version >/dev/null 2>&1; then
        PIP_CMD="python3 -m pip"
    fi
fi

if [ -z "${PIP_CMD}" ]; then
    printf "\033[0;31m[ERROR] pip konnte nicht gefunden/installiert werden.\033[0m\n"
    PY_VER=$(python3 -c "import sys; print(f'py{sys.version_info.major}{sys.version_info.minor}')" 2>/dev/null || echo "py311")
    printf "  Bitte manuell installieren: pkg install %s-pip\n" "${PY_VER}"
    exit 1
fi

printf "  pip: %s\n" "${PIP_CMD}"

# ---- Step 2: Create directories ----
printf "\033[0;36m[2/7] Erstelle Verzeichnisse...\033[0m\n"
mkdir -p /usr/local/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/Api
mkdir -p /usr/local/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/forms
mkdir -p /usr/local/opnsense/mvc/app/models/OPNsense/Tcpgeo/Menu
mkdir -p /usr/local/opnsense/mvc/app/models/OPNsense/Tcpgeo/ACL
mkdir -p /usr/local/opnsense/mvc/app/views/OPNsense/Tcpgeo
mkdir -p /usr/local/opnsense/service/conf/actions.d
mkdir -p /usr/local/opnsense/scripts/tcpgeo/frontend
mkdir -p /usr/local/opnsense/scripts/tcpgeo/frontend/fonts
mkdir -p /usr/local/opnsense/scripts/tcpgeo/geoip
mkdir -p /usr/local/etc/inc/plugins.inc.d
mkdir -p /usr/local/etc/tcpgeo
printf "  \033[0;32m✓ Verzeichnisse erstellt\033[0m\n"

# ---- Step 3: Copy MVC files ----
printf "\033[0;36m[3/7] Kopiere OPNsense MVC-Dateien...\033[0m\n"

cp "${SRC_DIR}/opnsense/mvc/app/models/OPNsense/Tcpgeo/Tcpgeo.xml" \
   /usr/local/opnsense/mvc/app/models/OPNsense/Tcpgeo/
cp "${SRC_DIR}/opnsense/mvc/app/models/OPNsense/Tcpgeo/Tcpgeo.php" \
   /usr/local/opnsense/mvc/app/models/OPNsense/Tcpgeo/
cp "${SRC_DIR}/opnsense/mvc/app/models/OPNsense/Tcpgeo/Menu/Menu.xml" \
   /usr/local/opnsense/mvc/app/models/OPNsense/Tcpgeo/Menu/
cp "${SRC_DIR}/opnsense/mvc/app/models/OPNsense/Tcpgeo/ACL/ACL.xml" \
   /usr/local/opnsense/mvc/app/models/OPNsense/Tcpgeo/ACL/

cp "${SRC_DIR}/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/IndexController.php" \
   /usr/local/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/
cp "${SRC_DIR}/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/Api/SettingsController.php" \
   /usr/local/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/Api/
cp "${SRC_DIR}/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/Api/ServiceController.php" \
   /usr/local/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/Api/

cp "${SRC_DIR}/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/forms/general.xml" \
   /usr/local/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/forms/
cp "${SRC_DIR}/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/forms/dialogPortcolor.xml" \
   /usr/local/opnsense/mvc/app/controllers/OPNsense/Tcpgeo/forms/

cp "${SRC_DIR}/opnsense/mvc/app/views/OPNsense/Tcpgeo/index.volt" \
   /usr/local/opnsense/mvc/app/views/OPNsense/Tcpgeo/

printf "  \033[0;32m✓ MVC-Dateien kopiert\033[0m\n"

# ---- Step 4: Copy Service Integration ----
printf "\033[0;36m[4/7] Kopiere Service-Integration...\033[0m\n"

cp "${SRC_DIR}/opnsense/service/conf/actions.d/actions_tcpgeo.conf" \
   /usr/local/opnsense/service/conf/actions.d/

cp "${SRC_DIR}/etc/inc/plugins.inc.d/tcpgeo.inc" \
   /usr/local/etc/inc/plugins.inc.d/

cp "${SRC_DIR}/etc/rc.d/tcpgeo" \
   /usr/local/etc/rc.d/
chmod 755 /usr/local/etc/rc.d/tcpgeo

printf "  \033[0;32m✓ Service-Integration installiert\033[0m\n"

# ---- Step 5: Copy Python Application ----
printf "\033[0;36m[5/7] Kopiere Python-Anwendung...\033[0m\n"

cp "${SRC_DIR}/opnsense/scripts/tcpgeo/server.py" \
   /usr/local/opnsense/scripts/tcpgeo/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/capture.py" \
   /usr/local/opnsense/scripts/tcpgeo/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/geoip_resolver.py" \
   /usr/local/opnsense/scripts/tcpgeo/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/download_geoip.py" \
   /usr/local/opnsense/scripts/tcpgeo/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/generate_config.py" \
   /usr/local/opnsense/scripts/tcpgeo/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/mqtt_client.py" \
   /usr/local/opnsense/scripts/tcpgeo/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/reconfigure.sh" \
   /usr/local/opnsense/scripts/tcpgeo/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/status.sh" \
   /usr/local/opnsense/scripts/tcpgeo/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/requirements.txt" \
   /usr/local/opnsense/scripts/tcpgeo/

# Frontend
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/frontend/index.html" \
   /usr/local/opnsense/scripts/tcpgeo/frontend/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/frontend/globe.js" \
   /usr/local/opnsense/scripts/tcpgeo/frontend/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/frontend/cyberpunk.css" \
   /usr/local/opnsense/scripts/tcpgeo/frontend/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/frontend/three.min.js" \
   /usr/local/opnsense/scripts/tcpgeo/frontend/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/frontend/globe.gl.min.js" \
   /usr/local/opnsense/scripts/tcpgeo/frontend/
cp "${SRC_DIR}/opnsense/scripts/tcpgeo/frontend/countries-110m.json" \
   /usr/local/opnsense/scripts/tcpgeo/frontend/
cp -R "${SRC_DIR}/opnsense/scripts/tcpgeo/frontend/fonts/" \
   /usr/local/opnsense/scripts/tcpgeo/frontend/fonts/

# Permissions
chmod 755 /usr/local/opnsense/scripts/tcpgeo/server.py
chmod 755 /usr/local/opnsense/scripts/tcpgeo/download_geoip.py
chmod 755 /usr/local/opnsense/scripts/tcpgeo/reconfigure.sh
chmod 755 /usr/local/opnsense/scripts/tcpgeo/status.sh

printf "  \033[0;32m✓ Python-Anwendung kopiert\033[0m\n"

# ---- Step 6: Install Python dependencies ----
printf "\033[0;36m[6/7] Installiere Python-Abhängigkeiten...\033[0m\n"

# Try with --break-system-packages first (needed on newer Python)
${PIP_CMD} install --break-system-packages \
    -r /usr/local/opnsense/scripts/tcpgeo/requirements.txt 2>&1 | tail -5
PIP_EXIT=$?

if [ ${PIP_EXIT} -ne 0 ]; then
    printf "  Versuche ohne --break-system-packages...\n"
    ${PIP_CMD} install -r /usr/local/opnsense/scripts/tcpgeo/requirements.txt 2>&1 | tail -5
fi

# Verify
VERIFY_OK=1
if python3 -c "import aiohttp" 2>/dev/null; then
    printf "  \033[0;32m✓ aiohttp verifiziert\033[0m\n"
else
    printf "\033[0;31m  ✗ aiohttp nicht importierbar!\033[0m\n"
    VERIFY_OK=0
fi

if python3 -c "import maxminddb" 2>/dev/null; then
    printf "  \033[0;32m✓ maxminddb verifiziert\033[0m\n"
else
    printf "\033[1;33m  ⚠ maxminddb nicht importierbar (GeoIP wird nicht funktionieren)\033[0m\n"
fi

if [ ${VERIFY_OK} -eq 0 ]; then
    printf "\033[0;31m[ERROR] Kritische Abhängigkeiten fehlen!\033[0m\n"
    printf "  Manuelle Installation: %s install aiohttp maxminddb\n" "${PIP_CMD}"
    exit 1
fi

# ---- Step 7: GeoIP + Finalize ----
printf "\033[0;36m[7/7] GeoIP-Datenbank & Finalisierung...\033[0m\n"

MAXMIND_KEY=""
if [ "$1" = "--with-geoip" ] && [ -n "$2" ]; then
    MAXMIND_KEY="$2"
fi

if [ -n "${MAXMIND_KEY}" ]; then
    printf "  Lade GeoIP-Datenbank herunter...\n"
    MAXMIND_LICENSE_KEY="${MAXMIND_KEY}" python3 /usr/local/opnsense/scripts/tcpgeo/download_geoip.py
    if [ -f /usr/local/opnsense/scripts/tcpgeo/geoip/GeoLite2-City.mmdb ]; then
        printf "  \033[0;32m✓ GeoIP-Datenbank heruntergeladen\033[0m\n"
    else
        printf "  \033[1;33m⚠ GeoIP-Download fehlgeschlagen.\033[0m\n"
    fi
else
    if [ -f /usr/local/opnsense/scripts/tcpgeo/geoip/GeoLite2-City.mmdb ]; then
        printf "  \033[0;32m✓ GeoIP-Datenbank bereits vorhanden\033[0m\n"
    else
        printf "  \033[1;33m⚠ Keine GeoIP-Datenbank vorhanden.\033[0m\n"
        printf "  \033[1;33m  Bitte MaxMind License Key in OPNsense UI konfigurieren.\033[0m\n"
        printf "  \033[1;33m  Oder: sh install.sh --with-geoip YOUR_LICENSE_KEY\033[0m\n"
    fi
fi

# Copy uninstall script
cp "${SCRIPT_DIR}/uninstall.sh" /usr/local/opnsense/scripts/tcpgeo/uninstall.sh 2>/dev/null || true
chmod 755 /usr/local/opnsense/scripts/tcpgeo/uninstall.sh 2>/dev/null || true

# Security: Create sudoers rules for tcpdump + pfctl (service runs as nobody)
printf "  Erstelle sudoers-Regeln für tcpdump und pfctl...\n"
mkdir -p /usr/local/etc/sudoers.d
cat > /usr/local/etc/sudoers.d/tcpgeo <<'EOF'
nobody ALL=(root) NOPASSWD: /usr/sbin/tcpdump
nobody ALL=(root) NOPASSWD: /sbin/pfctl
EOF
chmod 440 /usr/local/etc/sudoers.d/tcpgeo
printf "  \033[0;32m✓ sudoers-Regeln erstellt\033[0m\n"

# Fix permissions for service user (nobody)
chown root:nobody /usr/local/etc/tcpgeo 2>/dev/null || true
chmod 750 /usr/local/etc/tcpgeo 2>/dev/null || true
chown -R root:nobody /usr/local/opnsense/scripts/tcpgeo/geoip 2>/dev/null || true
chmod -R g+r /usr/local/opnsense/scripts/tcpgeo/geoip 2>/dev/null || true

# Restart configd to pick up new actions
if service configd status >/dev/null 2>&1; then
    service configd restart
    printf "  \033[0;32m✓ configd neugestartet\033[0m\n"
fi

# Generate initial config
python3 /usr/local/opnsense/scripts/tcpgeo/generate_config.py 2>/dev/null || true

printf "  \033[0;32m✓ Installation abgeschlossen\033[0m\n"

printf "\n"
printf "\033[0;32m╔══════════════════════════════════════════════════╗\033[0m\n"
printf "\033[0;32m║     TCPGeo Installation erfolgreich!             ║\033[0m\n"
printf "\033[0;32m╚══════════════════════════════════════════════════╝\033[0m\n"
printf "\n"
printf "  Nächste Schritte:\n"
printf "  1. OPNsense Web-UI öffnen\n"
printf "  2. Services → TCPGeo → Einstellungen\n"
printf "  3. Schnittstellen und Port konfigurieren\n"
printf "  4. MaxMind License Key eintragen (für GeoIP)\n"
printf "  5. Port-Farben zuordnen\n"
printf "  6. 'Speichern & Anwenden' klicken\n"
printf "\n"
printf "  Der Globus ist dann erreichbar unter:\n"
printf "  http://<LAN-IP>:<PORT>\n"
printf "\n"
printf "  Deinstallation:\n"
printf "  sh /usr/local/opnsense/scripts/tcpgeo/uninstall.sh\n"
printf "\n"
