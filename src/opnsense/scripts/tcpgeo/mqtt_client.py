"""
TCPGeo OPNsense — Pure-Python MQTT 3.1.1 Client + Publisher

Zero external dependencies.  Implements only the subset of MQTT 3.1.1
needed for periodic telemetry publishing (QoS 0, no subscriptions).

Published topics (base topic configurable, default "tcpgeo"):

  {base}/stats/outgoing
      Per-client outgoing connection statistics:
      { timestamp, clients: { "192.168.1.50": { total, bytes, countries: {US:5, DE:3} } } }

  {base}/stats/incoming
      Per-port incoming connection statistics:
      { timestamp, ports: { "22": { label:"SSH", total:150, countries: {CN:80, RU:45} } } }

  {base}/connections
      Snapshot of recent connections:
      { timestamp, count, connections: [ {dir, client, country, city, port, label, bytes} ] }

CPU impact: negligible — one TCP connection, small JSON payloads every N seconds.
RAM impact: ~2–3 MB for aggregation buffers.
"""

import socket
import struct
import threading
import time
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger('tcpgeo.mqtt')

# ---- MQTT 3.1.1 Packet Types ----
_CONNECT = 1
_CONNACK = 2
_PUBLISH = 3
_PINGREQ = 12
_PINGRESP = 13
_DISCONNECT = 14


class MQTTError(Exception):
    """MQTT protocol or connection error."""
    pass


class MQTTClient:
    """Minimal MQTT 3.1.1 client — publish-only, QoS 0, pure Python.

    Implements CONNECT, PUBLISH, PINGREQ, DISCONNECT.
    No subscriptions, no QoS 1/2, no will messages.
    """

    def __init__(self, host, port=1883, client_id=None,
                 username=None, password=None, keepalive=60):
        self.host = host
        self.port = port
        self.client_id = client_id or ('tcpgeo-%d' % int(time.time()))
        self.username = username
        self.password = password
        self.keepalive = keepalive
        self._sock = None
        self._lock = threading.Lock()

    # ---- Public API ----

    def connect(self):
        """Connect to MQTT broker.  Raises MQTTError on failure."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            sock.connect((self.host, self.port))
        except (OSError, socket.error) as e:
            sock.close()
            raise MQTTError('TCP-Verbindung fehlgeschlagen: %s' % e)

        self._sock = sock

        # ---- Build CONNECT packet ----
        proto_name = self._encode_utf8('MQTT')
        proto_level = b'\x04'  # 3.1.1

        flags = 0x02  # Clean Session
        if self.username:
            flags |= 0x80
        if self.password:
            flags |= 0x40

        var_header = (proto_name
                      + proto_level
                      + struct.pack('!B', flags)
                      + struct.pack('!H', self.keepalive))

        payload = self._encode_utf8(self.client_id)
        if self.username:
            payload += self._encode_utf8(self.username)
        if self.password:
            payload += self._encode_utf8(self.password)

        self._send_packet(_CONNECT, 0, var_header + payload)

        # ---- Read CONNACK ----
        resp = self._read_packet(timeout=10)
        if resp is None:
            self._close()
            raise MQTTError('Keine CONNACK-Antwort vom Broker')

        ptype, _, data = resp
        if ptype != _CONNACK:
            self._close()
            raise MQTTError('Unerwartetes Paket (erwartet CONNACK, erhalten %d)' % ptype)
        if len(data) < 2:
            self._close()
            raise MQTTError('CONNACK zu kurz')

        rc = data[1]
        if rc != 0:
            self._close()
            codes = {
                1: 'Protokollfehler',
                2: 'Client-ID abgelehnt',
                3: 'Server nicht verfügbar',
                4: 'Anmeldedaten ungültig',
                5: 'Nicht autorisiert',
            }
            raise MQTTError('Broker lehnt Verbindung ab: %s (rc=%d)'
                            % (codes.get(rc, 'Unbekannt'), rc))

        log.info('MQTT verbunden: %s:%d', self.host, self.port)

    def publish(self, topic, payload, retain=False):
        """Publish a message with QoS 0."""
        flags = 0
        if retain:
            flags |= 0x01
        data = self._encode_utf8(topic)
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        data += payload
        with self._lock:
            self._send_packet(_PUBLISH, flags, data)

    def ping(self):
        """Send PINGREQ keepalive."""
        with self._lock:
            self._send_packet(_PINGREQ, 0, b'')
        # Try to read PINGRESP (non-blocking, best-effort)
        try:
            self._read_packet(timeout=2)
        except Exception:
            pass

    def disconnect(self):
        """Send DISCONNECT and close socket."""
        try:
            with self._lock:
                self._send_packet(_DISCONNECT, 0, b'')
        except Exception:
            pass
        self._close()

    @property
    def connected(self):
        return self._sock is not None

    # ---- Protocol helpers ----

    def _close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    @staticmethod
    def _encode_utf8(s):
        """Encode a string as MQTT UTF-8 (2-byte length prefix + bytes)."""
        encoded = s.encode('utf-8')
        return struct.pack('!H', len(encoded)) + encoded

    @staticmethod
    def _encode_remaining_length(length):
        """Encode MQTT variable-length integer."""
        buf = bytearray()
        while True:
            byte = length % 128
            length //= 128
            if length > 0:
                byte |= 0x80
            buf.append(byte)
            if length == 0:
                break
        return bytes(buf)

    def _send_packet(self, ptype, flags, data):
        """Send a complete MQTT packet."""
        if not self._sock:
            raise MQTTError('Nicht verbunden')
        header = struct.pack('!B', (ptype << 4) | (flags & 0x0F))
        header += self._encode_remaining_length(len(data))
        try:
            self._sock.sendall(header + data)
        except (OSError, socket.error) as e:
            self._close()
            raise MQTTError('Sende-Fehler: %s' % e)

    def _read_packet(self, timeout=5):
        """Read one MQTT packet.  Returns (type, flags, data) or None."""
        if not self._sock:
            return None
        self._sock.settimeout(timeout)
        try:
            b = self._sock.recv(1)
            if not b:
                return None
            byte0 = b[0]
            ptype = (byte0 >> 4) & 0x0F
            flags = byte0 & 0x0F

            # Remaining length (variable-length encoding)
            multiplier = 1
            remaining = 0
            for _ in range(4):
                b = self._sock.recv(1)
                if not b:
                    return None
                remaining += (b[0] & 0x7F) * multiplier
                if (b[0] & 0x80) == 0:
                    break
                multiplier *= 128

            # Payload
            data = b''
            while len(data) < remaining:
                chunk = self._sock.recv(remaining - len(data))
                if not chunk:
                    return None
                data += chunk

            return (ptype, flags, data)
        except socket.timeout:
            return None
        except OSError:
            self._close()
            return None


# ====================================================================
# MQTTPublisher — aggregates TCPGeo data and publishes periodically
# ====================================================================

class MQTTPublisher:
    """Periodically publishes TCPGeo connection data to an MQTT broker.

    Topics (with base_topic = "tcpgeo"):
      tcpgeo/stats/outgoing   — per-client, per-country outgoing stats
      tcpgeo/stats/incoming   — per-port, per-country incoming stats
      tcpgeo/clients/outgoing — per-client detailed outgoing connections (city, port, bytes)
      tcpgeo/connections      — snapshot of recent connections

    Thread-safe: on_packet() is called from capture threads.
    """

    def __init__(self, host, port=1883, username=None, password=None,
                 base_topic='tcpgeo', interval=60, mask_ips=True):
        self.host = host
        self.port = port
        self.username = username or None
        self.password = password or None
        self.base_topic = base_topic.rstrip('/')
        self.interval = max(10, interval)
        self.mask_ips = mask_ips

        self._client = None
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        # ---- Aggregation state ----
        # Recent connections (ring buffer, capped)
        self._connections = []
        # Outgoing stats: client_ip → { country → count }
        self._out_stats = {}
        # Outgoing bytes: client_ip → total_bytes
        self._out_bytes = {}
        # Incoming stats: port_str → { country → count }
        self._in_stats = {}
        # Port labels: port_str → label
        self._in_labels = {}
        # Per-client detailed outgoing: client_ip → { "CC|City|port" → {country,city,port,label,count,bytes} }
        self._out_detail = {}

    def on_packet(self, pkt):
        """Called for each resolved packet.

        Expected dict keys:
            direction, ip (remote), country, city, port, portLabel,
            bytes, localIP, update, lat, lon
        """
        with self._lock:
            direction = pkt.get('direction', '')
            country = pkt.get('country', '')
            port = pkt.get('port', 0)
            client_ip = pkt.get('localIP', '')
            is_update = pkt.get('update', False)
            byte_count = pkt.get('bytes', 0)
            port_label = pkt.get('portLabel') or ('Port %d' % port)

            if is_update:
                # Enrichment: update byte counters + create entries for
                # connections whose SYN was missed (race condition at startup,
                # or MQTT enabled while connections were already active).
                if client_ip and direction == 'outgoing' and country:
                    if byte_count > 0:
                        self._out_bytes[client_ip] = max(
                            self._out_bytes.get(client_ip, 0), byte_count)

                    # Ensure outgoing stats entry exists
                    if client_ip not in self._out_stats:
                        self._out_stats[client_ip] = {}
                    cs = self._out_stats[client_ip]
                    if country not in cs:
                        cs[country] = 1  # count at least 1 connection

                    # Ensure detail entry exists
                    city = pkt.get('city', '')
                    detail_key = '%s|%s|%d' % (country, city, port)
                    if client_ip not in self._out_detail:
                        self._out_detail[client_ip] = {}
                    cd = self._out_detail[client_ip]
                    if detail_key not in cd:
                        cd[detail_key] = {
                            'country': country,
                            'city': city,
                            'port': port,
                            'label': port_label,
                            'count': 1,
                            'bytes': byte_count,
                        }
                    elif byte_count > cd[detail_key]['bytes']:
                        cd[detail_key]['bytes'] = byte_count

                elif client_ip and direction == 'incoming' and port and country:
                    # Ensure incoming stats entry exists
                    ps = str(port)
                    if ps not in self._in_stats:
                        self._in_stats[ps] = {}
                    ic = self._in_stats[ps]
                    if country not in ic:
                        ic[country] = 1
                    self._in_labels.setdefault(ps, port_label)

                return

            # ---- New connection ----
            self._connections.append({
                'dir': direction,
                'client_ip': client_ip,
                'country': country,
                'city': pkt.get('city', ''),
                'port': port,
                'label': port_label,
                'bytes': byte_count,
            })
            # Cap buffer
            if len(self._connections) > 2000:
                self._connections = self._connections[-1000:]

            # Outgoing stats
            if direction == 'outgoing' and client_ip and country:
                if client_ip not in self._out_stats:
                    self._out_stats[client_ip] = {}
                cs = self._out_stats[client_ip]
                cs[country] = cs.get(country, 0) + 1
                self._out_bytes[client_ip] = (
                    self._out_bytes.get(client_ip, 0) + byte_count)

                # Detailed outgoing per client
                city = pkt.get('city', '')
                detail_key = '%s|%s|%d' % (country, city, port)
                if client_ip not in self._out_detail:
                    self._out_detail[client_ip] = {}
                cd = self._out_detail[client_ip]
                if detail_key in cd:
                    cd[detail_key]['count'] += 1
                    cd[detail_key]['bytes'] += byte_count
                else:
                    cd[detail_key] = {
                        'country': country,
                        'city': city,
                        'port': port,
                        'label': port_label,
                        'count': 1,
                        'bytes': byte_count,
                    }

            # Incoming stats
            if direction == 'incoming' and port and country:
                ps = str(port)
                if ps not in self._in_stats:
                    self._in_stats[ps] = {}
                cs = self._in_stats[ps]
                cs[country] = cs.get(country, 0) + 1
                self._in_labels[ps] = port_label

    # ---- Lifecycle ----

    def start(self):
        """Start the background publisher thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name='mqtt-publisher'
        )
        self._thread.start()
        log.info('MQTT Publisher gestartet (Broker: %s:%d, Topic: %s, Intervall: %ds)',
                 self.host, self.port, self.base_topic, self.interval)

    def stop(self):
        """Stop publisher and disconnect from broker."""
        self._running = False
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        log.info('MQTT Publisher gestoppt')

    # ---- Internal ----

    def _mask_ip(self, ip):
        """Optionally mask last octet of an IPv4 address."""
        if not self.mask_ips or not ip:
            return ip
        parts = ip.rsplit('.', 1)
        return parts[0] + '.xxx' if len(parts) == 2 else ip

    def _ensure_connected(self):
        """Connect to broker if not already connected.  Returns True on success."""
        if self._client and self._client.connected:
            return True
        try:
            self._client = MQTTClient(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                keepalive=max(self.interval * 2, 60),
            )
            self._client.connect()
            return True
        except Exception as e:
            log.warning('MQTT Verbindung fehlgeschlagen: %s', e)
            self._client = None
            return False

    def _sleep(self, seconds):
        """Interruptible sleep (checks _running every 0.5 s)."""
        ticks = int(seconds * 2)
        for _ in range(ticks):
            if not self._running:
                break
            time.sleep(0.5)

    def _run(self):
        """Main publisher loop."""
        # Initial delay (let capture settle)
        self._sleep(min(self.interval, 15))

        while self._running:
            try:
                if self._ensure_connected():
                    self._publish_all()
                    # Keepalive
                    try:
                        self._client.ping()
                    except Exception:
                        self._client = None
            except Exception as e:
                log.debug('MQTT Publish-Fehler: %s', e)
                self._client = None

            self._sleep(self.interval)

    def _publish_all(self):
        """Publish all four topics."""
        if not self._client or not self._client.connected:
            return

        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        base = self.base_topic
        n_out = n_in = n_conn = n_detail = 0

        # ---- 1. Outgoing stats (per client, per country) ----
        with self._lock:
            out_data = {}
            for cip, countries in self._out_stats.items():
                display = self._mask_ip(cip)
                out_data[display] = {
                    'total': sum(countries.values()),
                    'bytes': self._out_bytes.get(cip, 0),
                    'countries': dict(countries),
                }

        if out_data:
            payload = json.dumps(
                {'timestamp': ts, 'clients': out_data},
                separators=(',', ':'))
            self._client.publish(
                '%s/stats/outgoing' % base, payload, retain=True)
            n_out = len(out_data)

        # ---- 2. Incoming stats (per port, per country) ----
        with self._lock:
            in_data = {}
            for ps, countries in self._in_stats.items():
                in_data[ps] = {
                    'label': self._in_labels.get(ps, 'Port %s' % ps),
                    'total': sum(countries.values()),
                    'countries': dict(countries),
                }

        if in_data:
            payload = json.dumps(
                {'timestamp': ts, 'ports': in_data},
                separators=(',', ':'))
            self._client.publish(
                '%s/stats/incoming' % base, payload, retain=True)
            n_in = len(in_data)

        # ---- 3. Per-client detailed outgoing connections ----
        with self._lock:
            detail_data = {}
            for cip, conns in self._out_detail.items():
                display = self._mask_ip(cip)
                entries = []
                for dk in sorted(conns, key=lambda k: conns[k]['count'],
                                 reverse=True):
                    c = conns[dk]
                    entries.append({
                        'country': c['country'],
                        'city': c['city'],
                        'port': c['port'],
                        'label': c['label'],
                        'count': c['count'],
                        'bytes': c['bytes'],
                    })
                detail_data[display] = entries

        if detail_data:
            payload = json.dumps(
                {'timestamp': ts, 'clients': detail_data},
                separators=(',', ':'))
            self._client.publish(
                '%s/clients/outgoing' % base, payload, retain=True)
            n_detail = len(detail_data)

        # ---- 4. Recent connections snapshot ----
        with self._lock:
            recent = list(self._connections[-200:])

        conns = []
        for c in recent:
            conns.append({
                'dir': c['dir'],
                'client': self._mask_ip(c['client_ip']),
                'country': c['country'],
                'city': c['city'],
                'port': c['port'],
                'label': c['label'],
                'bytes': c['bytes'],
            })

        payload = json.dumps(
            {'timestamp': ts, 'count': len(conns), 'connections': conns},
            separators=(',', ':'))
        self._client.publish(
            '%s/connections' % base, payload, retain=True)
        n_conn = len(conns)

        log.debug('MQTT publish: %d outgoing clients, %d detailed, '
                  '%d incoming ports, %d connections',
                  n_out, n_detail, n_in, n_conn)
