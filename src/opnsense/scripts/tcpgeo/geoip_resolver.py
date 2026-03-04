"""
TCPGeo OPNsense - GeoIP Resolver (Python)
IP → { lat, lon, country, city } using MaxMind GeoLite2-City database (maxminddb)
"""

import logging
from pathlib import Path

try:
    import maxminddb
except ImportError:
    maxminddb = None

log = logging.getLogger('tcpgeo.geoip')


class GeoIPResolver:
    """Resolves IP addresses to geographic coordinates using MaxMind GeoLite2"""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else None
        self._reader = None

    def load(self):
        """Load the GeoIP database"""
        if self._reader:
            return

        if maxminddb is None:
            log.error('maxminddb Modul nicht installiert! pip install maxminddb')
            return

        if not self.db_path or not self.db_path.exists():
            log.warning('GeoIP-Datenbank nicht gefunden: %s', self.db_path)
            log.warning('Bitte MaxMind License Key in OPNsense konfigurieren.')
            return

        try:
            self._reader = maxminddb.open_database(str(self.db_path))
            log.info('GeoIP-Datenbank geladen: %s', self.db_path)
        except Exception as e:
            log.error('GeoIP-Datenbank konnte nicht geladen werden: %s', e)

    def resolve(self, ip):
        """
        Resolve an IP to geographic data.

        Returns:
            dict with lat, lon, country, city or None
        """
        if not self._reader:
            return None

        try:
            geo = self._reader.get(ip)
            if not geo or 'location' not in geo:
                return None

            location = geo['location']
            lat = location.get('latitude')
            lon = location.get('longitude')

            if lat is None or lon is None:
                return None

            # Try German names first, then English
            country = ''
            if 'country' in geo and 'names' in geo['country']:
                names = geo['country']['names']
                country = names.get('de', names.get('en', ''))

            city = ''
            if 'city' in geo and 'names' in geo['city']:
                names = geo['city']['names']
                city = names.get('de', names.get('en', ''))

            return {
                'lat': lat,
                'lon': lon,
                'country': country,
                'city': city
            }
        except Exception:
            return None

    def is_ready(self):
        """Check if the GeoIP database is loaded"""
        return self._reader is not None

    def close(self):
        """Close the database"""
        if self._reader:
            self._reader.close()
            self._reader = None
