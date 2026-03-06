#!/bin/sh
#
# TCPGeo OPNsense - Status Script
# Returns the running status of the TCPGeo service.
# Output format expected by configd: "tcpgeo is running" or "tcpgeo is not running"
#

PIDFILE="/var/run/tcpgeo.pid"

if [ -f "${PIDFILE}" ]; then
    pid=$(cat "${PIDFILE}")
    if kill -0 "${pid}" 2>/dev/null; then
        echo "tcpgeo is running as pid ${pid}"
        exit 0
    fi
fi

echo "tcpgeo is not running"
exit 1
