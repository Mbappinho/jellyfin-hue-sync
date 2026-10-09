"""Small stdlib-only HTTP interface for an injected sync engine."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import http.client
import ipaddress
import json
import math
import re
import secrets
import socket
import threading


_SETTINGS = frozenset(('enabled', 'device_id', 'area_id', 'brightness', 'smoothing', 'offset_ms'))


def _validate_settings(body):
    if body.keys() - _SETTINGS:
        raise ValueError('unknown field')
    for key, value in body.items():
        if key == 'enabled':
            valid = type(value) is bool
        elif key == 'device_id':
            valid = isinstance(value, str) and len(value) <= 200
        elif key == 'area_id':
            valid = value is None or (isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,5}', value))
        elif key == 'offset_ms':
            valid = type(value) is int and -5000 <= value <= 5000
        else:
            minimum = 0.01 if key == 'smoothing' else 0
            valid = type(value) in (int, float) and math.isfinite(value) and minimum <= value <= 1
        if not valid:
            raise ValueError('invalid setting')


_ERROR = 'Unable to complete the operation. Check the settings and try again.'
_PHASES = frozenset(('unpaired', 'disabled', 'waiting', 'paused', 'preparing',
                     'streaming', 'testing', 'error', 'stale'))


def _fields(record, names):
    # Records are schemas, not arbitrary nested engine/config dictionaries.
    def public_value(value):
        if isinstance(value, (list, tuple)):
            return [public_value(item) for item in value]
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise ValueError('unexpected nested status value')
    return {key: public_value(record[key]) for key in names if key in record}


def _public_status(data):
    result = _fields(data, ('paired', 'bridge_ip', 'streaming', 'frames_sent', 'rgb'))
    result['phase'] = data['phase'] if data['phase'] in _PHASES else 'error'
    result['error'] = _ERROR if data.get('error') else None
    result['config'] = _fields(data['config'], _SETTINGS)
    _validate_settings(result['config'])
    result['areas'] = [_fields(area, ('id', 'name', 'ids', 'xs', 'active', 'occupied'))
                       for area in data['areas']]
    result['devices'] = [_fields(device, ('id', 'name', 'client', 'active'))
                         for device in data['devices']]
    result['playback'] = _fields(data['playback'], ('title', 'position', 'paused', 'device'))
    cache = data.get('cache', {})
    result['cache'] = _fields(cache, ('phase', 'mode', 'frames'))
    if 'error' in cache:
        result['cache']['error'] = _ERROR if cache['error'] else None
    return result


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('non-finite JSON')


class _HeaderBudget:
    """Bound header reads before stdlib parsing, not after allocation."""
    def __init__(self, source, budget):
        self.source = source
        self.remaining = budget

    def readline(self, limit=-1):
        data = self.source.readline(min(limit, self.remaining + 1) if limit >= 0 else self.remaining + 1)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise http.client.LineTooLong('request headers')
        return data


class _BoundedHTTPServer(ThreadingHTTPServer):
    """Admit workers without creating unbounded threads or waiting clients."""
    max_concurrent_requests=16

    def __init__(self,*args,**kwargs):
        self._slots=threading.BoundedSemaphore(self.max_concurrent_requests)
        super().__init__(*args,**kwargs)

    def process_request(self,request,client_address):
        if not self._slots.acquire(blocking=False):
            body=b'{"ok": false, "error": "Server busy. Try again shortly."}'
            headers=(b'HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\n'
                     b'Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\n'
                     b'X-Content-Type-Options: nosniff\r\nX-Frame-Options: DENY\r\n'
                     b'Referrer-Policy: no-referrer\r\nContent-Security-Policy: default-src \'none\'; frame-ancestors \'none\'\r\n'
                     b'Content-Length: '+str(len(body)).encode()+b'\r\n\r\n')
            try:
                # Read only a bounded header fragment before replying; closing a
                # Windows socket with an unread normal GET can discard its reply.
                import time
                deadline=time.monotonic()+.1;incoming=b''
                while len(incoming)<8192 and b'\r\n\r\n' not in incoming:
                    remaining=deadline-time.monotonic()
                    if remaining<=0: break
                    request.settimeout(remaining)
                    try: part=request.recv(8192-len(incoming))
                    except TimeoutError: break
                    if not part: break
                    incoming+=part
                request.settimeout(.1);request.sendall(headers+body)
                request.shutdown(socket.SHUT_WR)
            except OSError: pass
            finally: self.shutdown_request(request)
            return
        try: super().process_request(request,client_address)
        except BaseException:
            self._slots.release();raise

    def process_request_thread(self,request,client_address):
        try: super().process_request_thread(request,client_address)
        finally: self._slots.release()


def make_server(engine, host, port, allowed_hosts):
    """Return an unstarted IPv4 ThreadingHTTPServer for a trusted LAN.

    Bind host is explicit; port=0 is supported. allowed_hosts contains bare
    IPv4 addresses/hostnames, never URLs or ports; the actual bound port is
    appended for Host validation. Callers own the server lifecycle.
    """
    if not isinstance(host, str) or not host or host != host.strip():
        raise ValueError('Explicit bind host required')
    if not isinstance(allowed_hosts, (list, tuple, set, frozenset)) or not allowed_hosts:
        raise ValueError('Explicit host allowlist required')
    for name in allowed_hosts:
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', name):
            raise ValueError('Allowlist entries must be bare IPv4 addresses or hostnames')
    nonce = secrets.token_urlsafe(32)
    html = (Path(__file__).resolve().parents[1] / 'static' / 'index.html').read_text(encoding='utf-8')
    html = html.replace('__HUE_CSRF__', nonce).encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        def version_string(self):
            return 'HueAdmin'

        def setup(self):
            self.request.settimeout(self.server.request_timeout)
            super().setup()

        def handle(self):
            expired=threading.Event()
            def expire():
                expired.set()
                try: self.request.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            self._expired=expired
            timer=threading.Timer(self.server.request_duration,expire);timer.daemon=True
            timer.start()
            try:
                super().handle()
            except OSError:
                # Disconnects and deadline shutdowns are normal, not secret-bearing logs.
                pass
            finally:
                timer.cancel()

        def parse_request(self):
            original = self.rfile
            self.rfile = _HeaderBudget(original, 8192)
            try:
                accepted = super().parse_request()
            finally:
                self.rfile = original
            if accepted:
                # Do not turn //api/status into an allowed /api/status route.
                self.path = self.requestline.split()[1]
            return accepted

        def send_error(self, code, message=None, explain=None):
            self.respond(405 if code == 501 else code, {'ok': False, 'error': 'Request rejected.'})

        def log_message(self, format, *args):
            pass

        def respond(self, status, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy',
                             f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                             "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            values = self.headers.get_all('Host', [])
            if len(values) != 1 or values[0].lower() not in authorities:
                self.respond(403, {'ok': False, 'error': 'Host rejected.'})
                return False
            origins = self.headers.get_all('Origin', [])
            if origins and origins != ['http://' + self.headers['Host']]:
                self.respond(403, {'ok': False, 'error': 'Origin rejected.'})
                return False
            return True

        def do_POST(self):
            if not self.valid_host():
                return
            supplied = self.headers.get_all('X-Hue-CSRF', [])
            if len(supplied) != 1 or not secrets.compare_digest(supplied[0].encode(), nonce.encode()):
                self.respond(403, {'ok': False, 'error': 'Request rejected.'})
                return
            if self.path not in ('/api/pair', '/api/settings', '/api/test', '/api/start', '/api/stop'):
                self.respond(404, {'ok': False, 'error': 'Unknown route.'})
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if (len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,10}', lengths[0])
                        or self.headers.get_all('Transfer-Encoding', [])):
                    raise ValueError('invalid framing')
                length = int(lengths[0])
                if length > 8192:
                    self.respond(413, {'ok': False, 'error': 'Request too large.'})
                    return
                if self.headers.get_content_type() != 'application/json':
                    self.respond(415, {'ok': False, 'error': 'JSON required.'})
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('incomplete body')
                body = json.loads(raw.decode('utf-8'),
                                  object_pairs_hook=_object, parse_constant=_reject_constant)
                if not isinstance(body, dict):
                    raise ValueError('object required')
                if self._expired.is_set():
                    raise TimeoutError('Request deadline exceeded')
                if self.path in ('/api/start', '/api/stop', '/api/test') and body:
                    raise ValueError('no fields allowed')
                if self.path in ('/api/start', '/api/stop'):
                    engine.configure({'enabled': self.path == '/api/start'})
                elif self.path == '/api/test':
                    engine.test_lights()
                elif self.path == '/api/settings':
                    _validate_settings(body)
                    engine.configure(body)
                else:
                    if set(body) != {'ip'} or not isinstance(body['ip'], str):
                        raise ValueError('IP required')
                    address = ipaddress.IPv4Address(body['ip'])
                    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
                    if not any(address in ipaddress.IPv4Network(network) for network in networks):
                        raise ValueError('private IPv4 required')
                    engine.pair(body['ip'])
                self.respond(200, {'ok': True})
            except Exception:
                self.respond(400, {'ok': False, 'error': _ERROR})

        def do_GET(self):
            if not self.valid_host():
                return
            if self.path == '/api/status':
                try:
                    self.respond(200, _public_status(engine.status()))
                except Exception:
                    self.respond(400, {'ok': False, 'error': _ERROR})
            elif self.path == '/':
                self.respond(200, html, 'text/html; charset=utf-8')
            else:
                self.respond(404, {'ok': False, 'error': 'Unknown route.'})

    server = _BoundedHTTPServer((host, port), Handler)
    actual_port = server.server_address[1]
    authorities = {f'{name.lower()}:{actual_port}' for name in allowed_hosts}
    server.request_timeout = 5.0
    server.request_duration = 15.0
    server.csrf_nonce = nonce
    return server
