#!/bin/sh
#
# TCPGeo OPNsense Plugin - Uninstall Script
# Removes all TCPGeo components from OPNsense.
#
set -e

printf "\n"
printf "\033[0;36m╔══════════════════════════════════════════════════╗\033[0m\n"
printf "\033[0;36m║     TCPGeo OPNsense Plugin - Deinstallation     ║\033[0m\n"
printf "\033[0;36m╚══════════════════════════════════════════════════╝\033[0m\n"
printf "\n"

if [ "$(id -u)" -ne 0 ]; then
    printf "\033[0;31m[ERROR] Dieses Skript muss als root ausgeführt werden.\033[0m\n"
    exit 1
fi

printf "TCPGeo wirklich deinstallieren? (y/N): "
read -r answer
if [ "${answer}" != "y" ] && [ "${answer}" != "Y" ]; then
    printf "Deinstallation abgebrochen.\n"
    exit 0
fi

# ---- Stop Service ----
printf "\033[0;36m[1/5] Stoppe Dienst...\033[0m\n"
/usr/local/etc/rc.d/tcpgeo stop 2>/dev/null || true
sysrc -x tcpgeo_enable 2>/dev/null || true
pkill -f "python3.*tcpgeo/server.py" 2>/dev/null || true
printf "  \033[0;32m✓ Dienst gestoppt\033[0m\n"

# ---- Remove MVC Files ----
printf "\033[0;36m[2/5] Entferne MVC-Dateien...\033[0m\n"
rm -rf /usr/local/opnsense/mvc/app/controllers/OPNsense/Tcpgeo
rm -rf /usr/local/opnsense/mvc/app/models/OPNsense/Tcpgeo
rm -rf /usr/local/opnsense/mvc/app/views/OPNsense/Tcpgeo
printf "  \033[0;32m✓ MVC-Dateien entfernt\033[0m\n"

# ---- Remove Service Integration ----
printf "\033[0;36m[3/5] Entferne Service-Integration...\033[0m\n"
rm -f /usr/local/opnsense/service/conf/actions.d/actions_tcpgeo.conf
rm -f /usr/local/etc/inc/plugins.inc.d/tcpgeo.inc
rm -f /usr/local/etc/rc.d/tcpgeo
rm -f /usr/local/etc/sudoers.d/tcpgeo
printf "  \033[0;32m✓ Service-Integration entfernt\033[0m\n"

# ---- Remove Scripts & Data ----
printf "\033[0;36m[4/5] Entferne Skripte und Daten...\033[0m\n"
rm -rf /usr/local/opnsense/scripts/tcpgeo
rm -rf /usr/local/etc/tcpgeo
rm -f /var/run/tcpgeo.pid
rm -f /var/log/tcpgeo.log
printf "  \033[0;32m✓ Skripte und Daten entfernt\033[0m\n"

# ---- Restart configd ----
printf "\033[0;36m[5/5] Finalisiere...\033[0m\n"
if service configd status >/dev/null 2>&1; then
    service configd restart
    printf "  \033[0;32m✓ configd neugestartet\033[0m\n"
fi

printf "\n"
printf "\033[0;32m╔══════════════════════════════════════════════════╗\033[0m\n"
printf "\033[0;32m║     TCPGeo wurde erfolgreich deinstalliert.      ║\033[0m\n"
printf "\033[0;32m╚══════════════════════════════════════════════════╝\033[0m\n"
printf "\n"
printf "  \033[1;33mHinweis: Die TCPGeo-Konfiguration in config.xml\033[0m\n"
printf "  \033[1;33mwurde nicht entfernt. Sie wird beim nächsten\033[0m\n"
printf "  \033[1;33mFirmware-Update automatisch bereinigt.\033[0m\n"
printf "\n"
