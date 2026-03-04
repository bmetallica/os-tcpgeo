"""
TCPGeo OPNsense - Local tcpdump Capture (Python)
Spawns tcpdump locally on the configured interface, parses packets,
and calls back with source/dest IP, port, and direction info.
"""

import subprocess
import re
import os
import threading
import logging

log = logging.getLogger('tcpgeo.capture')

# Regex for tcpdump -nn output:
# "12:34:56.789 IP 1.2.3.4.443 > 5.6.7.8.12345: Flags [S], ..."
# Captures: srcIP, srcPort, dstIP, dstPort
PACKET_RE = re.compile(
    r'IP\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d+)\s+>\s+'
    r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d+):'
)

# Private/reserved IP ranges
PRIVATE_RE_LIST = [
    re.compile(r'^10\.'),
    re.compile(r'^172\.(1[6-9]|2\d|3[01])\.'),
    re.compile(r'^192\.168\.'),
    re.compile(r'^127\.'),
    re.compile(r'^0\.'),
    re.compile(r'^169\.254\.'),
    re.compile(r'^224\.'),
    re.compile(r'^255\.'),
]


def is_private_ip(ip):
    """Check if IP is in a private/reserved range"""
    return any(r.match(ip) for r in PRIVATE_RE_LIST)


class TcpdumpCapture:
    """
    Captures network traffic via local tcpdump subprocess.
    Parses each line, determines direction, and calls on_packet callback.
    """

    def __init__(self, device='em0', local_ips=None, on_packet=None,
                 on_status=None, on_error=None):
        self.device = device
        self.local_ips = set(local_ips or [])
        self.on_packet = on_packet
        self.on_status = on_status
        self.on_error = on_error
        self._process = None
        self._running = False
        self._thread = None

    def start(self):
        """Start tcpdump capture in a background thread"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """Main capture loop (runs in thread)"""
        # Validate device name (prevent command injection)
        if not re.match(r'^[a-zA-Z0-9_.]+$', self.device):
            log.error('Ung\u00fcltiger Interface-Name: %s', self.device)
            if self.on_error:
                self.on_error('Ung\u00fcltiger Interface-Name: {}'.format(self.device))
            self._running = False
            return

        args = [
            '/usr/sbin/tcpdump',
            '-l', '-nn', '-q',
            '-i', self.device,
            'ip and (tcp or udp) and not broadcast and not multicast'
        ]

        # Escalate privileges via sudo if not running as root
        if os.getuid() != 0:
            args = ['/usr/local/bin/sudo', '-n'] + args

        log.info('tcpdump %s', ' '.join(args[1:]))

        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                universal_newlines=True
            )
        except (OSError, FileNotFoundError) as e:
            log.error('tcpdump konnte nicht gestartet werden: %s', e)
            if self.on_error:
                self.on_error(str(e))
            self._running = False
            return

        # Read stderr in separate thread for status messages
        stderr_thread = threading.Thread(
            target=self._read_stderr, daemon=True
        )
        stderr_thread.start()

        # Read stdout line by line
        try:
            for line in self._process.stdout:
                if not self._running:
                    break
                line = line.strip()
                if line:
                    self._parse_line(line)
        except Exception as e:
            log.error('Lese-Fehler: %s', e)

        # Process ended
        retcode = self._process.wait() if self._process else -1
        log.info('tcpdump beendet (code=%s)', retcode)
        self._running = False

    def _read_stderr(self):
        """Read stderr for status/error messages"""
        if not self._process or not self._process.stderr:
            return
        try:
            for line in self._process.stderr:
                line = line.strip()
                if not line:
                    continue
                if 'listening on' in line:
                    log.info(line)
                    if self.on_status:
                        self.on_status('capturing')
                elif 'packets captured' in line or 'packets received' in line or 'packets dropped' in line:
                    log.info(line)
                elif re.search(r'permission|error|no suitable|can.*open|unknown|No such', line, re.I):
                    log.error('tcpdump Fehler: %s', line)
                    if self.on_error:
                        self.on_error(line)
        except Exception:
            pass

    def _parse_line(self, line):
        """Parse a tcpdump output line and emit packet event"""
        m = PACKET_RE.search(line)
        if not m:
            return

        src_ip = m.group(1)
        src_port = int(m.group(2))
        dst_ip = m.group(3)
        dst_port = int(m.group(4))

        src_is_local = src_ip in self.local_ips
        dst_is_local = dst_ip in self.local_ips

        if src_is_local and not dst_is_local:
            # Outgoing: local → remote
            remote_ip = dst_ip
            direction = 'outgoing'
            service_port = dst_port if dst_port < src_port else src_port
        elif not src_is_local and dst_is_local:
            # Incoming: remote → local
            remote_ip = src_ip
            direction = 'incoming'
            service_port = dst_port if dst_port < src_port else src_port
        elif src_is_local and dst_is_local:
            # Internal, skip
            return
        else:
            # Neither local (pass-through/forwarded)
            remote_ip = dst_ip
            direction = 'incoming'
            service_port = dst_port if dst_port < src_port else src_port

        # Skip private remote IPs
        if is_private_ip(remote_ip):
            return

        if self.on_packet:
            self.on_packet({
                'srcIP': src_ip,
                'srcPort': src_port,
                'dstIP': dst_ip,
                'dstPort': dst_port,
                'remoteIP': remote_ip,
                'direction': direction,
                'servicePort': service_port
            })

    def stop(self):
        """Stop tcpdump capture"""
        self._running = False
        if self._process:
            log.info('Stoppe tcpdump...')
            try:
                self._process.terminate()
            except OSError:
                pass
            # Force kill after 2 seconds
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except OSError:
                    pass
            self._process = None

    def is_running(self):
        return self._running
