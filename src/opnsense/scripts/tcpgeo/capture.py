"""
TCPGeo OPNsense — Connection Tracker

Multi-interface tcpdump capture with SYN-only BPF kernel filter.
Runs one lightweight tcpdump per interface (WAN + LAN), each with a BPF
filter that matches ONLY TCP SYN packets and selected UDP traffic.

Performance design:
  - **BPF kernel filter** on each interface → 99.9 % of packets never leave
    the kernel.  Python sees ~5–50 events/sec instead of 10 000+.
  - **Multi-interface**: WAN for incoming, LAN for outgoing + client IPs.
  - **UDP dedup**: Short-lived set with TTL for DNS/QUIC flows.
  - **No subprocess polling**: tcpdump streams continuously (line-buffered).

Typical CPU: < 1 % total across all tcpdump instances + Python parsing.
"""

import subprocess
import re
import os
import threading
import time
import logging

log = logging.getLogger('tcpgeo.capture')

# ---- BPF filters (compiled in kernel — near zero overhead) ----
# TCP SYN only (no SYN-ACK): catches the first packet of every new connection
_BPF_TCP_SYN = '(tcp[tcpflags] & (tcp-syn) != 0 and tcp[tcpflags] & (tcp-ack) = 0)'
# UDP to well-known ports (DNS, QUIC/HTTP3, NTP, STUN, WireGuard)
_BPF_UDP_PORTS = '(udp and (dst port 53 or dst port 443 or dst port 123 or dst port 3478 or dst port 51820))'
# Combined filter — only IP, no broadcast/multicast
BPF_FILTER = 'ip and ({} or {}) and not broadcast and not multicast'.format(
    _BPF_TCP_SYN, _BPF_UDP_PORTS)

# Regex for tcpdump -nn output:
# "12:34:56.789 IP 1.2.3.4.443 > 5.6.7.8.12345: Flags [S], ..."
PACKET_RE = re.compile(
    r'IP\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d+)\s+>\s+'
    r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d+):'
)

# Private/reserved IP ranges (fast integer check)
_PRIVATE_NETS = [
    (0x0A000000, 0xFF000000),   # 10.0.0.0/8
    (0xAC100000, 0xFFF00000),   # 172.16.0.0/12
    (0xC0A80000, 0xFFFF0000),   # 192.168.0.0/16
    (0x7F000000, 0xFF000000),   # 127.0.0.0/8
    (0x00000000, 0xFF000000),   # 0.0.0.0/8
    (0xA9FE0000, 0xFFFF0000),   # 169.254.0.0/16
    (0xE0000000, 0xF0000000),   # 224.0.0.0/4 (multicast)
    (0xFF000000, 0xFF000000),   # 255.0.0.0/8
]

# Cache for is_private_ip lookups
_priv_cache = {}


def _ip_to_int(ip):
    """Convert dotted-quad IP to 32-bit integer."""
    a, b, c, d = ip.split('.')
    return (int(a) << 24) | (int(b) << 16) | (int(c) << 8) | int(d)


def is_private_ip(ip):
    """Check if IP is in a private/reserved range (integer math, cached)."""
    r = _priv_cache.get(ip)
    if r is not None:
        return r
    try:
        n = _ip_to_int(ip)
    except (ValueError, AttributeError):
        _priv_cache[ip] = False
        return False
    result = any((n & mask) == net for net, mask in _PRIVATE_NETS)
    _priv_cache[ip] = result
    # Prevent unbounded cache growth
    if len(_priv_cache) > 50000:
        _priv_cache.clear()
    return result


# UDP dedup TTL in seconds
_UDP_DEDUP_TTL = 30.0

# ---- Byte-count enrichment via pfctl state table ----
# Interval between pfctl polls (seconds).  At 15 s the CPU cost is
# negligible (~1 s of work every 15 s ≈ 0.07 % average).
ENRICH_INTERVAL = 30.0

# Regex for "X:Y bytes" in pfctl -v stats line
_BYTES_RE = re.compile(r'(\d+):(\d+)\s+bytes')

_PROTO_OK = frozenset(('tcp', 'udp'))


class ConnectionTracker:
    """
    Multi-interface connection tracker using tcpdump with SYN-only BPF filter.

    Spawns one tcpdump per configured interface (WAN + LAN), parsing only
    TCP SYN packets and selected UDP traffic.  Python sees ~5–50 events/sec
    instead of 10 000+, because 99.9 % of packets are dropped by the kernel
    BPF filter before they ever reach userspace.

    Same callback interface as before:
        on_packet({'srcIP', 'srcPort', 'dstIP', 'dstPort',
                   'remoteIP', 'localIP', 'direction', 'servicePort'})
        on_status(status_string)
        on_error(error_string)
    """

    def __init__(self, wan_devices=None, lan_devices=None,
                 wan_ips=None, lan_ips=None,
                 on_packet=None, on_status=None, on_error=None):
        self.wan_devices = list(wan_devices or [])
        self.lan_devices = list(lan_devices or [])
        self.wan_ips = set(wan_ips or [])
        self.lan_ips = set(lan_ips or [])
        # All known local IPs (for direction detection)
        self.all_local_ips = self.wan_ips | self.lan_ips
        self.on_packet = on_packet
        self.on_status = on_status
        self.on_error = on_error
        self._running = False
        self._processes = {}   # device → Popen
        self._threads = {}     # device → Thread
        self._stderr_threads = {}
        # UDP dedup: (proto, remote_ip, remote_port) → expire_time
        self._udp_seen = {}
        self._lock = threading.Lock()
        self._any_capturing = False
        self._enrich_thread = None

    def start(self):
        """Start tcpdump on all configured interfaces."""
        if self._running:
            return
        self._running = True
        self._any_capturing = False

        all_devices = []
        for dev in self.wan_devices:
            all_devices.append((dev, False))
        for dev in self.lan_devices:
            all_devices.append((dev, True))

        if not all_devices:
            log.error('Keine Interfaces konfiguriert (WAN + LAN beide leer)')
            if self.on_error:
                self.on_error('Keine Interfaces konfiguriert')
            self._running = False
            return

        log.info('ConnectionTracker: WAN=%s LAN=%s',
                 ','.join(self.wan_devices) or '(keine)',
                 ','.join(self.lan_devices) or '(keine)')
        log.info('BPF-Filter: %s', BPF_FILTER)

        for dev, is_lan in all_devices:
            t = threading.Thread(
                target=self._capture_loop,
                args=(dev, is_lan),
                daemon=True,
                name='tcpdump-{}'.format(dev)
            )
            self._threads[dev] = t
            t.start()

        # Byte enrichment: periodically poll pfctl state table
        self._enrich_thread = threading.Thread(
            target=self._enrich_loop,
            daemon=True,
            name='pfctl-enrich'
        )
        self._enrich_thread.start()

    def _capture_loop(self, device, is_lan):
        """Run tcpdump on one interface and parse output."""
        role = 'LAN' if is_lan else 'WAN'

        # Validate device name
        if not all(c.isalnum() or c in '._' for c in device):
            log.error('[%s] Ungültiger Interface-Name: %s', role, device)
            if self.on_error:
                self.on_error('Ungültiger Interface-Name: {}'.format(device))
            return

        args = [
            '/usr/sbin/tcpdump',
            '-l', '-nn', '-q',
            '-i', device,
            BPF_FILTER
        ]
        if os.getuid() != 0:
            args = ['/usr/local/bin/sudo', '-n'] + args

        log.info('[%s/%s] Starte tcpdump: %s', role, device,
                 ' '.join(args[-2:]))  # just show the filter

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                universal_newlines=True
            )
        except (OSError, FileNotFoundError) as e:
            log.error('[%s/%s] tcpdump Startfehler: %s', role, device, e)
            if self.on_error:
                self.on_error(str(e))
            return

        self._processes[device] = proc

        # stderr reader
        st = threading.Thread(
            target=self._read_stderr,
            args=(proc, device, role),
            daemon=True
        )
        self._stderr_threads[device] = st
        st.start()

        # Main parse loop
        try:
            for line in proc.stdout:
                if not self._running:
                    break
                line = line.strip()
                if line:
                    self._parse_line(line, device, is_lan)
        except Exception as e:
            if self._running:
                log.error('[%s/%s] Lese-Fehler: %s', role, device, e)

        retcode = proc.wait() if proc else -1
        log.info('[%s/%s] tcpdump beendet (code=%s)', role, device, retcode)

    def _read_stderr(self, proc, device, role):
        """Read tcpdump stderr for status messages."""
        if not proc or not proc.stderr:
            return
        try:
            for line in proc.stderr:
                line = line.strip()
                if not line:
                    continue
                if 'listening on' in line:
                    log.info('[%s/%s] %s', role, device, line)
                    if not self._any_capturing:
                        self._any_capturing = True
                        if self.on_status:
                            self.on_status('capturing')
                elif 'packets' in line:
                    log.debug('[%s/%s] %s', role, device, line)
                elif re.search(
                        r'permission|error|no suitable|can.*open|unknown|No such',
                        line, re.I):
                    log.error('[%s/%s] tcpdump: %s', role, device, line)
                    if self.on_error:
                        self.on_error('{}/{}: {}'.format(role, device, line))
        except Exception:
            pass

    def _parse_line(self, line, device, is_lan):
        """Parse tcpdump output and emit new connection events."""
        m = PACKET_RE.search(line)
        if not m:
            return

        src_ip = m.group(1)
        src_port = int(m.group(2))
        dst_ip = m.group(3)
        dst_port = int(m.group(4))

        # Determine if this is TCP SYN or UDP
        # (BPF already filtered, but we still check for UDP dedup)
        is_udp = ' UDP' in line or '.53:' in line

        # ── UDP dedup ──
        if is_udp:
            # For UDP we might see many packets to the same remote.
            # Dedup by (remote_ip, dst_port) with a TTL.
            remote = dst_ip if not is_private_ip(dst_ip) else src_ip
            dedup_key = (remote, dst_port)
            now = time.monotonic()
            with self._lock:
                exp = self._udp_seen.get(dedup_key)
                if exp is not None and now < exp:
                    return  # already seen recently
                self._udp_seen[dedup_key] = now + _UDP_DEDUP_TTL
                # Periodic prune
                if len(self._udp_seen) > 5000:
                    self._udp_seen = {
                        k: v for k, v in self._udp_seen.items()
                        if v > now
                    }

        # ── Direction + client detection ──
        all_local = self.all_local_ips
        lan_ips = self.lan_ips
        src_is_local = src_ip in all_local or is_private_ip(src_ip)
        dst_is_local = dst_ip in all_local or is_private_ip(dst_ip)

        if src_is_local and not dst_is_local:
            # Outgoing: local → remote
            remote_ip = dst_ip
            direction = 'outgoing'
            # Client IP: on LAN interface we see the real client
            local_client = src_ip
        elif not src_is_local and dst_is_local:
            # Incoming: remote → local
            remote_ip = src_ip
            direction = 'incoming'
            local_client = dst_ip
        elif src_is_local and dst_is_local:
            return  # internal traffic
        else:
            # Pass-through / forwarded (neither side is us)
            remote_ip = dst_ip
            direction = 'outgoing'
            local_client = src_ip

        # Skip private remote IPs
        if is_private_ip(remote_ip):
            return

        service_port = min(src_port, dst_port)

        if self.on_packet:
            self.on_packet({
                'srcIP': src_ip,
                'srcPort': src_port,
                'dstIP': dst_ip,
                'dstPort': dst_port,
                'remoteIP': remote_ip,
                'localIP': local_client,
                'direction': direction,
                'servicePort': service_port,
                'interface': device,
                'isLAN': is_lan,
                'bytes': 0,
            })

    # ---- Byte enrichment via pfctl ----

    def _sleep(self, seconds):
        """Interruptible sleep (checks _running every 0.1 s)."""
        ticks = int(seconds * 10)
        for _ in range(ticks):
            if not self._running:
                break
            time.sleep(0.1)

    def _enrich_loop(self):
        """Periodically poll pfctl -ss -v for byte counts of active states.

        Runs ``pfctl -ss -v | grep -F -A 1 -e <ip1> …`` every 30 s.
        The grep runs in parallel with pfctl (both C-level), so Python
        only sees the matching states + their stats lines.
        """
        self._sleep(ENRICH_INTERVAL)   # initial delay
        while self._running:
            try:
                self._poll_byte_counts()
            except Exception as e:
                log.debug('Byte-Enrichment Fehler: %s', e)
            self._sleep(ENRICH_INTERVAL)

    def _poll_byte_counts(self):
        """Run pfctl -ss -v | grep -F -A 1, parse bytes, emit updates."""
        all_local = self.all_local_ips
        if not all_local:
            return

        grep_args = []
        for ip in sorted(all_local):
            grep_args.extend(['-e', ip])

        pfctl_cmd = ['/sbin/pfctl', '-ss', '-v']
        if os.getuid() != 0:
            pfctl_cmd = ['/usr/local/bin/sudo', '-n'] + pfctl_cmd

        pfctl_proc = None
        grep_proc = None
        try:
            pfctl_proc = subprocess.Popen(
                pfctl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            grep_proc = subprocess.Popen(
                ['/usr/bin/grep', '-F', '-A', '1'] + grep_args,
                stdin=pfctl_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            pfctl_proc.stdout.close()
            output, _ = grep_proc.communicate(timeout=30)
            pfctl_proc.stderr.close()
            pfctl_proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
            log.debug('pfctl byte-poll: %s', e)
            for p in (grep_proc, pfctl_proc):
                if p:
                    try:
                        p.kill()
                        p.wait(timeout=2)
                    except Exception:
                        pass
            return

        if not output:
            return

        lines = output.splitlines()
        i = 0
        emitted = 0

        while i < len(lines) and self._running:
            line = lines[i]
            i += 1

            # Skip grep separators and stats-only lines
            if not line.startswith('all '):
                continue

            parts = line.split()
            if len(parts) < 5:
                continue
            proto = parts[1]
            if proto not in _PROTO_OK:
                continue

            # Find arrow (-> or <-)
            arrow = None
            arrow_idx = -1
            for j in range(2, min(len(parts), 7)):
                if parts[j] in ('->', '<-'):
                    arrow = parts[j]
                    arrow_idx = j
                    break
            if arrow is None:
                continue

            src_addr = parts[2]
            dst_idx = arrow_idx + 1
            if dst_idx >= len(parts):
                continue
            dst_addr = parts[dst_idx]

            # NAT address in parentheses: (ip:port)
            nat_ip = None
            if arrow_idx > 3:
                maybe_nat = parts[3]
                if (len(maybe_nat) > 3
                        and maybe_nat[0] == '('
                        and maybe_nat[-1] == ')'):
                    c = maybe_nat.rfind(':')
                    if c > 1:
                        nat_ip = maybe_nat[1:c]

            # Parse src IP:port
            c = src_addr.rfind(':')
            if c < 1:
                continue
            src_ip = src_addr[:c]
            try:
                src_port = int(src_addr[c + 1:])
            except ValueError:
                continue

            # Parse dst IP:port
            c = dst_addr.rfind(':')
            if c < 1:
                continue
            dst_ip = dst_addr[:c]
            try:
                dst_port = int(dst_addr[c + 1:])
            except ValueError:
                continue

            # Normalize direction
            if arrow == '<-':
                src_ip, dst_ip = dst_ip, src_ip
                src_port, dst_port = dst_port, src_port

            # Look for stats line (next line, starts with spaces)
            total_bytes = 0
            if i < len(lines):
                stats_line = lines[i]
                if stats_line and not stats_line.startswith(('all ', '--')):
                    bm = _BYTES_RE.search(stats_line)
                    if bm:
                        total_bytes = int(bm.group(1)) + int(bm.group(2))
                    i += 1

            if total_bytes == 0:
                continue

            # Direction
            src_local = src_ip in all_local or is_private_ip(src_ip)
            dst_local = dst_ip in all_local or is_private_ip(dst_ip)

            if src_local and not dst_local:
                remote_ip = dst_ip
                direction = 'outgoing'
                local_client = nat_ip or src_ip
            elif not src_local and dst_local:
                remote_ip = src_ip
                direction = 'incoming'
                local_client = nat_ip or dst_ip
            else:
                continue

            if is_private_ip(remote_ip):
                continue

            service_port = min(src_port, dst_port)

            if self.on_packet:
                self.on_packet({
                    'srcIP': src_ip,
                    'srcPort': src_port,
                    'dstIP': dst_ip,
                    'dstPort': dst_port,
                    'remoteIP': remote_ip,
                    'localIP': local_client,
                    'direction': direction,
                    'servicePort': service_port,
                    'bytes': total_bytes,
                    'update': True,
                })
                emitted += 1

        if emitted > 0:
            log.info('Byte-Enrichment: %d Verbindungen aktualisiert', emitted)

    def stop(self):
        """Stop all tcpdump processes and enrichment thread."""
        self._running = False
        for dev, proc in self._processes.items():
            if proc:
                log.info('Stoppe tcpdump auf %s...', dev)
                try:
                    proc.terminate()
                except OSError:
                    pass
        # Wait and force-kill if needed
        for dev, proc in self._processes.items():
            if proc:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except OSError:
                        pass
        self._processes.clear()
        for t in self._threads.values():
            t.join(timeout=2)
        self._threads.clear()
        self._stderr_threads.clear()
        if self._enrich_thread:
            self._enrich_thread.join(timeout=5)
            self._enrich_thread = None
        self._udp_seen.clear()
        log.info('ConnectionTracker gestoppt')

    def is_running(self):
        return self._running


# ---- Legacy alias for backward compatibility ----
# server.py can import either ConnectionTracker or TcpdumpCapture
TcpdumpCapture = ConnectionTracker
