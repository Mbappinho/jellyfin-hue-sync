import unittest, tempfile, json, threading
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from ambilight.jellyfin import choose_session, JellyfinClient

class SelectionTests(unittest.TestCase):
    def test_exact_target_selects_fresh_playback_not_other_devices_or_stale_reports(self):
        now=datetime(2026,1,1,12,0,tzinfo=timezone.utc).timestamp()
        reports=[{'DeviceId':'other','LastPlaybackCheckIn':'2026-01-01T12:00:00Z','NowPlayingItem':{'Id':'wrong'}},
                 {'DeviceId':'target','LastPlaybackCheckIn':'2026-01-01T11:00:00Z','NowPlayingItem':{'Id':'stale'}},
                 {'DeviceId':'target','LastPlaybackCheckIn':'2026-01-01T11:59:55Z','NowPlayingItem':{'Id':'correct'}}]
        self.assertEqual(choose_session(reports,'target',now),reports[-1])
        self.assertIsNone(choose_session(reports[:2],'target',now))

class ClientTests(unittest.TestCase):
    def test_absolute_deadline_interrupts_slow_drip_headers_and_body(self):
        import time
        for drip_headers in (False, True):
            with self.subTest(drip_headers=drip_headers):
                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        payload = (b'HTTP/1.0 200 OK\r\nX-Drip: ' + b'x' * 30 if drip_headers
                                   else b'["' + b'x' * 30 + b'"]')
                        if not drip_headers:
                            self.send_response(200);self.end_headers()
                        try:
                            for byte in payload:
                                self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.03)
                        except OSError:
                            pass
                    def log_message(self,*args): pass
                server=HTTPServer(('127.0.0.1',0),Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        path=Path(tmp)/'settings.json';path.write_text(json.dumps({'jellyfin':{'apiKey':'UNIT_FIXTURE'}}))
                        client=JellyfinClient(path,'http://127.0.0.1:'+str(server.server_port))
                        client.request_timeout=.15
                        started=time.monotonic()
                        with self.assertRaisesRegex(RuntimeError,'Jellyfin access unavailable'):
                            client.sessions()
                        self.assertLess(time.monotonic()-started,.6)
                finally:
                    server.shutdown();server.server_close();thread.join(2)

    def test_standalone_private_key_file_authenticates_without_settings(self):
        from unittest.mock import patch, MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            key=Path(tmp)/'api-key';key.write_text('UNIT_TEST_ONLY_NOT_REAL\n');key.chmod(0o600)
            response=MagicMock();response.__enter__.return_value.read.return_value=b'[]'
            opener=MagicMock();opener.open.return_value=response
            with patch('urllib.request.build_opener',return_value=opener):
                client=JellyfinClient(api_key_file=key)
                self.assertEqual(client.sessions(),[])
                self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'),'MediaBrowser Token="UNIT_TEST_ONLY_NOT_REAL"')
            key.write_text('SECRET\nINJECTION')
            with self.assertRaisesRegex(RuntimeError,'Jellyfin access unavailable'):
                client.sessions()

    def test_authentication_is_in_header_never_url_and_response_is_read_only(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path,self.headers.get('Authorization')))
                content=b'[{"DeviceName":"Explicit unit fixture"}]'
                self.send_response(200);self.end_headers();self.wfile.write(content)
            def log_message(self,*args): pass
        server=HTTPServer(('127.0.0.1',0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'settings.json';path.write_text(json.dumps({'jellyfin':{'apiKey':'UNIT_TEST_ONLY_NOT_REAL'}}))
                client=JellyfinClient(path,'http://127.0.0.1:'+str(server.server_port))
                self.assertEqual(client.sessions(),[{'DeviceName':'Explicit unit fixture'}])
                self.assertEqual(seen,[('/Sessions','MediaBrowser Token="UNIT_TEST_ONLY_NOT_REAL"')])
        finally:
            server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__': unittest.main()
