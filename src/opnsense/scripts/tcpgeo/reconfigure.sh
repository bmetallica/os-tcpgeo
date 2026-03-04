#!/bin/sh
#
# TCPGeo - Reconfigure script
# Called by configd when user clicks "Save & Apply"
# Regenerates config and restarts the service.
#

SCRIPT_DIR="/usr/local/opnsense/scripts/tcpgeo"

# 1. Regenerate config.json from OPNsense model
if [ -f "${SCRIPT_DIR}/generate_config.py" ]; then
    /usr/local/bin/python3 "${SCRIPT_DIR}/generate_config.py"
    # Secure config file for service user
    chown root:nobody /usr/local/etc/tcpgeo/config.json 2>/dev/null || true
    chmod 640 /usr/local/etc/tcpgeo/config.json 2>/dev/null || true
fi

# 2. Check if service is enabled
ENABLED=$(grep -c '"enabled":.*true\|"enabled":.*1' /usr/local/etc/tcpgeo/config.json 2>/dev/null || echo "0")

if [ "${ENABLED}" -gt 0 ]; then
    # Enable and restart
    sysrc tcpgeo_enable="YES" >/dev/null 2>&1
    if /usr/local/etc/rc.d/tcpgeo status >/dev/null 2>&1; then
        /usr/local/etc/rc.d/tcpgeo restart
    else
        /usr/local/etc/rc.d/tcpgeo start
    fi
    echo "TCPGeo reconfigured and started."
else
    # Disable and stop
    /usr/local/etc/rc.d/tcpgeo stop 2>/dev/null || true
    sysrc tcpgeo_enable="NO" >/dev/null 2>&1
    echo "TCPGeo disabled and stopped."
fi
