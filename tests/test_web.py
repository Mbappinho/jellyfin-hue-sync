"""Real loopback HTTP tests; all engine data below are offline fixtures."""
import copy
import http.client
from html.parser import HTMLParser
import json
import re
import socket
import shutil
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer

try:
    from ambilight.web import make_server
except ImportError:
    make_server = None


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class FixtureEngine:
    def __init__(self):
        self.calls = []
        self.data = {
            'paired': False, 'bridge_ip': '',
            'config': {'enabled': False, 'device_id': '', 'area_id': None,
                       'brightness': 0.7, 'smoothing': 1.0, 'offset_ms': 0},
            'areas': [{'id': '1', 'name': '<b>Salon</b>', 'ids': [1],
                       'xs': [0.5], 'active': False, 'occupied': False}],
            'devices': [{'id': 'device-1', 'name': '<img src=x>',
                         'client': 'Jellyfin TV', 'active': True}],
            'playback': {'title': 'Film de test', 'position': 12.5,
                         'paused': False, 'device': 'device-1'},
            'phase': 'unpaired', 'error': None, 'streaming': False,
            'frames_sent': 0, 'rgb': [[255, 0, 128]], 'cache': {},
        }

    def status(self):
        return copy.deepcopy(self.data)

    def pair(self, ip):
        self.calls.append(('pair', ip))
        self.data.update(paired=True, bridge_ip=ip)

    def configure(self, body):
        self.calls.append(('configure', copy.deepcopy(body)))
        self.data['config'].update(body)

    def test_lights(self):
        self.calls.append(('test_lights',))
        self.data['phase'] = 'testing'


class WebTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(make_server, 'HTTP factory has not been implemented')
        self.engine = FixtureEngine()
        self.server = make_server(self.engine, '127.0.0.1', 0, ['127.0.0.1', 'localhost'])
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.host = f'127.0.0.1:{self.port}'

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            try:
                response = conn.getresponse()
            except (http.client.RemoteDisconnected, ConnectionAbortedError, ConnectionResetError):
                self.fail('HTTP server closed without a sanitized response')
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def raw(self, request):
        with socket.create_connection(('127.0.0.1', self.port), timeout=2) as sock:
            sock.sendall(request.encode('ascii'))
            sock.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(sock)
            response.begin()
            return response.status, dict(response.getheaders()), response.read()

    def nonce(self):
        status, _, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        match = re.search(rb'<meta name="hue-csrf" content="([A-Za-z0-9_-]{32,})">', body)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def post(self, path, body=None, headers=None):
        supplied = {'X-Hue-CSRF': self.nonce(), 'Content-Type': 'application/json'}
        supplied.update(headers or {})
        return self.request('POST', path, json.dumps(body or {}), supplied)

    def test_http_errors_and_status_diagnostics_are_english_and_secret_safe(self):
        cases = [
            (self.request('GET', '/', headers={'Host': 'invalid.example'}), 403, 'Host rejected.'),
            (self.request('GET', '/', headers={'Origin': 'http://invalid.example'}), 403, 'Origin rejected.'),
            (self.request('POST', '/api/start', '{}'), 403, 'Request rejected.'),
            (self.request('PUT', '/api/start'), 405, 'Request rejected.'),
            (self.request('GET', '/missing'), 404, 'Unknown route.'),
            (self.post('/missing'), 404, 'Unknown route.'),
            (self.request('POST', '/api/settings', ' ' * 8193,
                          {'X-Hue-CSRF': self.nonce(), 'Content-Type': 'application/json'}),
             413, 'Request too large.'),
            (self.post('/api/start', headers={'Content-Type': 'text/plain'}), 415, 'JSON required.'),
            (self.post('/api/settings', {'brightness': -1}), 400,
             'Unable to complete the operation. Check the settings and try again.'),
        ]
        for (status, _, body), expected_status, message in cases:
            with self.subTest(message=message):
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(body), {'ok': False, 'error': message})
        self.engine.data.update(phase='error', error='fixture-private-diagnostic')
        self.engine.data['cache'] = {'error': 'fixture-private-diagnostic'}
        status, _, body = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        self.assertNotIn(b'fixture-private-diagnostic', body)
        result = json.loads(body)
        message = 'Unable to complete the operation. Check the settings and try again.'
        self.assertEqual(result['error'], message)
        self.assertEqual(result['cache']['error'], message)
        self.assertEqual(self.engine.calls, [])

    def test_engine_failures_are_sanitized_http400(self):
        secret = 'fixture-clientkey-secret C:/private/config.json'
        def fail(ip):
            raise ValueError(secret)
        self.engine.pair = fail
        status, headers, body = self.post('/api/pair', {'ip': '10.0.0.2'})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)['ok'], False)
        self.assertNotIn(secret.encode(), body)
        self.assertNotIn(b'Traceback', body)

    def test_settings_updates_selected_area_device_and_timing(self):
        config = {'device_id': 'device-1', 'area_id': '1',
                  'brightness': 0.6, 'smoothing': 0.8, 'offset_ms': 250}
        status, _, body = self.post('/api/settings', config)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True})
        self.assertEqual(self.engine.calls, [('configure', config)])
        readback = json.loads(self.request('GET', '/api/status')[2])
        for key, value in config.items():
            self.assertEqual(readback['config'][key], value)

    def test_start_stop_toggle_only_enabled_and_read_back(self):
        for action, enabled in [('start', True), ('stop', False)]:
            with self.subTest(action=action):
                status, _, body = self.post('/api/' + action)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {'ok': True})
                self.assertEqual(self.engine.calls[-1], ('configure', {'enabled': enabled}))
                readback = json.loads(self.request('GET', '/api/status')[2])
                self.assertEqual(readback['config']['enabled'], enabled)

    def test_light_test_calls_engine_and_readback_shows_testing(self):
        status, _, body = self.post('/api/test')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True})
        self.assertEqual(self.engine.calls, [('test_lights',)])
        self.assertEqual(json.loads(self.request('GET', '/api/status')[2])['phase'], 'testing')

    def test_every_post_requires_exact_csrf_nonce(self):
        for path in ['/api/pair', '/api/settings', '/api/test', '/api/start', '/api/stop', '/missing', '/', '/api/status']:
            for nonce in [None, 'incorrect']:
                with self.subTest(path=path, nonce=nonce):
                    headers = {'Content-Type': 'application/json'}
                    if nonce is not None:
                        headers['X-Hue-CSRF'] = nonce
                    status, _, body = self.request('POST', path, '{"ip":"10.0.0.2"}', headers)
                    self.assertEqual(status, 403)
                    self.assertFalse(json.loads(body)['ok'])
        self.assertEqual(self.engine.calls, [])

    def test_host_allowlist_requires_actual_bound_port(self):
        for host in ['evil.example:' + str(self.port), '127.0.0.1:1', '127.0.0.1',
                     'localhost.evil:' + str(self.port)]:
            with self.subTest(host=host):
                self.assertEqual(self.request('GET', '/', headers={'Host': host})[0], 403)
                self.assertEqual(self.post('/api/start', headers={'Host': host})[0], 403)
        self.assertEqual(self.request('GET', '/', headers={'Host': f'localhost:{self.port}'})[0], 200)
        self.assertEqual(self.engine.calls, [])

    def test_origin_must_exactly_match_request_host(self):
        for origin in ['null', 'https://' + self.host, 'http://evil.example',
                       'http://' + self.host + '/', 'http://localhost:' + str(self.port)]:
            with self.subTest(origin=origin):
                self.assertEqual(self.post('/api/start', headers={'Origin': origin})[0], 403)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.post('/api/start', headers={'Origin': 'http://' + self.host})[0], 200)
        self.assertEqual(self.post('/api/stop', headers={'Host': f'localhost:{self.port}',
                         'Origin': f'http://localhost:{self.port}'})[0], 200)

    def test_routes_are_exact_and_get_never_changes_state(self):
        for path in ['/api/start', '/api/stop', '/api/test', '/api/pair', '/api/settings',
                     '/missing', '/api/status?x=1', '//evil.example/', '/%2e%2e/config.json',
                     'http://evil.example/']:
            with self.subTest(path=path):
                status, headers, _ = self.request('GET', path, headers={'Host': self.host})
                self.assertEqual(status, 404)
                self.assertNotIn('Location', headers)
        for path in ['/missing', '/', '/api/status', '/api/start?x=1']:
            self.assertEqual(self.post(path, {'ip': '10.0.0.2'})[0], 404)
        for method in ['PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS', 'TRACE', 'CONNECT', 'BREW']:
            status, headers, body = self.request(method, '/api/start')
            self.assertEqual(status, 405)
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.engine.calls, [])

    def test_post_body_is_bounded_and_must_be_json_object(self):
        auth = {'X-Hue-CSRF': self.nonce(), 'Content-Type': 'application/json'}
        status, _, _ = self.request('POST', '/api/settings', b' ' * 8193, auth)
        self.assertEqual(status, 413)
        for body in ['[]', 'null', 'true', '"text"', '{', '{"enabled":true,"enabled":false}',
                     '{"brightness":NaN}', '{"brightness":Infinity}']:
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', '/api/settings', body, auth)[0], 400)
        self.assertEqual(self.post('/api/start', headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.request('POST', '/api/start', '{}'.ljust(8192), auth)[0], 200)

    def test_settings_reject_private_fields_and_invalid_values_atomically(self):
        invalid = [{'username': 'fixture-secret'}, {'clientkey': 'fixture-secret'},
                   {'bridge_ip': '10.0.0.2'}, {'hue': {}}, {'enabled': 1},
                   {'device_id': None}, {'device_id': 'x' * 201}, {'area_id': '01'},
                   {'area_id': '0'}, {'area_id': '1234567'}, {'area_id': 1},
                   {'area_id': ''}, {'brightness': True}, {'brightness': -0.01},
                   {'brightness': 1.01}, {'brightness': '0.5'}, {'brightness': 1e309},
                   {'smoothing': 0}, {'smoothing': 1.01}, {'offset_ms': 0.5},
                   {'offset_ms': True}, {'offset_ms': 5001}, {'offset_ms': -5001},
                   {'device_id': 'valid', 'brightness': -1}]
        original = copy.deepcopy(self.engine.data)
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.post('/api/settings', body)[0], 400)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.engine.data, original)
        for body in [{'area_id': None}, {'area_id': '123456'}, {'enabled': True},
                     {'device_id': 'x' * 200}, {'brightness': 0}, {'brightness': 1},
                     {'smoothing': 0.01}, {'smoothing': 1}, {'offset_ms': -5000},
                     {'offset_ms': 5000}]:
            self.assertEqual(self.post('/api/settings', body)[0], 200)

    def test_pair_accepts_only_literal_private_ipv4_and_ip_field(self):
        for body in [{'ip': 'example.com'}, {'ip': 'http://10.0.0.2/'}, {'ip': '127.0.0.1'},
                     {'ip': '8.8.8.8'}, {'ip': '169.254.1.1'}, {'ip': '10.00.0.2'},
                     {'ip': '::1'}, {'ip': 123}, {'ip': '10.0.0.2', 'username': 'secret'}, {}]:
            with self.subTest(body=body):
                self.assertEqual(self.post('/api/pair', body)[0], 400)
        self.assertEqual(self.engine.calls, [])
        for ip in ['10.0.0.2', '172.16.1.2', '192.168.1.2']:
            self.assertEqual(self.post('/api/pair', {'ip': ip})[0], 200)
        for path in ['/api/start', '/api/stop', '/api/test']:
            self.assertEqual(self.post(path, {'unexpected': 'field'})[0], 400)

    def test_status_projects_public_fields_and_sanitizes_diagnostics(self):
        secret = 'fixture-owner-token'
        self.engine.data.update(username=secret, clientkey=secret, owner=secret,
                                config_path='C:/private/config.json', error=secret)
        self.engine.data['config'].update(username=secret, hue={'clientkey': secret})
        self.engine.data['areas'][0]['owner'] = secret
        self.engine.data['devices'][0]['token'] = secret
        self.engine.data['playback']['Path'] = 'C:/private/film.mkv'
        self.engine.data['cache'] = {'phase': 'ready', 'mode': 'vaapi', 'frames': 30,
                                     'path': 'C:/private/cache', 'error': secret,
                                     'nested': {'owner': secret}}
        status, _, body = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        self.assertNotIn(secret.encode(), body)
        self.assertNotIn(b'C:/private', body)
        self.assertNotIn(b'username', body)
        self.assertNotIn(b'clientkey', body)
        self.assertNotIn(b'owner', body)
        result = json.loads(body)
        self.assertEqual(result['cache']['frames'], 30)
        self.assertEqual(result['cache']['phase'], 'ready')
        self.assertEqual(result['config']['smoothing'], 1.0)
        self.assertIsInstance(result['error'], str)

    def test_status_exceptions_and_nested_private_payloads_fail_closed(self):
        for data in [None, {'owner': 'fixture-secret'}]:
            with self.subTest(data=data):
                def fail():
                    raise RuntimeError('fixture-secret C:/private/config.json')
                if data is None:
                    self.engine.status = fail
                else:
                    self.engine.status = lambda: copy.deepcopy(self.engine.data)
                    self.engine.data['devices'][0]['client'] = data
                status, _, body = self.request('GET', '/api/status')
                self.assertEqual(status, 400)
                self.assertFalse(json.loads(body)['ok'])
                self.assertNotIn(b'fixture-secret', body)
                self.assertNotIn(b'C:/private', body)

    def test_rejects_ambiguous_headers_and_body_framing(self):
        nonce = self.nonce()
        base = f'POST /api/start HTTP/1.1\r\nHost: {self.host}\r\nX-Hue-CSRF: {nonce}\r\nContent-Type: application/json\r\n'
        for extra in ['Content-Length: 2\r\nContent-Length: 2',
                      'Content-Length: -1', 'Content-Length: +2', 'Content-Length: 2x',
                      'Transfer-Encoding: chunked\r\nContent-Length: 2',
                      'Content-Length: 3', '']:
            with self.subTest(extra=extra):
                self.assertEqual(self.raw(base + extra + '\r\n\r\n{}')[0], 400)
        for header in [f'Host: {self.host}\r\nHost: {self.host}', '']:
            self.assertEqual(self.raw('GET / HTTP/1.1\r\n' + header + '\r\n\r\n')[0], 403)
        self.assertEqual(self.raw(base + f'X-Hue-CSRF: {nonce}\r\nContent-Length: 2\r\n\r\n{{}}')[0], 403)
        self.assertEqual(self.raw(base + f'Origin: http://{self.host}\r\nOrigin: http://{self.host}\r\nContent-Length: 2\r\n\r\n{{}}')[0], 403)
        self.assertEqual(self.engine.calls, [])

    def test_concurrent_requests_are_capped_and_overload_recovers(self):
        release=threading.Event();condition=threading.Condition();entered=[]
        original=self.engine.status
        def blocked_status():
            with condition:
                entered.append(threading.get_ident());condition.notify_all()
            release.wait(2)
            return original()
        self.engine.status=blocked_status
        clients=[]
        try:
            for _ in range(16):
                sock=socket.create_connection(('127.0.0.1',self.port),timeout=1)
                clients.append(sock)
                sock.sendall(f'GET /api/status HTTP/1.1\r\nHost: {self.host}\r\n\r\n'.encode())
            with condition:
                self.assertTrue(condition.wait_for(lambda:len(entered)==16,timeout=1))
            conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=.3)
            try:
                conn.request('GET','/api/status')
                try: response=conn.getresponse()
                except TimeoutError: self.fail('Overload spawned another blocked request instead of rejecting promptly')
                self.assertEqual(response.status,503)
                self.assertEqual(json.loads(response.read()),{'ok':False,'error':'Server busy. Try again shortly.'})
                self.assertEqual(response.getheader('Cache-Control'),'no-store')
            finally: conn.close()
            self.assertEqual(len(entered),16)
        finally:
            release.set()
            for sock in clients: sock.close()
        self.engine.status=original
        self.assertEqual(self.request('GET','/api/status')[0],200)

    def test_absolute_request_deadline_stops_slow_drip_headers_and_body(self):
        import time
        self.server.request_timeout=.2
        self.server.request_duration=.15
        nonce=self.nonce()
        for prefix,drip in [
                (b'GET / HTTP/1.1\r\nHost: ',b'x'*30),
                ((f'POST /api/start HTTP/1.1\r\nHost: {self.host}\r\nX-Hue-CSRF: {nonce}\r\nContent-Type: application/json\r\nContent-Length: 30\r\n\r\n').encode(),b' '*30)]:
            with self.subTest(prefix=prefix):
                with socket.create_connection(('127.0.0.1',self.port),timeout=.6) as sock:
                    sock.sendall(prefix)
                    def send_drip():
                        try:
                            for byte in drip:
                                sock.sendall(bytes([byte]));time.sleep(.03)
                        except OSError: pass
                    sender=threading.Thread(target=send_drip,daemon=True);sender.start()
                    started=time.monotonic()
                    try: sock.recv(4096)
                    except (ConnectionResetError,ConnectionAbortedError): pass
                    except TimeoutError: self.fail('Slow-drip HTTP request exceeded absolute deadline')
                    self.assertLess(time.monotonic()-started,.6)
                sender.join(1)
        self.assertEqual(self.engine.calls,[])
        self.assertEqual(self.request('GET','/api/status')[0],200)

    def test_socket_deadline_bounds_incomplete_headers_and_body(self):
        self.server.request_timeout = 0.1
        for partial in [b'GET / HTTP/1.1\r\nHost:',
                        (f'POST /api/start HTTP/1.1\r\nHost: {self.host}\r\nX-Hue-CSRF: {self.nonce()}\r\nContent-Type: application/json\r\nContent-Length: 10\r\n\r\n{{}}').encode()]:
            with self.subTest(partial=partial):
                with socket.create_connection(('127.0.0.1', self.port), timeout=1) as sock:
                    sock.sendall(partial)
                    try:
                        received = sock.recv(4096)
                    except TimeoutError:
                        self.fail('Incomplete HTTP request exceeded server socket deadline')
                    self.assertTrue(not received or b'400' in received)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.request('GET', '/api/status')[0], 200)

    def test_factory_requires_explicit_bind_and_bare_host_allowlist(self):
        for host, allowed in [('', ['127.0.0.1']), ('127.0.0.1', []),
                              ('127.0.0.1', 'localhost'), ('127.0.0.1', ['*']),
                              ('127.0.0.1', ['http://localhost']),
                              ('127.0.0.1', ['localhost:80']), ('127.0.0.1', ['localhost/'])]:
            with self.subTest(host=host, allowed=allowed):
                with self.assertRaises(ValueError):
                    result = make_server(FixtureEngine(), host, 0, allowed)
                    result.server_close()

    def test_responses_disable_caching_embedding_and_cross_origin_access(self):
        for method, path in [('GET', '/'), ('GET', '/api/status'), ('GET', '/missing'),
                             ('POST', '/api/start')]:
            status, headers, body = self.request(method, path, '{}' if method == 'POST' else None)
            self.assertEqual(headers.get('Cache-Control'), 'no-store')
            self.assertEqual(headers.get('X-Content-Type-Options'), 'nosniff')
            self.assertEqual(headers.get('Referrer-Policy'), 'no-referrer')
            self.assertEqual(headers.get('X-Frame-Options'), 'DENY')
            self.assertIn("frame-ancestors 'none'", headers.get('Content-Security-Policy', ''))
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            self.assertNotIn('Python', headers.get('Server', ''))
        self.assertEqual(self.request('GET', '/', headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/status', headers={'Origin': 'http://' + self.host})[0], 200)
        policy = self.request('GET', '/')[1]['Content-Security-Policy']
        self.assertIn("script-src 'nonce-" + self.nonce() + "'", policy)
        self.assertNotIn("'unsafe-inline'", policy)

    def test_english_controls_help_and_accessible_labels(self):
        html = self.request('GET', '/')[2].decode()
        markup = Markup(html)
        by_id = {attrs['id']: (tag, attrs) for tag, attrs in markup.elements if 'id' in attrs}
        for name in ['ip', 'area_id', 'device_id', 'brightness', 'smoothing', 'offset_ms',
                     'pair', 'save', 'start', 'stop', 'test', 'phase', 'title', 'position', 'swatches']:
            self.assertIn(name, by_id)
        labels = {attrs.get('for') for tag, attrs in markup.elements if tag == 'label'}
        for name in ['ip', 'area_id', 'device_id', 'brightness', 'smoothing', 'offset_ms']:
            self.assertIn(name, labels)
        self.assertEqual(by_id['smoothing'][1]['min'], '0.01')
        self.assertEqual(by_id['offset_ms'][1]['min'], '-5000')
        self.assertEqual(by_id['offset_ms'][1]['max'], '5000')
        for phrase in ['Responsiveness / Smoothing', '1 = no smoothing', '0.1 = slower',
                       'positive offset delays', 'physical button', 'other Hue Entertainment controllers',
                       'No TV capture app is required.', '4 seconds',
                       'no flashing', 'attempts to restore prior lighting afterward', 'Jellyfin Hue Sync',
                       'Restoration is best-effort', 'For trusted local networks only',
                       'Do not expose this interface to the Internet',
                       'Independent community project', 'not an official Jellyfin or Philips Hue product',
                       'Active means a playback item is present', 'Idle means no playback item',
                       'last 8 characters of the device ID', 'exact device ID, not its name']:
            self.assertIn(phrase, html)
        self.assertEqual(next(attrs['lang'] for tag, attrs in markup.elements if tag == 'html'), 'en')
        self.assertEqual(by_id['device_id'][1]['aria-describedby'], 'device-help')
        self.assertIn('device-help', by_id)
        self.assertTrue(any(attrs.get('role') == 'status' for _, attrs in markup.elements))
        self.assertNotIn('type="password"', html)
        self.assertNotIn('<iframe', html)
        self.assertNotIn('src="http', html)

    def run_ui(self, checks):
        if not shutil.which('node'):
            self.skipTest('Optional JavaScript runtime checks require Node; HTTP server needs only stdlib')
        html = self.request('GET', '/')[2].decode()
        match = re.search(r'<script nonce="([^"]+)">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match, 'Executable nonce-protected UI script missing')
        elements = {attrs['id']: attrs for _, attrs in Markup(html).elements if 'id' in attrs}
        payload = {'script': match.group(2), 'nonce': match.group(1),
                   'base': 'http://' + self.host, 'elements': elements, 'checks': checks}
        harness = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(attrs = {}) { this.attrs = attrs; this.value = attrs.value || ''; this.textContent = ''; this.children = []; this.style = {}; this.listeners = {}; this.disabled = 'disabled' in attrs; }
  set innerHTML(value) { throw Error('Untrusted innerHTML sink'); }
  get options() { return this.children; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.append(item); }
  replaceChildren(...items) { this.children = [...items]; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute(name, value) { this.attrs[name] = value; }
  reportValidity() { return true; }
}
const elements = Object.fromEntries(Object.entries(input.elements).map(([id, attrs]) => [id, new Element(attrs)]));
const timers = [], calls = [];
let failNetwork = false, readsOnly = false, heldRead = null;
const holdRead = () => {
  let release, entered;
  const block = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  heldRead = {block, entered};
  return {release, started};
};
const context = vm.createContext({console, AbortSignal, Number, Math,
 document: {getElementById: id => elements[id], activeElement: null,
  querySelector: selector => selector.includes('hue-csrf') ? {content: input.nonce} : null,
  createElement: () => new Element()},
 fetch: async (path, options = {}) => { if (failNetwork && (!readsOnly || !options.method || options.method === 'GET')) throw Error('fixture-offline'); calls.push({path, options}); const response = await fetch(input.base + path, options);
  if (path === '/api/status' && heldRead) { const gate = heldRead; heldRead = null; gate.entered(); await gate.block; }
  return response; },
 setTimeout: (callback, delay) => { timers.push({callback, delay}); return 1; }, clearTimeout: () => {}});
(async () => {
 vm.runInContext(input.script, context);
 for (let i = 0; i < 100 && elements.phase.textContent === ''; i++) await new Promise(resolve => setTimeout(resolve, 10));
 const fire = async (id, type = 'click') => { assert.equal(typeof elements[id].listeners[type], 'function', `Missing ${id}/${type} handler`); await elements[id].listeners[type]({preventDefault(){}}); };
 const refresh = () => vm.runInContext('refresh()', context);
 const offline = (value, onlyReads = false) => { failNetwork = value; readsOnly = onlyReads; };
 await eval(input.checks);
 console.log('UI runtime assertions passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(['node', '-e', harness], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('UI runtime assertions passed', result.stdout)

    def test_ui_duplicate_devices_show_state_and_suffix_without_name_selection(self):
        self.engine.data['devices'] = [
            {'id': 'old-device-0123ABcd', 'name': 'Bedroom TV', 'client': 'Jellyfin Android TV', 'active': False},
            {'id': 'new-device-9876ZyXw', 'name': 'Bedroom TV', 'client': 'Jellyfin Android TV', 'active': True},
            {'id': 'Ab-C', 'name': '<b>Device</b>', 'client': '', 'active': False},
        ]
        self.engine.data['config'].update(area_id='1', device_id='old-device-0123ABcd')
        self.run_ui(r"""
(async () => {
 assert.equal(elements.device_id.children[1].textContent, 'Bedroom TV · Jellyfin Android TV · Idle · ID …0123ABcd');
 assert.equal(elements.device_id.children[2].textContent, 'Bedroom TV · Jellyfin Android TV · Active · ID …9876ZyXw');
 assert.equal(elements.device_id.children[3].textContent, '<b>Device</b> · Idle · ID Ab-C');
 assert.equal(elements.device_id.children[1].value, 'old-device-0123ABcd');
 assert.equal(elements.device_id.children[2].value, 'new-device-9876ZyXw');
 assert.equal(elements.device_id.value, 'old-device-0123ABcd');
 elements.device_id.value = 'new-device-9876ZyXw';
 await fire('device_id', 'change');
 await refresh();
 assert.equal(elements.device_id.value, 'new-device-9876ZyXw');
 await fire('settings-form', 'submit');
 const write = calls.find(call => call.path === '/api/settings');
 assert.equal(JSON.parse(write.options.body).device_id, 'new-device-9876ZyXw');
 const data = vm.runInContext('lastStatus', context);
 data.devices = data.devices.filter(item => item.id === 'old-device-0123ABcd');
 vm.runInContext('renderStatus(lastStatus)', context);
 assert.equal(elements.device_id.value, 'new-device-9876ZyXw');
 assert.equal(elements.device_id.children.at(-1).textContent, 'Unavailable · new-device-9876ZyXw');
 assert.equal(elements.device_id.children.at(-1).value, 'new-device-9876ZyXw');
 data.config.device_id = 'Bedroom TV';
 vm.runInContext('renderStatus(lastStatus)', context);
 assert.equal(elements.device_id.value, 'Bedroom TV');
 assert.equal(elements.device_id.children.at(-1).textContent, 'Unavailable · Bedroom TV');
 data.config.device_id = '';
 vm.runInContext('renderStatus(lastStatus)', context);
 assert.equal(elements.device_id.value, '');
})()
""")
        self.assertEqual(self.engine.calls[0][1]['device_id'], 'new-device-9876ZyXw')

    def test_ui_status_copy_is_english_in_every_phase(self):
        self.run_ui(r"""
(async () => {
 const data = vm.runInContext('lastStatus', context);
 const phases = {unpaired:'Pair the bridge', disabled:'Stopped', waiting:'Waiting for playback',
   paused:'Playback paused · restoration is best-effort', preparing:'Analyzing media',
   streaming:'Synchronization active', testing:'Static test in progress', error:'Error',
   stale:'Stale session', unexpected:'Unknown state'};
 for (const [phase, label] of Object.entries(phases)) {
   data.phase = phase;
   vm.runInContext('renderStatus(lastStatus)', context);
   assert.equal(elements.phase.textContent, label);
 }
 assert.equal(elements.paired.textContent, 'Bridge not paired. Press its physical button to pair.');
 assert.equal(elements.streaming.textContent, 'Hue stream stopped');
 assert.equal(elements.cache.textContent, 'Idle');
 assert.equal(elements['smoothing-value'].textContent, '1.00');
 assert.equal(elements.swatches.children[0].attrs['aria-label'], 'Light 1 · RGB 255, 0, 128');
 data.paired = true; data.bridge_ip = '10.0.0.2'; data.streaming = true;
 for (const [phase, label] of Object.entries({idle:'Idle', extracting:'Analyzing', ready:'Ready', error:'Error', unexpected:'Unknown state'})) {
   data.cache = {phase, frames:30};
   vm.runInContext('renderStatus(lastStatus)', context);
   assert.equal(elements.cache.textContent, label + ' · 30 frames');
 }
 assert.equal(elements.paired.textContent, 'Bridge paired · 10.0.0.2');
 assert.equal(elements.streaming.textContent, 'Hue stream active');
 data.areas = [{id:'1', name:'Area', occupied:true}, {id:'2', name:'Other area', active:true}];
 data.config.area_id = 'missing-area';
 data.config.device_id = 'missing-device';
 data.playback.title = null;
 vm.runInContext('renderStatus(lastStatus)', context);
 assert.equal(elements.title.textContent, 'No playback');
 assert.equal(elements.area_id.children[0].textContent, 'Choose an area…');
 assert.equal(elements.device_id.children[0].textContent, 'Choose a device…');
 assert.equal(elements.area_id.children[1].textContent, 'Area · Occupied');
 assert.equal(elements.area_id.children[1].disabled, true);
 assert.equal(elements.area_id.children[2].textContent, 'Other area · Active');
 assert.equal(elements.area_id.children[3].textContent, 'Unavailable · missing-area');
 assert.equal(elements.device_id.children.at(-1).textContent, 'Unavailable · missing-device');
 offline(true);
 await refresh();
 assert.equal(elements.streaming.textContent, 'Stream state unknown');
 assert.equal(elements.message.textContent, 'Connection lost. Light state is unconfirmed; check the server.');
 offline(false);
 await refresh();
 assert.equal(elements.message.textContent, 'Connection restored.');
})()
""")

    def test_ui_uses_actual_engine_rgb8_without_dividing_wire_protocol_channels(self):
        self.run_ui(r"""
(async () => {
 const data = vm.runInContext('lastStatus', context);
 data.rgb = [[70,0,35]];
 vm.runInContext('renderStatus(lastStatus)', context);
 assert.equal(elements.swatches.children[0].style.backgroundColor, 'rgb(70, 0, 35)');
})().then(() => console.log('UI runtime assertions passed')).catch(error => { console.error(error); process.exit(1); });
""")

    def test_ui_renders_status_external_names_rgb8_and_one_second_poll(self):
        self.run_ui(r"""
(async () => {
 assert.equal(elements.phase.textContent, 'Pair the bridge');
 assert.equal(elements.title.textContent, 'Film de test');
 assert.equal(elements.position.textContent, '00:12');
 assert.equal(elements.area_id.children[1].textContent, '<b>Salon</b>');
 assert.ok(elements.device_id.children[1].textContent.includes('<img src=x>'));
 assert.equal(elements.swatches.children[0].style.backgroundColor, 'rgb(255, 0, 128)');
 assert.equal(elements.start.disabled, true);
 assert.ok(timers.some(timer => timer.delay === 1000));
 assert.ok(calls.every(call => !call.options.method || call.options.method === 'GET'));
})()
""")

    def test_ui_actions_use_csrf_post_and_verify_state_readback(self):
        self.run_ui(r"""
(async () => {
 elements.ip.value = '10.0.0.2';
 await fire('pair-form', 'submit');
 assert.ok(elements.paired.textContent.includes('10.0.0.2'));
 assert.equal(elements.message.textContent, 'Bridge paired.');
 elements.area_id.value = '1'; elements.device_id.value = 'device-1';
 elements.brightness.value = '0.6'; elements.smoothing.value = '0.1'; elements.offset_ms.value = '250';
 await fire('settings-form', 'submit');
 assert.equal(elements.smoothing.value, '0.1');
 assert.equal(elements.message.textContent, 'Settings saved.');
 assert.equal(elements.start.disabled, false);
 await fire('start');
 assert.equal(elements.start.disabled, true);
 await fire('test');
 assert.equal(elements.phase.textContent, 'Static test in progress');
 assert.equal(elements.message.textContent, 'Static test requested · 4 seconds. Restoration afterward is best-effort.');
 await fire('stop');
 assert.equal(elements.start.disabled, false);
 assert.equal(elements.message.textContent, 'Stop requested. Attempting to restore prior lighting.');
 const writes = calls.filter(call => call.options.method === 'POST');
 assert.deepEqual(writes.map(call => call.path), ['/api/pair', '/api/settings', '/api/start', '/api/test', '/api/stop']);
 for (const write of writes) {
   assert.equal(write.options.headers['X-Hue-CSRF'], input.nonce);
   assert.equal(write.options.headers['Content-Type'], 'application/json');
   assert.equal(write.options.redirect, 'error');
 }
 assert.deepEqual(JSON.parse(writes[1].options.body), {area_id:'1', device_id:'device-1', brightness:0.6, smoothing:0.1, offset_ms:250});
 assert.ok(calls.filter(call => call.path === '/api/status').length >= 6);
})()
""")
        self.assertEqual(self.engine.calls, [('pair', '10.0.0.2'),
            ('configure', {'area_id': '1', 'device_id': 'device-1', 'brightness': 0.6,
                           'smoothing': 0.1, 'offset_ms': 250}),
            ('configure', {'enabled': True}), ('test_lights',), ('configure', {'enabled': False})])
        readback = json.loads(self.request('GET', '/api/status')[2])
        self.assertFalse(readback['config']['enabled'])
        self.assertTrue(readback['paired'])

    def test_ui_preserves_unsaved_edits_and_failed_save(self):
        self.engine.data['paired'] = True
        self.engine.data['config'].update(area_id='1', device_id='device-1')
        self.run_ui(r"""
(async () => {
 elements.ip.value = '192.168.1.50'; await fire('ip', 'input');
 elements.brightness.value = '0.4'; await fire('brightness', 'input');
 elements.offset_ms.value = '-250'; await fire('offset_ms', 'input');
 await refresh();
 assert.equal(elements.ip.value, '192.168.1.50');
 assert.equal(elements.brightness.value, '0.4');
 assert.equal(elements['brightness-value'].textContent, '40 %');
 assert.equal(elements.offset_ms.value, '-250');
 const before = calls.filter(call => call.options.method === 'POST').length;
 await fire('start');
 assert.equal(calls.filter(call => call.options.method === 'POST').length, before);
 assert.ok(elements.message.textContent.includes('Save settings'));
 elements.brightness.value = '1.5'; await fire('brightness', 'input');
 await fire('settings-form', 'submit');
 assert.equal(elements.brightness.value, '1.5');
 assert.ok(elements.message.textContent.includes('Unable to complete the operation'));
 elements.brightness.value = '0.4'; await fire('brightness', 'input');
 await fire('settings-form', 'submit');
 await refresh();
 assert.equal(elements.brightness.value, '0.4');
 assert.equal(elements.offset_ms.value, '-250');
 assert.ok(elements.start.disabled === false);
})()
""")
        self.assertEqual(self.engine.calls, [('configure', {'area_id': '1',
            'device_id': 'device-1', 'brightness': 0.4, 'smoothing': 1, 'offset_ms': -250})])

    def test_ui_disconnect_removes_stale_colors_and_requires_readback(self):
        self.engine.data.update(paired=True, streaming=True, phase='streaming')
        self.engine.data['config'].update(area_id='1', device_id='device-1', enabled=True)
        self.run_ui(r"""
(async () => {
 assert.equal(elements.swatches.children.length, 1);
 offline(true);
 await refresh();
 assert.equal(elements.phase.textContent, 'Server unreachable');
 assert.equal(elements.swatches.children.length, 0);
 assert.equal(elements.test.disabled, true);
 assert.equal(elements.start.disabled, true);
 assert.equal(elements.stop.disabled, false);
 offline(false);
 await refresh();
 assert.equal(elements.phase.textContent, 'Synchronization active');
 assert.equal(elements.test.disabled, false);
 offline(true, true);
 await fire('stop');
 assert.equal(elements.phase.textContent, 'Server unreachable');
 assert.ok(!elements.message.textContent.includes('Stop requested.'));
 offline(false);
 await refresh();
 assert.equal(elements.start.disabled, false);
})()
""")
        self.assertEqual(self.engine.calls, [('configure', {'enabled': False})])

    def test_ui_displays_sanitized_engine_error_and_clears_it_on_recovery(self):
        self.engine.data.update(phase='error', error='fixture-secret C:/private/cache')
        self.engine.data['cache'] = {'phase': 'error', 'mode': 'vaapi', 'frames': 0,
                                    'error': 'fixture-secret C:/private/cache'}
        self.run_ui(r"""
(async () => {
 assert.equal(elements.phase.textContent, 'Error');
 assert.ok(elements.message.textContent.includes('Unable to complete the operation'));
 assert.ok(!elements.message.textContent.includes('fixture-secret'));
 const data = vm.runInContext('lastStatus', context);
 data.error = null; data.cache.error = null; data.phase = 'paused'; data.rgb = null;
 data.playback.position = null; data.playback.title = null;
 vm.runInContext('renderStatus(lastStatus)', context);
 assert.equal(elements.message.textContent, '');
 assert.equal(elements.phase.textContent, 'Playback paused · restoration is best-effort');
 assert.equal(elements.position.textContent, '—:—');
 assert.equal(elements.title.textContent, 'No playback');
 assert.equal(elements.swatches.children.length, 0);
})()
""")

    def test_oversized_headers_and_normalized_request_targets_are_rejected(self):
        status, _, body = self.raw(f'GET / HTTP/1.1\r\nHost: {self.host}\r\nX-Padding: ' + 'a' * 8193 + '\r\n\r\n')
        self.assertEqual(status, 431)
        self.assertNotIn(b'aaaa', body)
        for path in ['//', '///', '//api/status', '/api/status#fragment']:
            with self.subTest(path=path):
                self.assertEqual(self.raw(f'GET {path} HTTP/1.1\r\nHost: {self.host}\r\n\r\n')[0], 404)
        self.assertEqual(self.engine.calls, [])

    def test_ui_action_readback_cannot_reuse_poll_started_before_write(self):
        self.engine.data.update(paired=True, phase='disabled')
        self.engine.data['config'].update(area_id='1', device_id='device-1')
        self.run_ui(r"""
(async () => {
 const gate = holdRead();
 const priorPoll = refresh();
 await gate.started;
 const starting = fire('start');
 await new Promise(resolve => setTimeout(resolve, 30));
 gate.release();
 await priorPoll;
 await starting;
 assert.equal(elements.start.disabled, true);
 assert.equal(elements.message.textContent, 'Synchronization enabled.');
 assert.ok(calls.filter(call => call.path === '/api/status').length >= 3);
})()
""")
        self.assertEqual(self.engine.calls, [('configure', {'enabled': True})])

    def test_pair_updates_bridge_and_status(self):
        status, _, body = self.post('/api/pair', {'ip': '10.0.0.2'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True})
        self.assertEqual(self.engine.calls, [('pair', '10.0.0.2')])
        readback = json.loads(self.request('GET', '/api/status')[2])
        self.assertTrue(readback['paired'])
        self.assertEqual(readback['bridge_ip'], '10.0.0.2')

    def test_status_returns_engine_object_without_state_changes(self):
        status, headers, body = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        self.assertIn('application/json', headers['Content-Type'])
        self.assertEqual(json.loads(body), self.engine.data)
        self.assertEqual(self.engine.calls, [])

    def test_root_serves_english_html_with_random_per_server_nonce(self):
        self.assertIsInstance(self.server, ThreadingHTTPServer)
        status, headers, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn(b'lang="en"', body)
        self.assertEqual(self.nonce(), self.nonce())
        other = make_server(FixtureEngine(), '127.0.0.1', 0, ['127.0.0.1'])
        try:
            self.assertNotEqual(self.server.csrf_nonce, other.csrf_nonce)
        finally:
            other.server_close()


if __name__ == '__main__':
    unittest.main()
