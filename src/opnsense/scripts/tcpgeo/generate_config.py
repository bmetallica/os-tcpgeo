#!/usr/local/bin/python3
"""
TCPGeo OPNsense - Config Generator
Reads OPNsense config.xml and generates /usr/local/etc/tcpgeo/config.json
for the Python globe server.

Called by configd on reconfigure action.
"""

import xml.etree.ElementTree as ET
import json
import subprocess
import os
import sys

CONFIG_XML = '/conf/config.xml'
OUTPUT_DIR = '/usr/local/etc/tcpgeo'
OUTPUT_JSON = os.path.join(OUTPUT_DIR, 'config.json')


def get_interface_device(config_root, ifname):
    """Resolve OPNsense interface name (lan, wan, opt1) to physical device name"""
    iface = config_root.find(f'interfaces/{ifname}')
    if iface is None:
        return None
    return iface.findtext('if', None)


def get_interface_ips(config_root, ifname):
    """Get all IPs for an OPNsense interface (primary + VIPs)"""
    ips = []

    # Primary IP
    iface = config_root.find(f'interfaces/{ifname}')
    if iface is None:
        return ips

    device = iface.findtext('if', '')
    ipaddr = iface.findtext('ipaddr', '')

    if ipaddr and ipaddr != 'dhcp':
        ips.append(ipaddr)
    elif ipaddr == 'dhcp':
        # Get IP from ifconfig for DHCP interfaces
        try:
            output = subprocess.check_output(
                ['ifconfig', device], text=True, timeout=5
            )
            for line in output.split('\n'):
                line = line.strip()
                if line.startswith('inet ') and 'inet6' not in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        ips.append(parts[1])
        except (subprocess.SubprocessError, OSError):
            pass

    # Virtual IPs (aliases, CARP, etc.)
    virtualip = config_root.find('virtualip')
    if virtualip is not None:
        for vip in virtualip.findall('vip'):
            vip_if = vip.findtext('interface', '')
            if vip_if == ifname:
                subnet = vip.findtext('subnet', '')
                if subnet:
                    ips.append(subnet)

    return ips


def get_all_interface_ips(device):
    """Get all IPs from ifconfig for a physical device"""
    ips = []
    try:
        output = subprocess.check_output(
            ['ifconfig', device], text=True, timeout=5
        )
        for line in output.split('\n'):
            line = line.strip()
            if line.startswith('inet ') and 'inet6' not in line:
                parts = line.split()
                if len(parts) >= 2:
                    ips.append(parts[1])
    except (subprocess.SubprocessError, OSError):
        pass
    return ips


def main():
    if not os.path.exists(CONFIG_XML):
        print(f"[tcpgeo-config] Config nicht gefunden: {CONFIG_XML}", file=sys.stderr)
        sys.exit(1)

    try:
        tree = ET.parse(CONFIG_XML)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"[tcpgeo-config] XML Parse-Fehler: {e}", file=sys.stderr)
        sys.exit(1)

    # Find TCPGeo settings
    tcpgeo = root.find('OPNsense/tcpgeo')
    if tcpgeo is None:
        print("[tcpgeo-config] Keine TCPGeo-Konfiguration gefunden, erstelle Defaults")
        config = {
            'enabled': False,
            'listenAddress': '127.0.0.1',
            'listenPort': 3333,
            'captureDevice': 'em0',
            'captureIPs': [],
            'localLat': 50.0,
            'localLon': 10.0,
            'maxmindKey': '',
            'portColors': {},
            'globePassword': '',
            'maskIPs': True
        }
    else:
        general = tcpgeo.find('general')
        if general is None:
            print("[tcpgeo-config] Keine general-Sektion gefunden")
            sys.exit(1)

        enabled = general.findtext('enabled', '0') == '1'
        listen_if = general.findtext('listeninterface', 'lan')
        listen_port = int(general.findtext('listenport', '3333'))
        capture_if = general.findtext('captureinterface', 'wan')
        maxmind_key = general.findtext('maxmindkey', '')
        globe_password = general.findtext('globepassword', '')
        mask_ips = general.findtext('maskips', '1') == '1'

        # Resolve interface names
        listen_device = get_interface_device(root, listen_if)
        capture_device = get_interface_device(root, capture_if)

        # Get IPs
        listen_ips = get_interface_ips(root, listen_if)
        capture_ips = get_interface_ips(root, capture_if)

        # Also get IPs directly from ifconfig for the capture device
        if capture_device:
            ifconfig_ips = get_all_interface_ips(capture_device)
            for ip in ifconfig_ips:
                if ip not in capture_ips:
                    capture_ips.append(ip)

        # Listen address: use first IP of the listen interface
        listen_address = listen_ips[0] if listen_ips else '127.0.0.1'

        # Port-color mappings
        port_colors = {}
        portcolors_el = tcpgeo.find('portcolors')
        if portcolors_el is not None:
            for pc in portcolors_el.findall('portcolor'):
                pc_enabled = pc.findtext('enabled', '1')
                if pc_enabled != '1':
                    continue
                port = pc.findtext('port', '')
                color = pc.findtext('color', '#00ffff')
                label = pc.findtext('label', '')
                if port:
                    port_colors[port] = {
                        'color': color,
                        'label': label
                    }

        config = {
            'enabled': enabled,
            'listenAddress': listen_address,
            'listenPort': listen_port,
            'captureDevice': capture_device or 'em0',
            'captureIPs': capture_ips,
            'localLat': 50.0,
            'localLon': 10.0,
            'maxmindKey': maxmind_key,
            'portColors': port_colors,
            'globePassword': globe_password,
            'maskIPs': mask_ips
        }

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Write config
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(config, f, indent=2)

    # Secure config file (contains passwords and API keys)
    try:
        import pwd
        nobody = pwd.getpwnam('nobody')
        os.chown(OUTPUT_JSON, 0, nobody.pw_gid)
        os.chmod(OUTPUT_JSON, 0o640)
    except (KeyError, OSError):
        os.chmod(OUTPUT_JSON, 0o644)

    print(f"[tcpgeo-config] Konfiguration geschrieben: {OUTPUT_JSON}")
    print(f"[tcpgeo-config] Enabled: {config['enabled']}")
    print(f"[tcpgeo-config] Listen: {config['listenAddress']}:{config['listenPort']}")
    print(f"[tcpgeo-config] Capture: {config['captureDevice']} (IPs: {', '.join(config['captureIPs'])})")
    print(f"[tcpgeo-config] Port-Farben: {len(config['portColors'])} Einträge")

    return 0


if __name__ == '__main__':
    sys.exit(main())
